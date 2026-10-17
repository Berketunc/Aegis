from io import BytesIO
import threading
import time

import numpy as np
from PIL import Image
import pytest

from risk_service.heatmap import HeatmapQueue, QueueFull
from risk_service.precompute import heatmap_crops


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (800, 600), (70, 100, 190)).save(output, format="PNG")
    return output.getvalue()


def test_grid_covers_resized_image_without_shrinking_to_224():
    crops, boxes, size = heatmap_crops(image_bytes())
    assert crops.shape == (16, 3, 224, 224)
    assert crops.dtype == np.uint8
    assert size == [800, 600]  # short side 600 < 640: native pixels kept, never upscaled
    assert boxes[0] == [0, 0, 224, 224]
    assert boxes[-1] == [576, 376, 800, 600]
    assert all(right - left == bottom - top == 224 for left, top, right, bottom in boxes)


class StubIndex:
    def get(self, evidence_id):
        return {"evidence_id": evidence_id}


class StubStore:
    def load_heatmap_crops(self, record):
        return heatmap_crops(image_bytes())


class TestOnlyDetector:
    __test__ = False
    version = "explicit-test-double"
    def __init__(self, event=None):
        self.event = event

    def score_grid(self, crops):
        assert crops.shape == (16, 3, 224, 224)
        if self.event is not None:
            self.event.wait(timeout=2)
        return [index / 15 for index in range(16)]


def wait_done(queue, job_id):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = queue.get(job_id)
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("Heatmap test worker did not complete")


def test_async_heatmap_result_is_row_major_and_cache_is_bounded():
    queue = HeatmapQueue(StubStore(), StubIndex(), TestOnlyDetector(), capacity=1)
    try:
        first = queue.submit("CLM-one", "EV-one")
        assert first["job_id"].startswith("HM-")
        complete = wait_done(queue, first["job_id"])
        assert complete["status"] == "succeeded"
        assert complete["result"]["patch_scores"][0][0] == 0
        assert complete["result"]["patch_scores"][3][3] == 1
        assert queue.submit("CLM-one", "EV-one")["job_id"] == first["job_id"]
        second = queue.submit("CLM-two", "EV-two")
        assert queue.get(first["job_id"]) is None
        assert wait_done(queue, second["job_id"])["status"] == "succeeded"
        assert len(queue.jobs) == 1
    finally:
        queue.close()


def test_active_queue_cannot_grow_past_capacity():
    event = threading.Event()
    queue = HeatmapQueue(StubStore(), StubIndex(), TestOnlyDetector(event), capacity=1)
    try:
        queue.submit("CLM-one", "EV-one")
        with pytest.raises(QueueFull):
            queue.submit("CLM-two", "EV-two")
    finally:
        event.set()
        queue.close()
