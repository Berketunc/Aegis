"""Bounded, in-memory async jobs for an actual trained detector's patch scores."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import copy
import math
import threading
import time
import uuid


class QueueFull(RuntimeError):
    pass


class HeatmapQueue:
    def __init__(self, store, index, detector, capacity=32):
        self.store, self.index, self.detector = store, index, detector
        self.capacity = capacity
        self.jobs = OrderedDict()
        self.lock = threading.Lock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aegis-heatmap")

    def submit(self, claim_id, evidence_id):
        with self.lock:
            for job in self.jobs.values():
                if job["claim_id"] == claim_id and job["evidence_id"] == evidence_id and job["status"] != "failed":
                    return self._summary(job)
            while len(self.jobs) >= self.capacity:
                completed = next((key for key, job in self.jobs.items() if job["status"] in {"succeeded", "failed"}), None)
                if completed is None:
                    raise QueueFull("Heatmap queue is full")
                self.jobs.pop(completed)
            job_id = "HM-" + uuid.uuid4().hex[:24]
            self.jobs[job_id] = {"job_id": job_id, "claim_id": claim_id, "evidence_id": evidence_id,
                                 "status": "queued", "result": None, "error": None}
            self.worker.submit(self._run, job_id)
            return self._summary(self.jobs[job_id])

    @staticmethod
    def _summary(job):
        return {key: job[key] for key in ("job_id", "claim_id", "evidence_id", "status")}

    def _run(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            job["status"] = "running"
        started = time.perf_counter()
        try:
            record = self.index.get(job["evidence_id"])
            crops, boxes, size = self.store.load_heatmap_crops(record)
            values = self.detector.score_grid(crops)
            if len(values) != 16 or any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
                raise ValueError("Detector returned invalid patch signals")
            result = {"grid_shape": [4, 4], "patch_scores": [values[index:index + 4] for index in range(0, 16, 4)],
                      "crop_boxes": boxes, "resized_size": size, "model_version": self.detector.version,
                      "score_scope": "advisory_patch_signal_not_validated_edit_localisation",
                      "timings_ms": {"total": round((time.perf_counter() - started) * 1000, 3)}}
            with self.lock:
                job.update(status="succeeded", result=result)
        except Exception:
            with self.lock:
                job.update(status="failed", error={"code": "HEATMAP_UNAVAILABLE"})

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def close(self):
        self.worker.shutdown(wait=True, cancel_futures=True)
