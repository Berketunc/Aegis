#!/usr/bin/env python3
"""Benchmark stock ImageNet ResNet-18 on LinuxONE, without torchvision.

This tests the serving platform with three synthetic crops. It is neither the
trained claim-photo detector nor a zDLC compilation test. The upstream model,
its BSD license, and weights are cached persistently; no packages are installed.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time
import urllib.request


SOURCE_URL = "https://raw.githubusercontent.com/pytorch/vision/v0.10.0/torchvision/models/resnet.py"
LICENSE_URL = "https://raw.githubusercontent.com/pytorch/vision/v0.10.0/LICENSE"
WEIGHTS_URL = "https://download.pytorch.org/models/resnet18-f37072fd.pth"
WEIGHTS_SHA256_PREFIX = "f37072fd"
OLD_IMPORT = "from .utils import load_state_dict_from_url"
NEW_IMPORT = "from torch.hub import load_state_dict_from_url"
INPUT_SHAPE = (3, 3, 224, 224)


def positive_int(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return result


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    project_dir = Path(__file__).resolve().parent.parent
    parser.add_argument("--runs", type=positive_int, default=100)
    parser.add_argument("--warmup", type=positive_int, default=10)
    parser.add_argument("--threads", type=positive_int, default=3)
    parser.add_argument("--interop-threads", type=positive_int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cpu-only", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=project_dir / ".cache" / "linuxone")
    parser.add_argument("--output", type=Path, default=project_dir / "eval" / "stock_resnet18.json")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_once(url: str, destination: Path) -> None:
    if destination.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            request = urllib.request.Request(url, headers={"User-Agent": "AEGIS-LinuxONE-benchmark/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                shutil.copyfileobj(response, temporary)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_stock_model(torch, cache_dir: Path):
    source_dir = cache_dir / "torchvision-v0.10.0"
    original_path = source_dir / "resnet.py"
    license_path = source_dir / "LICENSE"
    fetch_once(SOURCE_URL, original_path)
    fetch_once(LICENSE_URL, license_path)
    original = original_path.read_text(encoding="utf-8")
    if original.count(OLD_IMPORT) != 1:
        raise RuntimeError(f"Expected exactly one upstream relative import in {original_path}")
    adapted_path = source_dir / "resnet_standalone.py"
    adapted_path.write_text(original.replace(OLD_IMPORT, NEW_IMPORT), encoding="utf-8")
    (source_dir / "ATTRIBUTION.md").write_text(
        "# Upstream ResNet implementation\n\n"
        "Source: PyTorch torchvision v0.10.0, distributed under BSD 3-Clause.\n"
        f"Source URL: {SOURCE_URL}\nLicense URL: {LICENSE_URL}\n"
        "The unmodified license is saved in LICENSE beside both source files.\n"
        f"Only adaptation: `{OLD_IMPORT}` → `{NEW_IMPORT}`.\n"
        f"Original source SHA-256: {sha256_file(original_path)}\n"
        f"Weights: {WEIGHTS_URL}\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("aegis_upstream_resnet", adapted_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the standalone upstream model")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    weights_dir = cache_dir / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)
    weights_path = weights_dir / WEIGHTS_URL.rsplit("/", 1)[-1]
    # torch.hub verifies downloads, but does not recheck an existing cached file.
    if weights_path.exists() and not sha256_file(weights_path).startswith(WEIGHTS_SHA256_PREFIX):
        raise RuntimeError(f"Cached weights failed the official filename hash check: {weights_path}")
    state = torch.hub.load_state_dict_from_url(
        WEIGHTS_URL,
        model_dir=str(weights_dir),
        map_location="cpu",
        check_hash=True,
        weights_only=True,
        progress=True,
    )
    weights_hash = sha256_file(weights_path)
    if not weights_hash.startswith(WEIGHTS_SHA256_PREFIX):
        raise RuntimeError("Downloaded weights failed the official filename hash check")
    model = module.resnet18(pretrained=False)
    model.load_state_dict(state, strict=True)
    model.eval()
    provenance = {
        "source_url": SOURCE_URL,
        "source_sha256": sha256_file(original_path),
        "license_url": LICENSE_URL,
        "license_file": str(license_path),
        "weights_url": WEIGHTS_URL,
        "weights_sha256": weights_hash,
        "verified_hash_prefix": WEIGHTS_SHA256_PREFIX,
        "weights_only": True,
        "strict_state_dict": True,
    }
    return model, provenance


def percentile(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def benchmark(torch, model, inputs, device: str, warmup: int, runs: int):
    samples = []
    output = None
    output_device = None
    with torch.inference_mode():
        for _ in range(warmup):
            output = model(inputs.to(device)).to("cpu")
        for _ in range(runs):
            started = time.perf_counter()
            device_inputs = inputs.to(device)
            device_output = model(device_inputs)
            output = device_output.to("cpu")
            samples.append((time.perf_counter() - started) * 1000)
            output_device = str(device_output.device)
    return {
        "status": "completed",
        "requested_device": device,
        "model_parameter_device": str(next(model.parameters()).device),
        "output_device_before_cpu_copy": output_device,
        "actual_backend": (
            "PyTorch CPU eager"
            if device == "cpu"
            else "torch_nnpa eager; supported operations use NNPA, others may fall back to CPU"
        ),
        "runs": runs,
        "warmup": warmup,
        "output_shape": list(output.shape),
        "finite": bool(torch.isfinite(output).all().item()),
        "latency_ms": {
            "p50": percentile(samples, 0.50),
            "p95": percentile(samples, 0.95),
            "p99": percentile(samples, 0.99),
            "min": min(samples),
            "max": max(samples),
        },
        "samples_ms": samples,
    }, output


def save_report(report: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(report, temporary, indent=2, allow_nan=False)
            temporary.write("\n")
        temporary_path.replace(output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    args = arguments()
    # Set before importing torch or its extension; routing logs distort timings.
    os.environ["TORCH_NNPA_DEBUG"] = "0"
    os.environ["ZDNN_ENABLE_PRECHECK"] = "false"
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Stock ImageNet 1000-class ResNet-18 platform benchmark; not a fraud detector and not zDLC compilation",
        "plan_gate_status": "G2 PyTorch fallback timing evidence only; this script does not complete the zDLC compilation requirement",
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "input_shape": list(INPUT_SHAPE),
        "input_kind": "fixed synthetic float32 tensor; three RGB crops for one image",
        "seed": args.seed,
        "threads": args.threads,
        "interop_threads": args.interop_threads,
        "timing_scope": "CPU input to requested device, eager forward, output copied back to CPU; preprocessing, model loading and warmup excluded",
        "percentile_method": "linear interpolation at (N-1)*quantile",
        "routing_evidence": "not collected during timing; NNPA device labels alone do not establish per-operation offload",
        "benchmarks": {},
    }
    try:
        import torch

        torch.set_num_threads(args.threads)
        torch.set_num_interop_threads(args.interop_threads)
        torch.manual_seed(args.seed)
        report["torch_version"] = torch.__version__
        cpu_model, report["provenance"] = load_stock_model(torch, args.cache_dir.resolve())
        inputs = torch.randn(INPUT_SHAPE, dtype=torch.float32, device="cpu")
        report["benchmarks"]["cpu"], cpu_output = benchmark(
            torch, cpu_model, inputs, "cpu", args.warmup, args.runs
        )
        # Preserve the working CPU result before loading the optional extension.
        save_report(report, args.output)
    except Exception as error:
        report["benchmarks"]["cpu"] = {
            "status": "failed",
            "error_type": type(error).__name__,
            "error": str(error),
        }
        save_report(report, args.output)
        print(json.dumps(report, indent=2, allow_nan=False))
        return 1

    if args.cpu_only:
        report["benchmarks"]["nnpa"] = {"status": "skipped", "reason": "--cpu-only"}
    else:
        # Save intent too: a native extension failure cannot erase CPU timings.
        report["benchmarks"]["nnpa"] = {"status": "starting"}
        save_report(report, args.output)
        try:
            import torch_nnpa  # noqa: F401 - import registers the nnpa device

            try:
                report["torch_nnpa_version"] = importlib.metadata.version("torch_nnpa")
            except importlib.metadata.PackageNotFoundError:
                report["torch_nnpa_version"] = "unknown"
            nnpa_model = copy.deepcopy(cpu_model).nnpa().eval()
            report["benchmarks"]["nnpa"], nnpa_output = benchmark(
                torch, nnpa_model, inputs, "nnpa", args.warmup, args.runs
            )
            difference = (cpu_output - nnpa_output).abs()
            report["numerical_comparison"] = {
                "finite_cpu": bool(torch.isfinite(cpu_output).all().item()),
                "finite_nnpa": bool(torch.isfinite(nnpa_output).all().item()),
                "max_abs_diff": float(difference.max().item()) if torch.isfinite(difference).all().item() else None,
                "interpretation": "descriptive difference only; no parity threshold asserted; final detector requires calibration on LinuxONE",
            }
        except Exception as error:
            report["benchmarks"]["nnpa"] = {
                "status": "unavailable_or_failed",
                "error_type": type(error).__name__,
                "error": str(error),
            }
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    save_report(report, args.output)
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
