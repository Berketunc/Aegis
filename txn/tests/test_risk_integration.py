"""Real gateway -> B ASGI integration, with no fabricated detector installed."""
import base64
import asyncio
from io import BytesIO
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from privacy import ALLOWED_FIELDS, pseudonym
from risk_service.app import create_app as create_risk_app
from risk_service.config import Settings as RiskSettings, read_thresholds
from risk_service.privacy import PrivacyAdapter
from txn.gateway.app import create_app as create_gateway_app
from txn.gateway.config import Settings

from .conftest import claim_body


def png_photo():
    buffer = BytesIO()
    Image.new("RGB", (800, 600), (60, 120, 180)).save(buffer, format="PNG")
    return buffer.getvalue()


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)
        self.risk_requests = []

    async def handle_async_request(self, request):
        assert request.headers["x-risk-secret"] == "integration-risk-secret"
        if request.url.path == "/risk/score":
            self.risk_requests.append(json.loads(request.content))
        return await self.inner.handle_async_request(request)


def test_actual_gateway_upload_and_missing_model_fail_safe(tmp_path, monkeypatch):
    risk_data = tmp_path / "risk-data"
    hmac_key = "stable-integration-hmac-key-123456789"
    monkeypatch.setenv("HMAC_KEY", hmac_key)
    monkeypatch.setenv("AEGIS_DATA_DIR", str(risk_data))
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text(json.dumps({"version": "t-integration-1", "tau_amber": 0.6, "tau_red": 0.9}))
    risk = create_risk_app(RiskSettings(
        data_dir=risk_data, secret="integration-risk-secret",
        evidence_key_b64=base64.b64encode(b"e" * 32).decode(),
        privacy_adapter="risk_service.c_privacy_adapter:process", thresholds_path=str(thresholds),
    ))
    transport = RecordingTransport(risk)
    gateway = create_gateway_app(Settings(
        risk_url="http://risk", risk_secret="integration-risk-secret", risk_timeout_ms=300,
        db_path=str(tmp_path / "gateway.sqlite3"), thresholds_path=str(thresholds),
        policy_engine="python", hmac_key=hmac_key.encode(), granite_url="", ui_dir=str(tmp_path / "no-ui"),
    ), transport=transport)
    with TestClient(risk) as internal, TestClient(gateway) as client:
        health = internal.get("/health").json()
        assert health["privacy"]["ready"] is True
        assert health["upload_ready"] is True
        assert health["model"]["ready"] is False
        assert health["thresholds_version"] == "t-integration-1"
        assert health["ready"] is False
        uploaded = client.post("/evidence", files={"file": ("synthetic.png", png_photo(), "image/png")})
        assert uploaded.status_code == 200, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]
        assert len(evidence_id) == 67  # The former C max64 contract rejected B IDs.
        decision = client.post("/claims", json=claim_body([evidence_id], key="real-integration"))
        assert decision.status_code == 200, decision.text
        result = decision.json()
        assert result["band"] == "AMBER"
        assert result["reasons"] == ["RISK_SERVICE_UNAVAILABLE"]
        assert result["thresholds_version"] == "t-integration-1"
        assert result["model_version"] is None
        assert result["timings_ms"]["risk"] < 600
        heatmap = client.post(f"/claims/{result['claim_id']}/heatmap")
        assert heatmap.status_code == 503
        assert "patch_scores" not in heatmap.text
        sent = transport.risk_requests[0]
        for raw in ["Alex", "Doe", "07700", "alex@example.com", "PN-4471-0098"]:
            assert raw not in json.dumps(sent)
        assert set(sent["privacy_context"]) == {"claim_id", "damage_category"}
        assert sent["policy_token"].startswith("POLICY_")
        replay = client.post("/claims", json=claim_body([evidence_id], key="real-integration")).json()
        assert replay["claim_id"] == result["claim_id"]
        assert replay["replayed"] is True
        assert len(transport.risk_requests) == 1


def test_shared_privacy_rules_create_real_durable_egress(tmp_path, monkeypatch):
    monkeypatch.setenv("HMAC_KEY", "stable-integration-hmac-key-123456789")
    monkeypatch.setenv("AEGIS_DATA_DIR", str(tmp_path))
    adapter = PrivacyAdapter("risk_service.c_privacy_adapter:process")
    assert adapter.ready
    result = adapter.process("Alex Doe: email alex@example.com, call 07700 900123.", {
        "claim_id": "CLM-private", "damage_category": "dent", "gazetteer": {"name": "Alex Doe"},
        "not_allowed": "must never be logged",
    })
    expected_token = pseudonym(b"stable-integration-hmac-key-123456789", "PERSON", "Alex Doe")
    assert expected_token in result["redacted_record"]["incident"]["description"]
    with sqlite3.connect(tmp_path / "privacy_egress.sqlite3") as db:
        row = db.execute("SELECT egress_payload_id, event, payload_json FROM prepared_egress").fetchone()
        assert row[0] == result["egress_payload_id"]
        assert row[1] == "PREPARED"
        assert set(json.loads(row[2])) <= set(ALLOWED_FIELDS)
        for raw in ["Alex", "Doe", "alex@example.com", "07700", "must never be logged"]:
            assert raw not in row[2]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM prepared_egress")


