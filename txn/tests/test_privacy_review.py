"""Free text stays quarantined until an enabled second pass succeeds."""
import json

import pytest

from txn.gateway import db, granite
from .conftest import claim_body, upload


def result(privacy_status="unavailable", *, redacted=None):
    return {"status": "unavailable", "note": None, "pii_added": [], "vault": [],
            "redacted": redacted, "privacy_review_status": privacy_status}


def test_disabled_review_is_explicitly_rules_only(client):
    evidence = upload(client)
    decision = client.post("/claims", json=claim_body([evidence])).json()
    assert decision["privacy_review"] == {"status": "rules_only", "reason": "granite_disabled", "description_released": True}
    views = client.get(f"/claims/{decision['claim_id']}/views").json()
    assert views["pseudonymised"]["incident"]["description"]
    assert views["privacy_review"] == decision["privacy_review"]


def test_failed_review_hides_description_and_category_but_authorised_role_can_access(make_client, monkeypatch):
    async def failed_review(**kwargs):
        return result()
    monkeypatch.setattr(granite, "run_job", failed_review)
    client = make_client(granite_url="http://granite")
    evidence = upload(client, "fake.jpg")
    body = claim_body([evidence], description="Unknown witness Missedname saw the collision.")
    body["damage_category"] = "dent near Missedname"
    submitted = client.post("/claims", json=body).json()
    claim_id = submitted["claim_id"]
    assert submitted["privacy_review"]["status"] == "pending"
    got = client.get(f"/claims/{claim_id}").json()
    assert got["privacy_review"]["status"] == "unavailable"
    assert got["privacy_review"]["description_released"] is False
    assert got["band"] == submitted["band"] == "RED"
    views = client.get(f"/claims/{claim_id}/views").json()
    assert views["pseudonymised"]["incident"]["description"] is None
    assert views["pseudonymised"]["damage_category"] is None
    assert "description_redacted" not in views["egress"]["payload"]
    assert "damage_category" not in views["egress"]["payload"]
    assert "Missedname" not in json.dumps(views)
    # The export-candidate table itself never contained pending free text.
    payload = json.loads(client.app.state.conn.execute("SELECT payload_json FROM egress").fetchone()[0])
    assert "description_redacted" not in payload and "damage_category" not in payload
    authorised = client.get(f"/claims/{claim_id}/views", headers={"X-Role": "authorised"}).json()
    assert "Missedname" in authorised["original"]["incident"]["description"]
    assert authorised["pseudonymised"]["incident"]["description"] is None
    assert any(event["event"] == "VIEW_ORIGINAL" for event in client.get("/journal", params={"claim_id": claim_id}).json())


def test_successful_pii_review_releases_text_even_if_note_fails(make_client, monkeypatch):
    async def reviewed_without_note(**kwargs):
        return result("reviewed")
    monkeypatch.setattr(granite, "run_job", reviewed_without_note)
    client = make_client(granite_url="http://granite")
    evidence = upload(client, "fake.jpg")
    decision = client.post("/claims", json=claim_body([evidence], description="Bumper dented by a bollard.")).json()
    got = client.get(f"/claims/{decision['claim_id']}").json()
    assert got["privacy_review"]["status"] == "reviewed"
    assert got["adjuster_note"]["status"] == "unavailable"
    views = client.get(f"/claims/{decision['claim_id']}/views").json()
    assert views["pseudonymised"]["incident"]["description"] == "Bumper dented by a bollard."
    assert views["egress"]["payload"]["description_redacted"] == "Bumper dented by a bollard."
    assert got["band"] == decision["band"]


def test_pending_view_hides_text_with_legacy_egress_rows(client):
    evidence = upload(client)
    decision = client.post("/claims", json=claim_body([evidence])).json()
    claim_id = decision["claim_id"]
    # A view must defend against an older persisted egress candidate too.
    client.app.state.conn.execute("UPDATE privacy_reviews SET status='pending', reason='fixture' WHERE claim_id=?", (claim_id,))
    views = client.get(f"/claims/{claim_id}/views").json()
    assert views["privacy_review"]["status"] == "pending"
    assert views["pseudonymised"]["incident"]["description"] is None
    assert "description_redacted" not in views["egress"]["payload"]


def test_restart_marks_lost_pending_review_unavailable(tmp_path):
    path = str(tmp_path / "claims.db")
    conn = db.connect(path)
    decision = {"band": "AMBER", "reasons": [], "model_version": "test", "thresholds_version": "test",
                "policy_engine": "python", "timings_ms": {}}
    db.commit_decision(conn, claim_id="CLM-one", idem_key="one", redacted={"incident": {"description": "Missedname"}},
                       vault=[], decision=decision, signals={}, egress_id="EG-one",
                       egress_payload={"description_redacted": "Missedname"}, egress_dropped=[],
                       privacy_status="pending", privacy_reason="second_pass_queued")
    conn.close()
    reopened = db.connect(path)
    try:
        review = db.get_privacy_review(reopened, "CLM-one")
        assert review["status"] == "unavailable"
        assert review["reason"] == "worker_restarted"
        assert db.get_claim(reopened, "CLM-one")["incident"]["description"] is None
        assert "description_redacted" not in db.get_egress(reopened, "CLM-one")["payload"]
    finally:
        reopened.close()
