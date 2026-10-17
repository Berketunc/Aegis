#!/usr/bin/env python3
"""Run the actual Granite smoke suite inside its VM container and save safe metrics.

No secret, claim text or generated note is read back to the host. Docker statistics
are sampled while the real model suite runs. This script does not download a model.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker_prefix() -> list[str]:
    for command in (["docker"], ["sudo", "-n", "docker"]):
        try:
            result = subprocess.run(command + ["info"], capture_output=True, timeout=20)
            if result.returncode == 0:
                return command
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise RuntimeError("Docker unavailable; start Docker or run with suitable Docker access")


def sample_stats(command: list[str], names: list[str]) -> dict:
    result = subprocess.run(command + ["stats", "--no-stream", "--format", "{{json .}}", *names],
                            capture_output=True, text=True, timeout=20)
    rows = []
    for line in result.stdout.splitlines():
        try:
            row = json.loads(line)
            # Explicit allowlist: no inspect environment, command line, or mounted secret content.
            rows.append({key: row.get(key) for key in ("Name", "CPUPerc", "MemUsage", "MemPerc", "PIDs")})
        except ValueError:
            continue
    return {"elapsed_monotonic": round(time.monotonic(), 3), "containers": rows,
            "partial_or_unavailable": result.returncode != 0}


def collect(command: list[str], container: str, per_job_timeout: float, max_seconds: float) -> dict:
    inspected = subprocess.run(command + ["inspect", "--format", "{{.Image}}", container],
                               capture_output=True, text=True, timeout=20)
    if inspected.returncode:
        raise RuntimeError("Granite container is absent; provision the model and start the service first")
    process = subprocess.Popen(command + ["exec", container, "python3", "-m", "llm.real_smoke",
                                         "--timeout-seconds", str(per_job_timeout)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = time.monotonic()
    samples = []
    timed_out = False
    try:
        while process.poll() is None:
            if time.monotonic() - started > max_seconds:
                timed_out = True
                process.terminate()
                break
            try:
                samples.append(sample_stats(command, [container, "aegis-risk", "aegis-gateway"]))
            except (OSError, subprocess.TimeoutExpired):
                samples.append({"partial_or_unavailable": True, "containers": []})
            if process.poll() is None:
                time.sleep(2)
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    if timed_out:
        report = {"status": "failed", "error": "SMOKE_RUN_TIMEOUT", "real_model_exercised": False}
    else:
        try:
            report = json.loads(output)
            if not isinstance(report, dict):
                raise ValueError("Expected report object")
        except ValueError:
            report = {"status": "failed", "error": "CONTAINER_SMOKE_DID_NOT_RETURN_SAFE_JSON",
                      "real_model_exercised": False}
    if process.returncode != 0 and report.get("status") == "passed":
        report.update(status="failed", error="CONTAINER_SMOKE_EXITED_UNSUCCESSFULLY")
    report.update(container_image_id=inspected.stdout.strip(), docker_stats=samples,
                  elapsed_seconds=round(time.monotonic() - started, 2),
                  measured_at_utc=datetime.now(timezone.utc).isoformat())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="aegis-granite")
    parser.add_argument("--output", type=Path, default=ROOT / "eval" / "granite_smoke.json")
    parser.add_argument("--timeout-seconds", type=float, default=180, help="Per queued model job")
    parser.add_argument("--max-seconds", type=float, default=1200, help="Overall test budget")
    args = parser.parse_args()
    try:
        if platform.machine() != "s390x":
            raise RuntimeError("Run this smoke script on the LinuxONE VM, not on the laptop")
        report = collect(docker_prefix(), args.container, args.timeout_seconds, args.max_seconds)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        report = {"status": "not_ready", "error": "LINUXONE_CONTAINER_RUNTIME_UNAVAILABLE",
                  "real_model_exercised": False, "architecture": platform.machine()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "real_model_exercised": report.get("real_model_exercised", False),
                      "report": str(args.output)}))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
