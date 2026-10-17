#!/usr/bin/env python3
"""Export the stock ImageNet ResNet-18 to ONNX for the zDLC sanity path (plan 8.3 step 1).

Static shape [3,3,224,224] (one claim photo = three 224 crops), opset 17. This is the
compile-path check only; it is NOT the claim-photo detector. A's trained model gets
exported with the same shape and opset by A's own export script.

Run on the LinuxONE VM inside the event's Jupyter image (it has torch for s390x), or on
any machine with torch installed:

    python3 zdlc/export_stock_onnx.py            # -> zdlc/build/resnet18-stock.onnx
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location("aegis_stock_benchmark", ROOT / "linuxone" / "benchmark_resnet18.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "zdlc" / "build" / "resnet18-stock.onnx")
    parser.add_argument("--batch", type=int, default=3, help="3 for the decision path, 16 for the heatmap variant")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache" / "linuxone")
    args = parser.parse_args()
    try:
        import torch
    except ImportError:
        print(json.dumps({"status": "failed", "error": "torch is not installed; run inside the event image"}))
        return 2
    bench = load_benchmark_module()
    model, provenance = bench.load_stock_model(torch, args.cache_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    example = torch.zeros(args.batch, 3, 224, 224, dtype=torch.float32)
    with torch.no_grad():
        torch.onnx.export(model, example, str(args.output), opset_version=17, do_constant_folding=True,
                          input_names=["crops"], output_names=["logits"], dynamic_axes=None)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    report = {"status": "exported", "path": str(args.output), "sha256": digest, "opset": 17,
              "input_shape": [args.batch, 3, 224, 224], "model": "stock torchvision ResNet-18 (ImageNet), not the claim detector",
              "weights_sha256": provenance["weights_sha256"]}
    args.output.with_suffix(".provenance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
