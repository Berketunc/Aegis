"""Encrypted originals and non-pickle crop arrays, addressed by full SHA-256."""
import base64
import hashlib
import io
import os
from pathlib import Path
import tempfile
import threading

import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .precompute import PREPROCESS_VERSION, PHASH_VERSION, prepare_image, heatmap_crops


def load_encryption_key(value: str) -> bytes:
    try:
        key = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("AEGIS_EVIDENCE_KEY_B64 must be base64-encoded 32 random bytes") from exc
    if len(key) != 32:
        raise ValueError("AEGIS_EVIDENCE_KEY_B64 must decode to 32 bytes")
    return key


class EvidenceStore:
    def __init__(self, root: Path, index, key: bytes):
        self.root, self.index, self.cipher = root, index, AESGCM(key)
        self.lock = threading.Lock()
        (root / "objects").mkdir(exist_ok=True, mode=0o700)

    @staticmethod
    def atomic_write(path: Path, data: bytes):
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def put(self, raw: bytes):
        sha = hashlib.sha256(raw).hexdigest()
        evidence_id = "EV-" + sha
        prepared = prepare_image(raw)
        destination = self.root / "objects" / sha
        with self.lock:
            destination.mkdir(exist_ok=True, mode=0o700)
            # Verify the currently configured key can decrypt reused evidence. A
            # key changed accidentally must never silently orphan old originals.
            original_path = destination / "original.aesgcm"
            if original_path.exists():
                encrypted = original_path.read_bytes()
                self.cipher.decrypt(encrypted[7:19], encrypted[19:], sha.encode())
            else:
                nonce = os.urandom(12)
                encrypted = b"AEGIS1\x00" + nonce + self.cipher.encrypt(nonce, raw, sha.encode())
                self.atomic_write(original_path, encrypted)
            crop_buffer = io.BytesIO()
            np.save(crop_buffer, prepared.crops, allow_pickle=False)
            self.atomic_write(destination / "crops.npy", crop_buffer.getvalue())
            self.index.add(evidence_id, sha, prepared, PREPROCESS_VERSION, PHASH_VERSION)
        return {"evidence_id": evidence_id, "sha256": sha, "preprocessing_version": PREPROCESS_VERSION,
                "phash_version": PHASH_VERSION, "crop_boxes": prepared.crop_boxes}

    def load_crops(self, record):
        # The SHA is taken from our DB rather than interpolating user input.
        crops = np.load(self.root / "objects" / record["sha256"] / "crops.npy", allow_pickle=False)
        if crops.shape != (3, 3, 224, 224) or crops.dtype != np.uint8:
            raise ValueError("Stored crops have an incompatible shape or dtype")
        return crops

    def load_heatmap_crops(self, record):
        encrypted = (self.root / "objects" / record["sha256"] / "original.aesgcm").read_bytes()
        if not encrypted.startswith(b"AEGIS1\x00"):
            raise ValueError("Unknown evidence encryption format")
        raw = self.cipher.decrypt(encrypted[7:19], encrypted[19:], record["sha256"].encode())
        return heatmap_crops(raw)
