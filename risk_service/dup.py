"""Persistent evidence metadata and cross-claim perceptual-hash matching."""
import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


class EvidenceIndex:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = root / "index.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    sha256 TEXT UNIQUE NOT NULL,
                    phash TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    preprocessing_version TEXT NOT NULL,
                    phash_version TEXT NOT NULL,
                    crop_boxes TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS claim_evidence (
                    claim_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
                    policy_token TEXT,
                    PRIMARY KEY (claim_id, evidence_id)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def add(self, evidence_id, sha256, prepared, preprocessing_version, phash_version):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)", (
                evidence_id, sha256, prepared.phash, json.dumps(prepared.metadata),
                preprocessing_version, phash_version, json.dumps(prepared.crop_boxes),
            ))

    def get(self, evidence_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        return dict(row) if row else None

    def belongs_to_claim(self, claim_id, evidence_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM claim_evidence WHERE claim_id=? AND evidence_id=?",
                              (claim_id, evidence_id)).fetchone() is not None

    def check_and_register(self, claim_id, evidence_ids, policy_token=None):
        """One atomic check+registration avoids concurrent claims missing each other.

        Call only after detector and privacy processing succeed. Retrying a
        claim never matches its own earlier upload.
        policy_token must be C's opaque pseudonym, never a raw policy number.
        """
        matches = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            candidates = db.execute("""
                SELECT ce.claim_id, ce.evidence_id, ce.policy_token, e.phash
                FROM claim_evidence ce JOIN evidence e USING(evidence_id)
                WHERE ce.claim_id != ?
                ORDER BY ce.claim_id, ce.evidence_id
            """, (claim_id,)).fetchall()
            for evidence_id in evidence_ids:
                row = db.execute("SELECT phash FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
                if row is None:
                    raise KeyError(evidence_id)
                for other in candidates:
                    distance = hamming_distance(row["phash"], other["phash"])
                    if distance <= 8:
                        matches.append({"claim_id": other["claim_id"], "evidence_id": other["evidence_id"],
                                        "submitted_evidence_id": evidence_id, "hamming_distance": distance})
                db.execute("INSERT OR IGNORE INTO claim_evidence VALUES (?, ?, ?)", (claim_id, evidence_id, policy_token))
        matches.sort(key=lambda item: (item["hamming_distance"], item["claim_id"], item["evidence_id"]))
        return matches
