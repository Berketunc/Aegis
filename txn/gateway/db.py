"""SQLite system of record for CLMI.

commit_decision() is our SYNCPOINT: claim, decision, egress and journal rows are
written in one transaction or not at all. The journal is append-only, enforced by triggers.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

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
                    egress_id: str, egress_payload: dict[str, Any], egress_dropped: list[str]) -> None:
    """SYNCPOINT: everything for one claim commits atomically."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO claims VALUES (?,?,?,?)", (claim_id, idem_key, _dumps(redacted), now()))
        conn.executemany("INSERT OR IGNORE INTO token_vault VALUES (?,?,?)", vault)
        conn.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)", (
            claim_id, decision["band"], _dumps(decision["reasons"]), decision.get("model_version"),
            decision["thresholds_version"], decision["policy_engine"],
            _dumps(signals) if signals is not None else None, _dumps(decision["timings_ms"])))
        journal(conn, claim_id, "DECISION", {k: decision[k] for k in (
            "band", "reasons", "model_version", "thresholds_version", "policy_engine", "timings_ms")})
        conn.execute("INSERT INTO egress VALUES (?,?,?,?,?)",
                     (egress_id, claim_id, _dumps(egress_payload), _dumps(egress_dropped), now()))
        journal(conn, claim_id, "EGRESS", {"egress_id": egress_id, "fields": sorted(egress_payload),
                                           "dropped": egress_dropped})
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
              redacted: dict[str, Any] | None) -> None:
    """After-decision update from Granite. Never touches the decision; journaled as new rows."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany("INSERT OR IGNORE INTO token_vault VALUES (?,?,?)", vault)
        if redacted is not None:
            conn.execute("UPDATE claims SET redacted_json = ? WHERE claim_id = ?", (_dumps(redacted), claim_id))
        conn.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?)",
                     (claim_id, status, note, _dumps(pii_added), now()))
        journal(conn, claim_id, "PII_SECOND_PASS", {"tokens_added": pii_added})
        journal(conn, claim_id, "NOTE", {"status": status})
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def get_claim(conn: sqlite3.Connection, claim_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT redacted_json FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
    return json.loads(row[0]) if row else None


def get_note(conn: sqlite3.Connection, claim_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT status, note FROM notes WHERE claim_id = ?", (claim_id,)).fetchone()
    return {"status": row["status"], "note": row["note"]} if row else None


def get_egress(conn: sqlite3.Connection, claim_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT egress_id, payload_json, dropped_json FROM egress WHERE claim_id = ?",
                       (claim_id,)).fetchone()
    if not row:
        return None
    return {"egress_id": row[0], "payload": json.loads(row[1]), "dropped": json.loads(row[2])}


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
