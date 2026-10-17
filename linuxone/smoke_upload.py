#!/usr/bin/env python3
"""Run inside aegis-risk; upload a synthetic PNG and check its stored artifacts."""
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
    print(json.dumps({"status": "passed", "input": "synthetic plumbing fixture",
                      "evidence_id": result["evidence_id"], "sha256_verified": True,
                      "encrypted_original_verified": True, "crops_verified": True,
                      "upload_preprocess_ms": milliseconds,
                      "scope": "one upload; no detector or latency percentile validation"}, indent=2))


if __name__ == "__main__":
    main()
