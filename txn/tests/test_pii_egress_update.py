import json

from txn.gateway import db


def test_second_pass_redaction_updates_egress_without_changing_decision(tmp_path):
    conn = db.connect(str(tmp_path / "claims.db"))
    try:
        decision = {"band": "AMBER", "reasons": ["AI_SCORE_ABOVE_AMBER"],
                    "model_version": "fixture", "thresholds_version": "fixture",
                    "policy_engine": "python", "timings_ms": {"precommit_total": 1.0}}
        original = {"incident": {"description": "Ask Missedname about the bumper."}}
        db.commit_decision(conn, claim_id="CLM-000001", idem_key="fixture", redacted=original,
                           vault=[], decision=decision, signals={}, egress_id="EG-000001",
                           egress_payload={"description_redacted": original["incident"]["description"], "band": "AMBER"},
                           egress_dropped=["policy_number"])
        before = db.get_decision(conn, "CLM-000001")
        updated = {"incident": {"description": "Ask PERSON_abcdef01 about the bumper."}}
        db.save_note(conn, "CLM-000001", "ready", "- Advisory only.", ["PERSON_abcdef01"],
                     [("PERSON_abcdef01", "PERSON", "Missedname")], updated)
        assert db.get_decision(conn, "CLM-000001") == before
        assert db.get_egress(conn, "CLM-000001")["payload"]["description_redacted"] == updated["incident"]["description"]
        assert "Missedname" not in json.dumps(db.get_claim(conn, "CLM-000001"))
        assert "Missedname" not in json.dumps(db.get_egress(conn, "CLM-000001"))
        assert db.get_egress(conn, "CLM-000001")["dropped"] == ["policy_number"]
        assert any(row["event"] == "EGRESS_PII_UPDATED" for row in db.read_journal(conn, "CLM-000001"))
    finally:
        conn.close()
