"""A's trained binary detector only: explicit artifact + inference manifest.

No ImageNet weights, random weights or fabricated risk scores are substituted.
Torch is imported lazily so evidence-only development works without it.
"""
import hashlib
import importlib
import json
import math
from pathlib import Path
import threading

import numpy as np

from .precompute import PREPROCESS_VERSION


class DetectorUnavailable(RuntimeError):
    pass


class Detector:
    def __init__(self, manifest_path: str | None, device="cpu", threads=1):
        self.ready = False
        self.error = "model_not_configured"
        self.version = None
        self.device = device
        self.artifact_sha256 = None
        self.batch16_validated = False
        self.lock = threading.Lock()
        self.threads = threads
        if not manifest_path:
            return
        try:
            self._load(Path(manifest_path))
            self.ready = True
            self.error = None
        except Exception:
            # Never expose file paths, weights or arbitrary exception strings via health.
            self.error = "model_load_or_warmup_failed"

    def _load(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        import torch
        self.torch = torch
        if manifest["preprocessing_version"] != PREPROCESS_VERSION:
            raise ValueError("Preprocessing does not match uploaded crops")
        self.version = manifest["model_version"]
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("A versioned trained model is required")
        self.output_kind = manifest["output_kind"]
        if self.output_kind not in {"binary_logit", "two_class_logits"}:
            raise ValueError("Model must return uncalibrated logits")
        self.temperature = float(manifest["temperature"])
        self.bias = float(manifest.get("calibration_bias", 0.0))
        if self.temperature <= 0 or not math.isfinite(self.temperature) or not math.isfinite(self.bias):
            raise ValueError("Invalid calibration")
        mean, std = manifest["mean"], manifest["std"]
        if len(mean) != 3 or len(std) != 3 or not all(math.isfinite(float(x)) for x in mean + std) or min(std) <= 0:
            raise ValueError("Invalid RGB normalisation")
        self.mean = torch.tensor(mean, dtype=torch.float32).reshape(1, 3, 1, 1)
        self.std = torch.tensor(std, dtype=torch.float32).reshape(1, 3, 1, 1)
        torch.set_num_threads(self.threads)
        if self.device not in {"cpu", "nnpa"}:
            raise ValueError("Only explicit cpu/nnpa devices are supported")
        if self.device == "nnpa":
            import torch_nnpa  # noqa: F401; registers the device backend
        artifact = (manifest_path.parent / manifest["artifact"]).resolve()
        self.artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if manifest.get("sha256") and manifest["sha256"] != self.artifact_sha256:
            raise ValueError("Artifact SHA-256 mismatch")
        if manifest["format"] == "torchscript":
            self.model = torch.jit.load(str(artifact), map_location="cpu")
        elif manifest["format"] == "state_dict":
            # This factory is trusted deployment configuration supplied by A.
            module, factory = manifest["factory"].split(":", 1)
            self.model = getattr(importlib.import_module(module), factory)(**manifest.get("factory_kwargs", {}))
            self.model.load_state_dict(torch.load(str(artifact), map_location="cpu", weights_only=True), strict=True)
        else:
            raise ValueError("Supported formats are torchscript and state_dict")
        self.model.eval()
        if self.device == "nnpa":
            self.model.nnpa()
        # Exercise exactly the production shape once, including calibration.
        with torch.inference_mode():
            self._predict(np.zeros((3, 3, 224, 224), dtype=np.uint8))

    def _logits(self, crops):
        torch = self.torch
        if crops.ndim != 4 or tuple(crops.shape[1:]) != (3, 224, 224) or crops.dtype != np.uint8:
            raise ValueError("Expected uint8 NCHW RGB crops")
        count = crops.shape[0]
        batch = torch.from_numpy(np.ascontiguousarray(crops)).float().div_(255)
        batch = (batch - self.mean) / self.std
        if self.device == "nnpa":
            batch = batch.to("nnpa")
        output = self.model(batch)
        if not isinstance(output, torch.Tensor):
            raise ValueError("Model must return a tensor of logits")
        output = output.cpu().float()
        if self.output_kind == "binary_logit":
            if tuple(output.shape) not in {(count,), (count, 1)}:
                raise ValueError("Expected one fake-vs-real logit per crop")
            crop_logits = output.reshape(count)
        else:
            if tuple(output.shape) != (count, 2):
                raise ValueError("Expected [real, fake] logits per crop")
            crop_logits = output[:, 1] - output[:, 0]
        if not bool(torch.isfinite(crop_logits).all()):
            raise ValueError("Model returned non-finite logits")
        return crop_logits

    def _predict(self, crops):
        torch = self.torch
        crop_logits = self._logits(crops)
        mean_logit = float(crop_logits.mean())
        score = float(torch.sigmoid(torch.tensor(mean_logit / self.temperature + self.bias)))
        return {"ai_score": score, "mean_logit": mean_logit}

    def score(self, crops):
        if not self.ready:
            raise DetectorUnavailable(self.error)
        with self.lock, self.torch.inference_mode():
            return self._predict(crops)

    def score_grid(self, crops):
        """16 patch signals; localisation is advisory, not a calibrated edit mask."""
        if not self.ready:
            raise DetectorUnavailable(self.error)
        if crops.shape[0] != 16:
            raise ValueError("Heatmap requires a batch of sixteen patches")
        with self.lock, self.torch.inference_mode():
            logits = self._logits(crops)
            scores = self.torch.sigmoid(logits / self.temperature + self.bias).tolist()
            self.batch16_validated = True
            return scores
