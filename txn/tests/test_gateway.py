import asyncio
import json
import sqlite3

import httpx
import pytest

from .conftest import JPEG, PNG, claim_body, upload


def test_green_claim_end_to_end(client):
    ev = upload(client)
    r = client.post("/claims", json=claim_body([ev]))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["band"] == "GREEN" and d["reasons"] == ["NONE"]
    assert d["claim_id"].startswith("CLM-")
    assert d["thresholds_version"] == "t-dev" and d["model_version"] == "stub-0"
    assert set(d["timings_ms"]) >= {"gateway", "risk", "policy", "total"}
    events = [j["event"] for j in client.get("/journal", params={"claim_id": d["claim_id"]}).json()]
    assert events == ["DECISION", "EGRESS"]


@pytest.mark.parametrize("name,band,reason", [
    ("fake_sdxl.jpg", "RED", "AI_SCORE_ABOVE_RED"),
    ("inflated.jpg", "AMBER", "AI_SCORE_ABOVE_AMBER"),
    ("gimp_export.jpg", "AMBER", "EDITING_SOFTWARE_TAG"),
])
def test_bands(client, name, band, reason):
    ev = upload(client, name, JPEG + name.encode())
    d = client.post("/claims", json=claim_body([ev])).json()
    assert d["band"] == band and reason in d["reasons"]


def test_missing_exif_is_informational_only(client):
    ev = upload(client, "noexif.jpg")
    d = client.post("/claims", json=claim_body([ev])).json()
    assert d["band"] == "GREEN" and d["reasons"] == ["INFO_EXIF_MISSING"]


def test_duplicate_photo_on_another_claim_is_red(client):
    """Admiral's pattern: the same damage photo re-used on a second claim."""
    ev = upload(client, "door.jpg")
    first = client.post("/claims", json=claim_body([ev], key="k1")).json()
    second = client.post("/claims", json=claim_body([ev], key="k2")).json()
    assert first["band"] == "GREEN"
    assert second["band"] == "RED" and "DUPLICATE_PHOTO" in second["reasons"]


def test_same_claim_twice_is_idempotent(client, stub):
    ev = upload(client)
    body = claim_body([ev], key="same-key")
    a = client.post("/claims", json=body).json()
    stub._state["mode"] = "fail"  # a replay that re-scored would now come back AMBER
    b = client.post("/claims", json=body).json()
    c = client.post("/claims", json=claim_body([ev]), headers={"Idempotency-Key": "same-key"}).json()
    assert a["claim_id"] == b["claim_id"] == c["claim_id"] and a["band"] == b["band"]
    assert (a["replayed"], b["replayed"], c["replayed"]) == (False, True, True)
    events = [j["event"] for j in client.get("/journal", params={"claim_id": a["claim_id"]}).json()]
    assert events == ["DECISION", "EGRESS", "REPLAY", "REPLAY"]


def test_concurrent_duplicate_commit_returns_first_decision(client):
    """Two requests with one key both reach commit: the loser returns the winner's decision."""
    from txn.gateway import db
    ev = upload(client)
    conn = client.app.state.conn
    first = client.post("/claims", json=claim_body([ev], key="race")).json()
    with pytest.raises(db.DuplicateKey):
        db.commit_decision(conn, claim_id=db.reserve_claim_id(conn), idem_key="race", redacted={}, vault=[],
                           decision={"band": "GREEN", "reasons": ["NONE"], "thresholds_version": "t-dev",
                                     "policy_engine": "python", "timings_ms": {}},
                           signals=None, egress_id="EG-x", egress_payload={}, egress_dropped=[])
    assert db.find_by_key(conn, "race")["claim_id"] == first["claim_id"]


def test_no_photo_or_unknown_photo_is_rejected_and_nothing_committed(client):
    assert client.post("/claims", json=claim_body([])).status_code == 400
    r = client.post("/claims", json=claim_body(["EV-NOPE"]))
    assert r.status_code == 400 and "EV-NOPE" in r.json()["detail"]
    assert client.app.state.conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["fail", "slow", "garbage"])
def test_risk_service_failure_is_amber_never_green(client, stub, mode):
    ev = upload(client)
    stub._state["mode"] = mode
    d = client.post("/claims", json=claim_body([ev])).json()
    assert d["band"] == "AMBER" and d["reasons"] == ["RISK_SERVICE_UNAVAILABLE"]
    assert d["timings_ms"]["risk"] < 600  # 300 ms deadline, not the stub's 1 s sleep


def test_risk_service_unreachable_is_amber(make_client, stub):
    def down(request):
        if request.url.path == "/evidence":
            return httpx.Response(200, json={"evidence_id": "EV-1", "sha256": "x"})
        raise httpx.ConnectError("connection refused")
    c = make_client(transport=httpx.MockTransport(down))
    upload(c)
    d = c.post("/claims", json=claim_body(["EV-1"])).json()
    assert d["band"] == "AMBER" and d["reasons"] == ["RISK_SERVICE_UNAVAILABLE"]


