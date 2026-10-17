#!/usr/bin/env python3
"""Push labelled photos through the real gateway and record the decisions (stdlib only).

Run on the VM host against the loopback gateway. Each photo becomes one claim with a
fictional persona; the band is whatever the deployed detector and CLMIPOL decide.
The report lists band, reasons, score path and timings per photo and is the
"one GREEN and one RED through the real pipeline" evidence.

    python3 linuxone/record_demo_claims.py --expect GREEN ui/evidence/D01.jpg \
        --expect RED output/crash-closeups-2026-10-17/01-red-sedan-front-corner.png

Writes eval/demo_claims.json. Exit status 1 if any expectation is not met.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = [("Alex Doe", "alex.doe@example.com", "07700 900123", "PN-4471-0098", "bumper",
             "Reversed into a bollard in a supermarket car park; rear bumper pushed in."),
            ("Priya Shah", "priya.shah@example.com", "07700 900218", "PN-2210-7781", "scratch",
             "Trolley scraped along the passenger door outside a retail park."),
            ("Sam Okafor", "sam.okafor@example.com", "07700 900341", "PN-6603-1145", "dent",
             "Another driver clipped the front wing while parking; the panel is dented.")]


def call(url: str, data=None, headers=None, timeout=30):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        try:
            body = json.loads(body)
        except ValueError:
            pass
        return error.code, body


def upload(gateway: str, path: Path):
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    boundary = "aegis-" + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return call(gateway + "/evidence", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--expect", nargs=2, action="append", metavar=("BAND", "PHOTO"), required=True,
                        help="expected band (GREEN/AMBER/RED/ANY) and photo path; repeatable")
    parser.add_argument("--output", type=Path, default=ROOT / "eval" / "demo_claims.json")
    args = parser.parse_args()
    status, health = call(args.gateway + "/health")
    if status != 200 or health.get("txn") != "CLMI":
        print(json.dumps({"status": "failed", "error": "gateway not reachable", "detail": health}), file=sys.stderr)
        return 1
    results, failures = [], 0
    for index, (expected, photo) in enumerate(args.expect):
        path = Path(photo)
        name, email, phone, policy, category, description = PERSONAS[index % len(PERSONAS)]
        if not path.is_file():
            results.append({"photo": photo, "status": "missing_file"}); failures += 1; continue
        status, ev = upload(args.gateway, path)
        if status != 200:
            results.append({"photo": photo, "status": "upload_failed", "http": status, "detail": ev}); failures += 1; continue
        claim = {"idempotency_key": "demo-" + uuid.uuid4().hex, "policy_number": policy,
                 "claimant": {"name": name, "email": email, "phone": phone},
                 "incident": {"date": datetime.now(timezone.utc).date().isoformat(), "description": description},
                 "damage_category": category, "evidence": [{"evidence_id": ev["evidence_id"]}]}
        started = time.perf_counter()
        status, decision = call(args.gateway + "/claims", json.dumps(claim).encode(), {"Content-Type": "application/json"})
        http_ms = round((time.perf_counter() - started) * 1000, 1)
        if status != 200:
            results.append({"photo": photo, "status": "claim_failed", "http": status, "detail": decision}); failures += 1; continue
        ok = expected.upper() == "ANY" or decision["band"] == expected.upper()
        failures += not ok
        timings = decision.get("timings_ms", {})
        results.append({"photo": photo, "evidence_id": ev["evidence_id"], "claim_id": decision["claim_id"],
                        "expected": expected.upper(), "band": decision["band"], "as_expected": ok,
                        "reasons": decision["reasons"], "model_version": decision.get("model_version"),
                        "thresholds_version": decision.get("thresholds_version"), "policy_engine": decision.get("policy_engine"),
                        "timings_ms": {"gateway_total": timings.get("total"), "risk": timings.get("risk"),
                                       "inference": (timings.get("risk_detail") or {}).get("inference"),
                                       "policy": timings.get("policy"), "http_round_trip": http_ms}})
    report = {"status": "passed" if not failures else "failed", "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "gateway_health": {k: health.get(k) for k in ("thresholds_version", "tau_red", "tau_amber", "policy_engine")},
              "scope": "real upload, detector, COBOL rule, journal; photo labels are the operator's expectation, not ground truth",
              "claims": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for r in results:
        print(f"{r.get('band', r['status']):6} expected {r.get('expected', '-'):6} {Path(r['photo']).name}  {','.join(r.get('reasons', []))}  "
              f"{(r.get('timings_ms') or {}).get('gateway_total', '-')} ms")
    print(("passed" if not failures else f"{failures} not as expected") + f" -> {args.output}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
