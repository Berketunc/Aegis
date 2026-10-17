"""SQLite system of record for CLMI.

commit_decision() is our SYNCPOINT: claim, decision, egress and journal rows are
written in one transaction or not at all. The journal is append-only, enforced by triggers.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from privacy import build_egress

SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_seq (
    n  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id      TEXT PRIMARY KEY,
    idem_key      TEXT UNIQUE NOT NULL,
    redacted_json TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    claim_id           TEXT PRIMARY KEY REFERENCES claims(claim_id),
    band               TEXT NOT NULL CHECK (band IN ('GREEN','AMBER','RED')),
    reasons            TEXT NOT NULL,
    model_version      TEXT,
    thresholds_version TEXT NOT NULL,
    policy_engine      TEXT NOT NULL,
    signals_json       TEXT,
    timings_json       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    sha256      TEXT,
    size_bytes  INTEGER,
    created_at  TEXT NOT NULL
);
-- Demo stand-in for the key-protected token vault (IBM Z crypto hardware in production).
CREATE TABLE IF NOT EXISTS token_vault (
    token TEXT PRIMARY KEY,
    kind  TEXT NOT NULL,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS egress (
    egress_id    TEXT PRIMARY KEY,
    claim_id     TEXT NOT NULL REFERENCES claims(claim_id),
    payload_json TEXT NOT NULL,
    dropped_json TEXT NOT NULL,
    ts           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    claim_id       TEXT PRIMARY KEY REFERENCES claims(claim_id),
    status         TEXT NOT NULL,
    note           TEXT,
    pii_added_json TEXT,
    ts             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS privacy_reviews (
    claim_id TEXT PRIMARY KEY REFERENCES claims(claim_id),
    status TEXT NOT NULL CHECK(status IN ('rules_only','pending','reviewed','unavailable')),
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    txn          TEXT NOT NULL DEFAULT 'CLMI',
    claim_id     TEXT,
    event        TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS journal_no_update BEFORE UPDATE ON journal
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS journal_no_delete BEFORE DELETE ON journal
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def connect(path: str) -> sqlite3.Connection:
    # isolation_level=None: we issue BEGIN/COMMIT ourselves so the syncpoint is explicit.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    # Existing demo records had only the deterministic first pass. Do not label
    # them as model-reviewed during an upgrade.
    conn.execute("INSERT OR IGNORE INTO privacy_reviews SELECT claim_id, 'rules_only', 'legacy_rules_only', ? FROM claims", (now(),))
    # In-memory background tasks cannot resume after this one-worker process
    # restarts. Preserve quarantine and report their interrupted status honestly.
    interrupted = [row[0] for row in conn.execute("SELECT claim_id FROM privacy_reviews WHERE status='pending'")]
    conn.execute("UPDATE privacy_reviews SET status='unavailable', reason='worker_restarted', updated_at=? WHERE status='pending'", (now(),))
    for claim_id in interrupted:
        journal(conn, claim_id, "PRIVACY_REVIEW_UNAVAILABLE", {"reason": "worker_restarted"})
    return conn


def _dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def journal(conn: sqlite3.Connection, claim_id: str | None, event: str, payload: dict[str, Any]) -> None:
    conn.execute("INSERT INTO journal (ts, claim_id, event, payload_json) VALUES (?,?,?,?)",
                 (now(), claim_id, event, _dumps(payload)))


def add_evidence(conn: sqlite3.Connection, evidence_id: str, sha256: str | None, size: int) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT OR IGNORE INTO evidence VALUES (?,?,?,?)", (evidence_id, sha256, size, now()))
        journal(conn, None, "EVIDENCE", {"evidence_id": evidence_id, "sha256": sha256, "size_bytes": size})
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def unknown_evidence(conn: sqlite3.Connection, ids: list[str]) -> list[str]:
    known = {r[0] for r in conn.execute(
        f"SELECT evidence_id FROM evidence WHERE evidence_id IN ({','.join('?' * len(ids))})", ids)}
    return [i for i in ids if i not in known]


def reserve_claim_id(conn: sqlite3.Connection) -> str:
    """Atomic sequence, like a CICS-assigned task number. Gaps (failed requests) are fine."""
    cur = conn.execute("INSERT INTO claim_seq (ts) VALUES (?)", (now(),))
    return f"CLM-{cur.lastrowid:06d}"


def find_by_key(conn: sqlite3.Connection, idem_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT c.claim_id, d.* FROM claims c JOIN decisions d USING (claim_id) WHERE c.idem_key = ?",
        (idem_key,)).fetchone()
    return _decision_row(row) if row else None


def get_decision(conn: sqlite3.Connection, claim_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM decisions WHERE claim_id = ?", (claim_id,)).fetchone()
    return _decision_row(row) if row else None


def _decision_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "claim_id": row["claim_id"],
        "band": row["band"],
        "reasons": json.loads(row["reasons"]),
        "model_version": row["model_version"],
        "thresholds_version": row["thresholds_version"],
        "policy_engine": row["policy_engine"],
        "timings_ms": json.loads(row["timings_json"]),
    }


class DuplicateKey(Exception):
    """Another request committed the same idempotency key first."""


def commit_decision(conn: sqlite3.Connection, *, claim_id: str, idem_key: str, redacted: dict[str, Any],
                    vault: list[tuple[str, str, str]], decision: dict[str, Any], signals: dict[str, Any] | None,
                    egress_id: str, egress_payload: dict[str, Any], egress_dropped: list[str],
                    privacy_status: str = "rules_only", privacy_reason: str = "granite_disabled") -> None:
    """SYNCPOINT: everything for one claim commits atomically."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO claims VALUES (?,?,?,?)", (claim_id, idem_key, _dumps(redacted), now()))
        conn.execute("INSERT INTO privacy_reviews VALUES (?,?,?,?)", (claim_id, privacy_status, privacy_reason, now()))
        conn.executemany("INSERT OR IGNORE INTO token_vault VALUES (?,?,?)", vault)
        conn.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)", (
            claim_id, decision["band"], _dumps(decision["reasons"]), decision.get("model_version"),
            decision["thresholds_version"], decision["policy_engine"],
            _dumps(signals) if signals is not None else None, _dumps(decision["timings_ms"])))
        journal(conn, claim_id, "DECISION", {k: decision[k] for k in (
            "band", "reasons", "model_version", "thresholds_version", "policy_engine", "timings_ms")})
        publishable = dict(egress_payload)
        dropped = list(egress_dropped)
        if privacy_status in {"pending", "unavailable"}:
            for field in ("description_redacted", "damage_category"):
                if field in publishable:
                    publishable.pop(field)
                    dropped.append(field)
        conn.execute("INSERT INTO egress VALUES (?,?,?,?,?)",
                     (egress_id, claim_id, _dumps(publishable), _dumps(sorted(set(dropped))), now()))
        journal(conn, claim_id, "EGRESS", {"egress_id": egress_id, "fields": sorted(publishable),
                                           "dropped": sorted(set(dropped)), "privacy_review": privacy_status})
        conn.execute("COMMIT")
    except sqlite3.IntegrityError as e:
        conn.execute("ROLLBACK")
        if "claims.idem_key" in str(e):
            raise DuplicateKey(idem_key) from e
        raise
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def record_replay(conn: sqlite3.Connection, claim_id: str, idem_key: str) -> None:
    journal(conn, claim_id, "REPLAY", {"idem_key": idem_key})


