#!/usr/bin/env python3
"""Score A's parity crops with the deployed detector on LinuxONE (plan 7.7 parity check).

Runs inside the aegis-risk container, where torch, the model manifest and the
parity sets copied by install_model.py live. Writes the raw per-crop logits as
float32 [N,3] in the same order as the .npz, which A feeds to
train/fit_thresholds_linuxone.py to fit thresholds t-1 on LinuxONE scores.

    sudo docker exec -i aegis-risk python3 - /models/parity_val.npz /tmp/val.npy < linuxone/parity_logits.py
    sudo docker cp aegis-risk:/tmp/val.npy eval/parity/linuxone_val_logits_$(device).npy
    (repeat for parity_test.npz)

Prints the drift between Modal FP32 logits stored in the .npz and the LinuxONE
logits; no crops or scores are printed.
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, "/app")
from risk_service.detector import Detector  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: parity_logits.py <parity.npz> <output.npy>", file=sys.stderr)
        return 2
    source, output = sys.argv[1], sys.argv[2]
    detector = Detector(os.environ.get("AEGIS_MODEL_MANIFEST"), device=os.environ.get("AEGIS_MODEL_DEVICE", "cpu"),
                        threads=int(os.environ.get("AEGIS_TORCH_THREADS", "1")))
    if not detector.ready:
        print(json.dumps({"status": "failed", "error": detector.error}), file=sys.stderr)
        return 1
    data = np.load(source, allow_pickle=False)
    crops = data["crops"]  # [N,3,224,224,3] uint8 HWC RGB, from A's three_crops
    if crops.ndim != 5 or crops.shape[1:] != (3, 224, 224, 3) or crops.dtype != np.uint8:
        print(json.dumps({"status": "failed", "error": "unexpected parity crop layout"}), file=sys.stderr)
        return 1
    logits = np.empty((crops.shape[0], 3), dtype=np.float32)
    with detector.lock, detector.torch.inference_mode():
        for index in range(crops.shape[0]):
            sample = np.ascontiguousarray(crops[index].transpose(0, 3, 1, 2))
            logits[index] = detector._logits(sample).numpy().astype(np.float32)
    np.save(output, logits)
    report = {"status": "scored", "n": int(crops.shape[0]), "device": detector.device,
              "model_version": detector.version, "output": output}
    if "logits_torch" in data:
        reference = data["logits_torch"].astype(np.float64).reshape(logits.shape)
        temperature = float(data["temperature"]) if "temperature" in data else detector.temperature
        score = lambda z: 1 / (1 + np.exp(-z.mean(1) / temperature))  # noqa: E731
        report.update({"max_abs_logit_diff_vs_modal_fp32": float(np.abs(reference - logits).max()),
                       "mean_abs_logit_diff_vs_modal_fp32": float(np.abs(reference - logits).mean()),
                       "max_abs_score_diff_vs_modal_fp32": float(np.abs(score(reference) - score(logits.astype(np.float64))).max())})
        if "labels" in data:
            labels = data["labels"].astype(int)
            reals = score(logits.astype(np.float64))[labels == 0]
            report["real_fpr_at_installed_thresholds"] = None
            thresholds_path = os.environ.get("AEGIS_THRESHOLDS_PATH")
            if thresholds_path and os.path.isfile(thresholds_path):
                thresholds = json.load(open(thresholds_path))
                report["real_fpr_at_installed_thresholds"] = {
                    "version": thresholds.get("version", thresholds.get("thresholds_version")),
                    "tau_red": float((reals >= thresholds["tau_red"]).mean()),
                    "tau_amber": float((reals >= thresholds["tau_amber"]).mean())}
    for key, value in list(report.items()):
        if isinstance(value, float) and not math.isfinite(value):
            report[key] = None
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
