#!/usr/bin/env python3
"""Run inside aegis-gateway: real upload, claim, replay, journal and COBOL checks."""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAFAAAAA8CAIAAAB+RarbAAAApElEQVR4nO3bsQ3CUAwA0cvpDwQbwQSsAhMwEiuxACGRkIBwvNYuLNulPe0OZ0okRmIkRmIkRmIkRmIkZjwP364nNmh/vMyFJGa82LBvs7iSEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxY03SRo+XHspNePq/APw4iZEYiZEYiZEYP13Au90BSW4Kmsyy2kQAAAAASUVORK5CYII="
)


def request(path, *, data=None, headers=None, method=None):
    req = urllib.request.Request("http://127.0.0.1:8080" + path, data=data,
                                 headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def main(expect_degraded=False):
    # A missing model/threshold must not hide a broken COBOL executable.
    binary = os.environ.get("CLMIPOL_BIN", "/app/txn/CLMIPOL")
    for line, expected in (
        ("00100 08500 06000 0 0 1", "GREEN NONE"),
        ("09000 08500 06000 0 0 1", "RED AI_SCORE_ABOVE_RED"),
        ("00100 08500 06000 0 0 0", "AMBER RISK_SERVICE_UNAVAILABLE"),
    ):
        run = subprocess.run([binary], input=line + "\n", text=True,
                             capture_output=True, check=True, timeout=5)
        assert run.stdout.strip() == expected, "COBOL policy smoke failed"
    deadline = time.monotonic() + 30
    while True:
        try:
            health = request("/health")
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    assert health["txn"] == "CLMI" and health["policy_engine"] == "cobol"
    boundary = "aegis-smoke-" + uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic-gateway.png"\r\n'
        'Content-Type: image/png\r\n\r\n'
    ).encode() + PNG + f"\r\n--{boundary}--\r\n".encode()
    # Startup of the ML process can take longer than the gateway. Repeating
    # this exact upload is safe because evidence is addressed by its SHA-256.
    deadline = time.monotonic() + 30
    while True:
        try:
            evidence = request("/evidence", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            break
        except urllib.error.HTTPError as error:
            if error.code != 503 or time.monotonic() >= deadline:
                raise
            time.sleep(1)
    assert evidence["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert evidence["evidence_id"] == "EV-" + evidence["sha256"]
    claim = {
        "idempotency_key": "smoke-" + uuid.uuid4().hex,
        "policy_number": "PN-0000-0000",
        "claimant": {"name": "Smoke Example", "email": "smoke@example.invalid", "phone": "07700 900123"},
        "incident": {"date": "2026-10-17", "description": "Synthetic check for Smoke Example; call 07700 900123."},
        "evidence": [{"evidence_id": evidence["evidence_id"]}],
    }
    payload = json.dumps(claim).encode()
    headers = {"Content-Type": "application/json"}
    started = time.perf_counter()
    decision = request("/claims", data=payload, headers=headers)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if expect_degraded:
        assert decision["band"] == "AMBER", "Unavailable scoring must fail to AMBER"
        assert {"THRESHOLDS_UNAVAILABLE", "RISK_SERVICE_UNAVAILABLE"} & set(decision["reasons"]), "Expected missing-dependency reason"
    replay = request("/claims", data=payload, headers=headers)
    assert replay["claim_id"] == decision["claim_id"] and replay["replayed"] is True
    claim_id = decision["claim_id"]
    events = request(f"/journal?claim_id={claim_id}")
    assert sum(row["event"] == "DECISION" for row in events) == 1
    assert any(row["event"] == "REPLAY" for row in events)
    views = request(f"/claims/{claim_id}/views", headers={"X-Role": "adjuster"})
    protected = json.dumps({"record": views["pseudonymised"], "egress": views["egress"]["payload"]})
    for identifier in ("Smoke Example", "smoke@example.invalid", "07700 900123", "PN-0000-0000"):
        assert identifier not in protected, "Fixture identifier escaped pseudonymisation"
    assert views["original"] is None
    print(json.dumps({"status": "passed", "scope": "synthetic gateway plumbing; no detector accuracy or latency percentile validation",
                      "claim_id": claim_id, "band": decision["band"], "reasons": decision["reasons"],
                      "cobol_smoke_verified": True, "upload_verified": True,
                      "idempotency_verified": True, "journal_verified": True,
                      "pseudonymised_view_verified": True, "single_claim_round_trip_ms": round(elapsed_ms, 3)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-degraded", action="store_true", help="Require AMBER from missing detector/thresholds")
    main(parser.parse_args().expect_degraded)
