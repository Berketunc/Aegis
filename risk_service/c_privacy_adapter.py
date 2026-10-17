"""Bridge C's shared privacy rules to B's adapter and durable egress allowlist log.

Rows record payload PREPARATION, not a claim that anything left the VM. C logs
the final decision payload separately at its transaction syncpoint.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

from privacy import build_egress, redact


def _settings():
    key = os.environ.get("HMAC_KEY", "").encode()
    if len(key) < 32:
        raise RuntimeError("A stable HMAC_KEY of at least 32 bytes is required")
    return key, Path(os.environ.get("AEGIS_DATA_DIR", "/data")) / "privacy_egress.sqlite3"


@contextmanager
def _connect(path):
    db = sqlite3.connect(path, timeout=5)
    try:
        with db:
            yield db
    finally:
        db.close()


def check_ready():
    _, path = _settings()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _connect(path) as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS prepared_egress (
                egress_payload_id TEXT PRIMARY KEY,
                claim_id TEXT,
                event TEXT NOT NULL CHECK(event='PREPARED'),
                payload_json TEXT NOT NULL,
                dropped_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS prepared_egress_no_update BEFORE UPDATE ON prepared_egress
            BEGIN SELECT RAISE(ABORT, 'egress log is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS prepared_egress_no_delete BEFORE DELETE ON prepared_egress
            BEGIN SELECT RAISE(ABORT, 'egress log is append-only'); END;
        """)


def process(*, description: str, context: dict) -> dict:
    key, path = _settings()
    supplied = context.get("gazetteer", {})
    if not isinstance(supplied, dict):
        raise ValueError("Invalid privacy gazetteer")
    gazetteer = {name: value for name, value in supplied.items()
                 if name in {"name", "email", "phone", "policy_number", "postcode", "registration"}
                 and isinstance(value, str)}
    description_redacted, _ = redact(description, key, gazetteer)
    damage_category = context.get("damage_category")
    # Do not retain arbitrary context fields or raw identifiers. This module's
    # vault remains C's responsibility; the gateway already retained its findings.
    record = {"description_redacted": description_redacted, "damage_category": damage_category}
    payload, dropped = build_egress(record)
    egress_id = "EG-RISK-" + uuid.uuid4().hex
    claim_id = context.get("claim_id")
    if claim_id is not None and (not isinstance(claim_id, str) or len(claim_id) > 100):
        raise ValueError("Invalid privacy claim context")
    with _connect(path) as db:
        db.execute("INSERT INTO prepared_egress VALUES (?, ?, 'PREPARED', ?, ?, ?)", (
            egress_id, claim_id, json.dumps(payload, separators=(",", ":"), sort_keys=True),
            json.dumps(dropped), datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        ))
    return {"redacted_record": {"incident": {"description": description_redacted}},
            "egress_payload_id": egress_id}


# Generic adapters can expose this optional startup probe. A configured import
# alone must not claim that an absent HMAC key or unwritable log is ready.
process.check_ready = check_ready
