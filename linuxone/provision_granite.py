#!/usr/bin/env python3
"""Provision one pinned public BE Granite artifact; never executes model code.

Run on LinuxONE with --download. The Mac-side bundle includes metadata only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "llm" / "model-source.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, expected_size: int, expected_sha256: str,
                header_hex: str | None = None) -> dict:
    if not path.is_file():
        raise ValueError(f"Required asset is absent: {path.name}")
    if path.stat().st_size != expected_size:
        raise ValueError(f"Asset size does not match the pinned source: {path.name}")
    if header_hex:
        with path.open("rb") as stream:
            if stream.read(len(bytes.fromhex(header_hex))) != bytes.fromhex(header_hex):
                raise ValueError("GGUF header is not the pinned big-endian v3 format")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(f"Asset SHA-256 does not match the pinned source: {path.name}")
    return {"size_bytes": expected_size, "sha256": actual, "verified": True}


def download_verified(url: str, target: Path, size: int, digest: str,
                      header_hex: str | None = None, opener=urllib.request.urlopen) -> dict:
    if target.exists():
        return verify_file(target, size, digest, header_hex)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if shutil.disk_usage(target.parent).free < size + 256 * 1024**2:
        raise ValueError("Insufficient disk space for the pinned artifact and safety margin")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AEGIS-LinuxONE-Granite-Provisioner/1"})
        with os.fdopen(descriptor, "wb") as output, opener(request, timeout=60) as response:
            count = 0
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                count += len(chunk)
                if count > size:
                    raise ValueError("Download exceeded the pinned asset size")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        result = verify_file(temporary, size, digest, header_hex)
        if target.exists():
            # Do not replace an independently supplied model or key material.
            return verify_file(target, size, digest, header_hex)
        temporary.replace(target)
        return result
    finally:
        temporary.unlink(missing_ok=True)


def provision(runtime_dir: Path, source: dict, download: bool) -> dict:
    model_dir = runtime_dir.resolve() / "models"
    model = model_dir / "granite.gguf"
    license_file = model_dir / "granite.LICENSE-2.0.txt"
    model_url = (f"https://huggingface.co/{source['repository']}/resolve/"
                 f"{source['revision']}/{source['filename']}")
    if download:
        model_result = download_verified(model_url, model, source["size_bytes"], source["sha256"],
                                         source["gguf_header_hex"])
        license_result = download_verified(source["license_url"], license_file,
                                           source["license_size_bytes"], source["license_sha256"])
    else:
        model_result = verify_file(model, source["size_bytes"], source["sha256"], source["gguf_header_hex"])
        license_result = verify_file(license_file, source["license_size_bytes"], source["license_sha256"])
    report = {
        "status": "verified_assets_only",
        "runtime_validated": False,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_path": str(model),
        "model": {**source, **model_result, "download_url": model_url},
        "license_asset": {"path": str(license_file), **license_result},
        "note": "Checksum and endian verification only; run smoke_granite.py on LinuxONE for inference evidence.",
    }
    provenance = model_dir / "granite.provenance.json"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".granite-provenance.", dir=model_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(report, target, indent=2)
            target.write("\n")
        Path(temporary_name).replace(provenance)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, default=ROOT / ".runtime")
    parser.add_argument("--download", action="store_true", help="Download the pinned ~784 MiB public model and licence")
    parser.add_argument("--show-source", action="store_true", help="Print pinned metadata without touching files/network")
    args = parser.parse_args()
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    if args.show_source:
        print(json.dumps(source, indent=2))
        return 0
    try:
        report = provision(args.runtime_dir, source, args.download)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        # Do not print signed redirect URLs or environment values.
        print(json.dumps({"status": "provision_failed", "error_type": type(exc).__name__,
                          "error": str(exc) if isinstance(exc, ValueError) else "Public asset download or local file operation failed",
                          "runtime_validated": False}), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