def test_builtin_privacy_requires_real_key(tmp_path, monkeypatch):
    monkeypatch.delenv("HMAC_KEY", raising=False)
    monkeypatch.setenv("AEGIS_DATA_DIR", str(tmp_path))
    assert PrivacyAdapter("risk_service.c_privacy_adapter:process").ready is False


@pytest.mark.parametrize("field", ["version", "thresholds_version"])
def test_b_and_c_read_same_threshold_alias(tmp_path, field):
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({field: "t-2", "tau_amber": 0.4, "tau_red": 0.8}))
    assert read_thresholds(path)["thresholds_version"] == "t-2"
    assert Settings(thresholds_path=str(path)).load_thresholds().version == "t-2"


def test_both_services_reject_conflicting_threshold_aliases(tmp_path):
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({"version": "t-1", "thresholds_version": "t-2", "tau_amber": 0.4, "tau_red": 0.8}))
    with pytest.raises(ValueError):
        read_thresholds(path)
    with pytest.raises(ValueError):
        Settings(thresholds_path=str(path)).load_thresholds()


def test_gateway_rejects_risk_threshold_version_mismatch(make_client):
    def response(request):
        if request.url.path == "/evidence":
            return httpx.Response(200, json={"evidence_id": "EV-one", "sha256": "test"})
        body = json.loads(request.content)
        return httpx.Response(200, json={"claim_id": body["claim_id"], "thresholds_version": "wrong-version",
                                         "model_version": "test-model", "signals": {"ai_score": 0.01}})
    client = make_client(transport=httpx.MockTransport(response))
    assert client.post("/evidence", files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")}).status_code == 200
    result = client.post("/claims", json=claim_body(["EV-one"])).json()
    assert result["band"] == "AMBER"
    assert "RISK_SERVICE_UNAVAILABLE" in result["reasons"]
    assert "THRESHOLDS_VERSION_MISMATCH" in result["reasons"]


def test_concurrent_same_idempotency_key_scores_once_and_cleans_lock(make_client):
    risk_claim_ids = []
    async def response(request):
        if request.url.path == "/evidence":
            return httpx.Response(200, json={"evidence_id": "EV-one", "sha256": "test"})
        body = json.loads(request.content)
        risk_claim_ids.append(body["claim_id"])
        await asyncio.sleep(0.03)  # Make both submit requests overlap at risk I/O.
        return httpx.Response(200, json={"claim_id": body["claim_id"], "model_version": "test-only-model",
                                         "signals": {"ai_score": 0.01}})
    client = make_client(transport=httpx.MockTransport(response))
    assert client.post("/evidence", files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")}).status_code == 200

    async def concurrent_submissions():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://gateway") as http:
            body = claim_body(["EV-one"], key="concurrent-same-key")
            return await asyncio.gather(http.post("/claims", json=body), http.post("/claims", json=body))

    responses = client.portal.call(concurrent_submissions)
    assert all(response.status_code == 200 for response in responses)
    first, second = [response.json() for response in responses]
    assert first["claim_id"] == second["claim_id"]
    assert first["band"] == second["band"] == "GREEN"
    assert sorted([first["replayed"], second["replayed"]]) == [False, True]
    assert risk_claim_ids == [first["claim_id"]]
    assert client.app.state.claim_locks == {}
    assert client.app.state.conn.execute("SELECT COUNT(*) FROM claim_seq").fetchone()[0] == 1
    replay = next(item for item in [first, second] if item["replayed"])
    assert "replay_timings_ms" in replay
    assert "precommit_total" in replay["timings_ms"]
    assert "total" not in replay["timings_ms"]


def test_response_latency_includes_commit_stored_latency_explicitly_does_not(client, monkeypatch):
    from txn.gateway import db
    original_commit = db.commit_decision
    def slow_commit(*args, **kwargs):
        time.sleep(0.02)
        return original_commit(*args, **kwargs)
    monkeypatch.setattr(db, "commit_decision", slow_commit)
    from .conftest import upload
    evidence_id = upload(client)
    response = client.post("/claims", json=claim_body([evidence_id])).json()
    assert response["timings_ms"]["commit"] >= 20
    assert response["timings_ms"]["total"] >= response["timings_ms"]["precommit_total"] + 19
    stored = json.loads(client.app.state.conn.execute("SELECT timings_json FROM decisions").fetchone()[0])
    assert "precommit_total" in stored
    assert "total" not in stored
    journal = client.get("/journal", params={"claim_id": response["claim_id"]}).json()
    assert journal[0]["payload_json"]["timings_ms"] == stored
