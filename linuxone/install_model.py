#!/usr/bin/env python3
"""Install A's trained detector and thresholds into the private runtime (no torch needed).

Takes the files `modal run train/export_onnx.py --run <run>` leaves in models/<run>/
(train/HANDOFF.md) and writes what the risk service and gateway read:

    .runtime/models/detector.pth    strict torchvision-style ResNet-18 state dict (A's best.pt)
    .runtime/models/manifest.json   model_version, preprocessing version, calibration, factory
    .runtime/models/thresholds.json copy of eval/thresholds_<version>.json (shared by both services)
    .runtime/models/parity_*.npz    copied if present, for linuxone/parity_logits.py

Usage (on the VM, from the repository directory, with A's files copied alongside):

    python3 linuxone/install_model.py --run det-r18-v4 --weights models/det-r18-v4/best.pt \
        --temperature models/det-r18-v4/temperature.json --export models/det-r18-v4/export.json \
        --thresholds eval/thresholds_t-0.json
    bash linuxone/start_services.sh --with-gateway      # restart so the model loads once at startup

Refuses anything that does not match this repository's preprocessing (A's v4 recipe:
short side fitted to 640 without upscaling, three 224 crops). ImageNet weights are
never substituted for the trained detector.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from risk_service.config import read_thresholds  # noqa: E402
from risk_service.precompute import PREPROCESS_VERSION  # noqa: E402

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]
FACTORY = "risk_service.resnet18:build_detector"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(source: Path, destination: Path) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".install-")
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_write_json(destination: Path, payload: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".install-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="A's run name, becomes model_version, e.g. det-r18-v4")
    parser.add_argument("--weights", required=True, type=Path, help="A's best.pt (strict state dict)")
    parser.add_argument("--temperature", required=True, type=Path, help="A's temperature.json")
    parser.add_argument("--thresholds", required=True, type=Path, help="eval/thresholds_<version>.json")
    parser.add_argument("--export", type=Path, help="A's export.json; checked for the no-upscale preprocessing")
    parser.add_argument("--runtime-dir", type=Path, default=ROOT / ".runtime")
    args = parser.parse_args()
    try:
        temperature = float(json.loads(args.temperature.read_text())["temperature"])
        if not math.isfinite(temperature) or temperature <= 0:
            raise ValueError("temperature must be a positive finite number")
        if args.export is not None:
            export = json.loads(args.export.read_text())
            preprocess = export.get("preprocess", {})
            if preprocess.get("no_upscale") is not True or preprocess.get("short_side") != 640 or preprocess.get("crop") != 224:
                raise ValueError(f"export.json preprocessing {preprocess} does not match this service's {PREPROCESS_VERSION}; "
                                 "only no-upscale runs (det-r18-v4 and later) can be installed")
            if export.get("run") not in (None, args.run):
                raise ValueError(f"export.json is for run {export.get('run')!r}, not {args.run!r}")
            if abs(float(export.get("temperature", temperature)) - temperature) > 1e-9:
                raise ValueError("temperature.json and export.json disagree")
        thresholds = read_thresholds(str(args.thresholds))
        raw_thresholds = json.loads(args.thresholds.read_text())
        if raw_thresholds.get("model_version") not in (None, args.run):
            raise ValueError(f"thresholds were fitted for {raw_thresholds.get('model_version')!r}, not {args.run!r}")
        if not args.weights.is_file():
            raise ValueError(f"weights not found: {args.weights}")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "refused", "error": str(error)}), file=sys.stderr)
        return 2

    models_dir = args.runtime_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_copy(args.weights, models_dir / "detector.pth")
    digest = sha256_file(models_dir / "detector.pth")
    manifest = {
        "model_version": args.run,
        "preprocessing_version": PREPROCESS_VERSION,
        "format": "state_dict",
        "artifact": "detector.pth",
        "sha256": digest,
        "factory": FACTORY,
        "factory_kwargs": {"num_outputs": 1},
        "output_kind": "binary_logit",
        "mean": IMAGENET_MEAN,
        "std": IMAGENET_STD,
        "temperature": temperature,
        "calibration_bias": 0.0,
        "score": "sigmoid(mean(3 crop logits) / temperature)",
        "source": {"weights": args.weights.name, "temperature": args.temperature.name,
                   "export": args.export.name if args.export else None, "thresholds": args.thresholds.name},
    }
    atomic_write_json(models_dir / "manifest.json", manifest)
    atomic_copy(args.thresholds, models_dir / "thresholds.json")
    copied = []
    for name in ("parity_val.npz", "parity_test.npz"):
        candidate = args.weights.parent / name
        if candidate.is_file():
            atomic_copy(candidate, models_dir / name)
            copied.append(name)
    print(json.dumps({"status": "installed", "model_version": args.run, "weights_sha256": digest,
                      "temperature": temperature, "thresholds": thresholds, "parity_sets": copied,
                      "preprocessing_version": PREPROCESS_VERSION, "models_dir": str(models_dir),
                      "next": "bash linuxone/start_services.sh --with-gateway && bash linuxone/service_status.sh"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
