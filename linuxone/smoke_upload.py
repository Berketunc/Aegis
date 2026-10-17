#!/usr/bin/env python3
"""Run inside aegis-risk; upload a synthetic PNG, check its stored artifacts and, when the
trained detector is loaded, score it three times to show cold and warm stage timings."""
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import urllib.request
import uuid

import numpy as np
from PIL import Image, ImageDraw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def main():
    image = Image.new("RGB", (800, 600), (48, 96, 144))
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 700, 500), outline="white", width=8)
    draw.line((100, 500, 700, 100), fill="yellow", width=8)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    boundary = "aegis-smoke-" + uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic-smoke.png"\r\n'
        'Content-Type: image/png\r\n\r\n'
    ).encode() + raw + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8000/evidence", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-Risk-Secret": os.environ["AEGIS_RISK_SECRET"]},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 200, "Upload did not return HTTP 200"
        result = json.load(response)
    assert result["sha256"] == digest, "Uploaded bytes have a different SHA-256"
    assert result["evidence_id"] == "EV-" + digest, "Unexpected evidence ID"
    assert len(result["crop_boxes"]) == 3, "Expected three crop boxes"
    milliseconds = result["timings_ms"]["upload_preprocess"]
    assert math.isfinite(milliseconds) and milliseconds >= 0, "Invalid preprocessing timing"
    stored = Path(os.environ.get("AEGIS_DATA_DIR", "/data")) / "objects" / digest
    encrypted = (stored / "original.aesgcm").read_bytes()
    assert encrypted.startswith(b"AEGIS1\x00"), "Unexpected encrypted envelope"
    key = base64.b64decode(os.environ["AEGIS_EVIDENCE_KEY_B64"], validate=True)
    restored = AESGCM(key).decrypt(encrypted[7:19], encrypted[19:], digest.encode())
    assert restored == raw, "Encrypted original did not round-trip"
    crops = np.load(stored / "crops.npy", allow_pickle=False)
    assert crops.shape == (3, 3, 224, 224) and crops.dtype == np.uint8, "Invalid crop cache"
    report = {"status": "passed", "input": "synthetic plumbing fixture",
              "evidence_id": result["evidence_id"], "sha256_verified": True,
              "encrypted_original_verified": True, "crops_verified": True,
              "upload_preprocess_ms": milliseconds,
              "scope": "one upload; no detector or latency percentile validation"}
    with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10) as response:
        health = json.load(response)
    if health.get("ready"):
        # Three scores of the same evidence under fresh fixture claim IDs: the first is
        # cold, the rest warm. Stage timings are wall-clock inside the service only.
        scores = []
        for attempt in range(3):
            payload = json.dumps({"claim_id": "SMOKE-" + uuid.uuid4().hex[:12],
                                  "description": "Synthetic timing fixture, no personal data.",
                                  "evidence": [{"evidence_id": result["evidence_id"]}],
                                  "privacy_context": {"damage_category": "fixture"}}).encode()
            scoring = urllib.request.Request(
                "http://127.0.0.1:8000/risk/score", data=payload, method="POST",
                headers={"Content-Type": "application/json", "X-Risk-Secret": os.environ["AEGIS_RISK_SECRET"]})
            with urllib.request.urlopen(scoring, timeout=60) as response:
                body = json.load(response)
            scores.append({"attempt": attempt + 1, "ai_score": round(body["signals"]["ai_score"], 4),
                           "native_short_side": body["signals"]["metadata"].get("native_short_side"),
                           "timings_ms": body["timings_ms"]})
        report.update({"model_version": health["model"]["version"], "device": health["model"]["device"],
                       "torch_threads": os.environ.get("AEGIS_TORCH_THREADS"), "scoring": scores,
                       "gateway_deadline_ms": 300,
                       "note": "The synthetic drawing is not a car photo; ai_score is meaningless, timings are real."})
    else:
        report["scoring"] = "skipped: detector not ready"
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