def test_risk_response_for_wrong_claim_is_rejected(make_client):
    def wrong(request):
        if request.url.path == "/evidence":
            return httpx.Response(200, json={"evidence_id": "EV-1", "sha256": "x"})
        return httpx.Response(200, json={"claim_id": "CLM-999999", "signals": {"ai_score": 0.01}})
    c = make_client(transport=httpx.MockTransport(wrong))
    upload(c)
    assert c.post("/claims", json=claim_body(["EV-1"])).json()["band"] == "AMBER"


@pytest.mark.parametrize("name,band", [("door.jpg", "GREEN"), ("inflated.jpg", "AMBER"), ("fake.jpg", "RED")])
def test_gateway_on_real_cobol_engine(make_client, cobol_bin, name, band):
    c = make_client(policy_engine="cobol", clmipol_bin=cobol_bin)
    ev = upload(c, name, JPEG + name.encode())
    d = c.post("/claims", json=claim_body([ev])).json()
    assert (d["band"], d["policy_engine"]) == (band, "cobol")


def test_cobol_crash_is_amber(make_client, tmp_path):
    broken = tmp_path / "CLMIPOL"
    broken.write_text("not a binary")
    c = make_client(policy_engine="cobol", clmipol_bin=str(broken))
    ev = upload(c)
    d = c.post("/claims", json=claim_body([ev])).json()
    assert d["band"] == "AMBER" and d["reasons"] == ["POLICY_ENGINE_ERROR"]


def test_unreadable_thresholds_is_amber(make_client, tmp_path):
    c = make_client(thresholds_path=str(tmp_path / "missing.json"))
    ev = upload(c)
    d = c.post("/claims", json=claim_body([ev])).json()
    assert d["band"] == "AMBER" and d["reasons"] == ["THRESHOLDS_UNAVAILABLE"]


def test_upload_limits(client):
    big = JPEG + b"\x00" * (1024 * 1024)
    assert client.post("/evidence", files={"file": ("big.jpg", big, "image/jpeg")}).status_code == 413
    assert client.post("/evidence", files={"file": ("x.gif", b"GIF89a....", "image/gif")}).status_code == 415
    assert client.post("/evidence", files={"file": ("e.jpg", b"", "image/jpeg")}).status_code == 400
    assert client.post("/evidence", files={"file": ("p.png", PNG, "image/png")}).status_code == 200


def test_journal_is_append_only(client):
    ev = upload(client)
    client.post("/claims", json=claim_body([ev]))
    conn = client.app.state.conn
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE journal SET event = 'X'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM journal")


def test_stored_record_and_egress_hold_no_raw_pii(client):
    ev = upload(client)
    d = client.post("/claims", json=claim_body([ev])).json()
    conn = client.app.state.conn
    stored = conn.execute("SELECT redacted_json FROM claims").fetchone()[0]
    egress = conn.execute("SELECT payload_json FROM egress").fetchone()[0]
    journal = " ".join(r[0] for r in conn.execute("SELECT payload_json FROM journal"))
    for raw in ("Alex", "Doe", "alex@example.com", "07700", "PN-4471-0098"):
        assert raw not in stored and raw not in egress and raw not in journal
    assert set(json.loads(egress)) <= {"description_redacted", "damage_category", "band", "ai_score",
                                       "exif_present", "software_tag_present", "c2pa_manifest",
                                       "duplicate_detected"}
    assert d["band"] == json.loads(egress)["band"]


def test_who_sees_what(client):
    ev = upload(client)
    cid = client.post("/claims", json=claim_body([ev])).json()["claim_id"]
    adj = client.get(f"/claims/{cid}/views", headers={"X-Role": "adjuster"}).json()
    assert adj["original"] is None and "Alex" not in json.dumps(adj)
    auth = client.get(f"/claims/{cid}/views", headers={"X-Role": "authorised"}).json()
    assert auth["original"]["claimant"]["name"] == "Alex Doe"
    assert "07700 900123" in auth["original"]["incident"]["description"]
    events = [j["event"] for j in client.get("/journal", params={"claim_id": cid}).json()]
    assert events[-1] == "VIEW_ORIGINAL"


def test_get_claim_and_404(client):
    ev = upload(client)
    cid = client.post("/claims", json=claim_body([ev])).json()["claim_id"]
    assert client.get(f"/claims/{cid}").json()["adjuster_note"]["status"] == "not_requested"
    assert client.get("/claims/CLM-999999").status_code == 404
    assert client.get("/health").json()["status"] == "ok"
