#!/usr/bin/env python3
"""Real HTTP fail-safe/recovery check using an isolated, explicitly fake risk fixture.

Run inside the gateway image. No existing services, runtime files, model weights
or production thresholds are changed. The gateway and policy are the real code;
the fixture scores are not detector evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import platform
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-python", action="store_true", help="Mac-only policy twin check; native COBOL remains required on VM")
    parser.add_argument("--output", type=Path, default=None,
                        help="Also save the JSON report, e.g. eval/failure_smoke.json (plan 12.1 step 6 evidence)")
    args = parser.parse_args()
    source = Path(globals().get("__file__", "<stdin>"))
    repo = source.resolve().parents[1] if source.is_file() else Path.cwd()
    binary = Path(os.environ.get("CLMIPOL_BIN", str(repo / "txn" / "CLMIPOL")))
    policy = "cobol" if binary.is_file() and os.access(binary, os.X_OK) else "python"
    if policy != "cobol" and not args.allow_python:
        raise SystemExit("Native CLMIPOL missing: run inside aegis-gateway image")
    processes = []
    with tempfile.TemporaryDirectory(prefix="aegis-fault-") as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        thresholds = root / "thresholds.json"
        thresholds.write_text(json.dumps({"version": "fault-fixture-only", "tau_amber": .6, "tau_red": .85}))
        risk_port, gateway_port = free_port(), free_port()
        while gateway_port == risk_port:
            gateway_port = free_port()
        env = {**os.environ, "RISK_URL": f"http://127.0.0.1:{risk_port}", "RISK_SECRET": secrets.token_hex(32),
               "HMAC_KEY": secrets.token_hex(32), "DB_PATH": str(root / "clmi.db"),
               "THRESHOLDS_PATH": str(thresholds), "POLICY_ENGINE": policy,
               "CLMIPOL_BIN": str(binary), "GRANITE_URL": "", "UI_DIR": str(root / "no-ui"), "RISK_TIMEOUT_MS": "300"}

        def launch(module, port):
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1",
                                        "--port", str(port), "--no-access-log", "--log-level", "error"],
                                       cwd=repo, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(process)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"{module} exited during startup")
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health", timeout=.5).status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            raise RuntimeError(f"{module} startup timed out")

        def stop(process):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        try:
            risk = launch("txn.stub_risk:app", risk_port)
            launch("txn.gateway.app:app", gateway_port)
            with httpx.Client(base_url=f"http://127.0.0.1:{gateway_port}", timeout=5) as client:
                upload = client.post("/evidence", files={"file": ("fault-fixture.jpg", b"\xff\xd8\xff\xe0" + b"fixture" * 10, "image/jpeg")})
                upload.raise_for_status()
                body = {"policy_number": "PN-TEST-0001", "claimant": {"name": "Alex Doe", "email": "alex@example.com", "phone": "07700 900123"},
                        "incident": {"date": "2026-10-12", "description": "Fictional fault-test bumper dent."},
                        "damage_category": "bumper", "evidence": [{"evidence_id": upload.json()["evidence_id"]}]}
                results = []

                def submit(scenario, expected_band, reason=None):
                    started = time.perf_counter()
                    response = client.post("/claims", json={**body, "idempotency_key": "fault-" + scenario})
                    elapsed = (time.perf_counter() - started) * 1000
                    response.raise_for_status()
                    decision = response.json()
                    assert decision["band"] == expected_band, decision
                    assert decision["policy_engine"] == policy, decision
                    if reason:
                        assert reason in decision["reasons"], decision
                    rows = client.get("/journal", params={"claim_id": decision["claim_id"]}).json()
                    assert len([row for row in rows if row["event"] == "DECISION"]) == 1
                    results.append({"scenario": scenario, "band": decision["band"], "reasons": decision["reasons"],
                                    "http_ms": round(elapsed, 3), "risk_ms": decision["timings_ms"]["risk"]})
                    return decision

                submit("healthy", "GREEN")
                stop(risk)
                down = submit("connection_refused", "AMBER", "RISK_SERVICE_UNAVAILABLE")
                replay = client.post("/claims", json={**body, "idempotency_key": "fault-connection_refused"}).json()
                assert replay["claim_id"] == down["claim_id"] and replay["replayed"]
                risk = launch("txn.stub_risk:app", risk_port)
                httpx.post(f"http://127.0.0.1:{risk_port}/stub/mode", json={"mode": "slow"}, timeout=2).raise_for_status()
                slow = submit("over_300ms", "AMBER", "RISK_SERVICE_UNAVAILABLE")
                assert 200 <= slow["timings_ms"]["risk"] < 600, slow
                httpx.post(f"http://127.0.0.1:{risk_port}/stub/mode", json={"mode": "ok"}, timeout=2).raise_for_status()
                submit("recovered", "GREEN")
                report = {"status": "passed", "policy_engine": policy, "architecture": platform.machine(),
                          "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "scenarios": results, "replay_verified": True,
                          "scope": "isolated real HTTP gateway + controlled risk fixture; no trained detector or production outage"}
                text = json.dumps(report, indent=2)
                if args.output is not None:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(text + "\n", encoding="utf-8")
                print(text)
        finally:
            for process in reversed(processes):
                stop(process)


if __name__ == "__main__":
    main()