def save_note(conn: sqlite3.Connection, claim_id: str, status: str, note: str | None,
              pii_added: list[str], vault: list[tuple[str, str, str]],
              redacted: dict[str, Any] | None, privacy_status: str | None = None) -> None:
    """After-decision update from Granite. Never touches the decision; journaled as new rows."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        review_status = privacy_status or ("reviewed" if status == "ready" or redacted is not None else "unavailable")
        if review_status not in {"reviewed", "unavailable"}:
            raise ValueError("Invalid privacy review result")
        conn.executemany("INSERT OR IGNORE INTO token_vault VALUES (?,?,?)", vault)
        if redacted is not None:
            conn.execute("UPDATE claims SET redacted_json = ? WHERE claim_id = ?", (_dumps(redacted), claim_id))
        conn.execute("INSERT OR REPLACE INTO privacy_reviews VALUES (?,?,?,?)", (
            claim_id, review_status, "model_second_pass" if review_status == "reviewed" else "second_pass_unavailable", now()))
        # A reviewed description can be released even when note formatting fails.
        # A failed PII pass leaves description/category out of the egress payload.
        current = conn.execute("SELECT payload_json, dropped_json FROM egress WHERE claim_id = ?", (claim_id,)).fetchone()
        if current is not None:
            payload = json.loads(current[0])
            dropped = set(json.loads(current[1]))
            if review_status == "reviewed":
                row = conn.execute("SELECT redacted_json FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
                reviewed_record = json.loads(row[0])
                payload["description_redacted"] = reviewed_record.get("incident", {}).get("description", "")
                payload["damage_category"] = reviewed_record.get("damage_category")
                payload, fresh_dropped = build_egress(payload)
                dropped = sorted((dropped - {"description_redacted", "damage_category"}) | set(fresh_dropped))
                event = "EGRESS_PII_UPDATED" if redacted is not None else "EGRESS_PRIVACY_RELEASED"
                journal(conn, claim_id, event, {"fields": sorted(payload), "dropped": dropped})
            else:
                for field in ("description_redacted", "damage_category"):
                    payload.pop(field, None)
                    dropped.add(field)
            conn.execute("UPDATE egress SET payload_json = ?, dropped_json = ?, ts = ? WHERE claim_id = ?",
                         (_dumps(payload), _dumps(sorted(dropped)), now(), claim_id))
        conn.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?)",
                     (claim_id, status, note, _dumps(pii_added), now()))
        journal(conn, claim_id, "PII_SECOND_PASS", {"tokens_added": pii_added, "status": review_status})
        journal(conn, claim_id, "NOTE", {"status": status})
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def get_privacy_review(conn: sqlite3.Connection, claim_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT status, reason FROM privacy_reviews WHERE claim_id = ?", (claim_id,)).fetchone()
    status, reason = (row["status"], row["reason"]) if row else ("rules_only", "legacy_rules_only")
    return {"status": status, "reason": reason, "description_released": status in {"rules_only", "reviewed"}}


def get_claim(conn: sqlite3.Connection, claim_id: str, *, include_quarantined: bool = False) -> dict[str, Any] | None:
    row = conn.execute("SELECT redacted_json FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
    if row is None:
        return None
    record = json.loads(row[0])
    if not include_quarantined and not get_privacy_review(conn, claim_id)["description_released"]:
        record.get("incident", {})["description"] = None
        record["damage_category"] = None
    return record


def get_note(conn: sqlite3.Connection, claim_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT status, note FROM notes WHERE claim_id = ?", (claim_id,)).fetchone()
    return {"status": row["status"], "note": row["note"]} if row else None


def get_egress(conn: sqlite3.Connection, claim_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT egress_id, payload_json, dropped_json FROM egress WHERE claim_id = ?",
                       (claim_id,)).fetchone()
    if not row:
        return None
    payload = json.loads(row[1])
    if not get_privacy_review(conn, claim_id)["description_released"]:
        payload.pop("description_redacted", None)
        payload.pop("damage_category", None)
    return {"egress_id": row[0], "payload": payload, "dropped": json.loads(row[2])}


def vault_lookup(conn: sqlite3.Connection, tokens: list[str]) -> dict[str, str]:
    if not tokens:
        return {}
    rows = conn.execute(f"SELECT token, value FROM token_vault WHERE token IN ({','.join('?' * len(tokens))})",
                        tokens)
    return {r[0]: r[1] for r in rows}


def read_journal(conn: sqlite3.Connection, claim_id: str | None, limit: int = 200) -> list[dict[str, Any]]:
    if claim_id:
        rows = conn.execute("SELECT * FROM journal WHERE claim_id = ? ORDER BY seq", (claim_id,))
    else:
        rows = conn.execute("SELECT * FROM journal ORDER BY seq DESC LIMIT ?", (limit,))
    return [{**dict(r), "payload_json": json.loads(r["payload_json"])} for r in rows]
