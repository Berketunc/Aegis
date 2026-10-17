import base64
from io import BytesIO
import json
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from risk_service.app import create_app
from risk_service.config import Settings
from risk_service.detector import Detector
from risk_service.dup import EvidenceIndex, hamming_distance
from risk_service.precompute import InvalidImage, PHASH_VERSION, PREPROCESS_VERSION, PreparedImage, prepare_image
from risk_service.privacy import PrivacyAdapter, PrivacyUnavailable
from risk_service.storage import EvidenceStore


def image_bytes(size=(800, 400), exif=None):
    stream = BytesIO()
    kwargs = {"exif": exif} if exif else {}
    Image.new("RGB", size, (180, 60, 20)).save(stream, "JPEG", **kwargs)
    return stream.getvalue()


def test_preprocessing_applies_orientation_and_has_deterministic_crops():
    exif = Image.Exif()
    exif[274] = 6  # Camera rotation 90 degrees clockwise.
    exif[305] = "Adobe Photoshop"
    prepared = prepare_image(image_bytes(exif=exif))
    assert prepared.crops.shape == (3, 3, 224, 224)
    assert prepared.crops.dtype == np.uint8
    assert prepared.crop_boxes == [[208, 528, 432, 752], [104, 264, 328, 488], [312, 792, 536, 1016]]
    assert len(prepared.phash) == 16
    assert prepared.metadata["exif_present"] is True
    assert prepared.metadata["software_tag"] == "Adobe Photoshop"
    assert prepared.metadata["c2pa_manifest"] is False


def test_unsafe_image_rejected():
    with pytest.raises(InvalidImage):
        prepare_image(b"not an image")
    with pytest.raises(InvalidImage):
        prepare_image(image_bytes((1000, 10)))


def test_encryption_reused_evidence_and_non_pickle_crops(tmp_path):
    key = b"x" * 32
    index = EvidenceIndex(tmp_path)
    store = EvidenceStore(tmp_path, index, key)
    raw = image_bytes()
    result = store.put(raw)
    assert store.put(raw)["evidence_id"] == result["evidence_id"]
    stored = (tmp_path / "objects" / result["sha256"] / "original.aesgcm").read_bytes()
    assert raw not in stored
    assert AESGCM(key).decrypt(stored[7:19], stored[19:], result["sha256"].encode()) == raw
    assert store.load_crops(index.get(result["evidence_id"])).shape == (3, 3, 224, 224)
    with pytest.raises(Exception):
        EvidenceStore(tmp_path, index, b"y" * 32).put(raw)


def add_hash(index, evidence_id, perceptual):
    prepared = PreparedImage(np.zeros((3, 3, 224, 224), dtype=np.uint8), perceptual, {}, [])
    index.add(evidence_id, evidence_id, prepared, PREPROCESS_VERSION, PHASH_VERSION)


def test_duplicate_boundary_own_claim_exclusion_and_persistence(tmp_path):
    index = EvidenceIndex(tmp_path)
    add_hash(index, "EV-one", "0000000000000000")
    add_hash(index, "EV-eight", "00000000000000ff")
    add_hash(index, "EV-nine", "00000000000001ff")
    assert hamming_distance("0000000000000000", "00000000000000ff") == 8
    assert index.check_and_register("CLM-one", ["EV-one"]) == []
    assert index.check_and_register("CLM-one", ["EV-one"]) == []
    reopened = EvidenceIndex(tmp_path)
    matches = reopened.check_and_register("CLM-eight", ["EV-eight"])
    assert matches[0]["claim_id"] == "CLM-one"
    assert matches[0]["hamming_distance"] == 8
    # Nine-bit difference from CLM-one must not match that claim.
    matches = reopened.check_and_register("CLM-nine", ["EV-nine"])
    assert all(match["claim_id"] != "CLM-one" for match in matches)


@pytest.fixture
def service(tmp_path):
    config = Settings(data_dir=tmp_path, secret="local-test-secret", evidence_key_b64=base64.b64encode(b"z" * 32).decode())
    app = create_app(config)
    with TestClient(app) as client:
        yield app, client


HEADERS = {"X-Risk-Secret": "local-test-secret"}


def upload(client):
    response = client.post("/evidence", headers=HEADERS, files={"file": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 200
    return response.json()["evidence_id"]


def test_no_fake_scoring_while_model_missing(service):
    app, client = service
    health = client.get("/health").json()
    assert health["ready"] is False
    assert health["upload_ready"] is True
    assert health["model"]["error"] == "model_not_configured"
    evidence_id = upload(client)
    response = client.post("/risk/score", headers=HEADERS, json={"claim_id": "CLM-one", "description": "dent", "evidence": [{"evidence_id": evidence_id}]})
    assert response.status_code == 503
    assert "ai_score" not in response.text


def test_authentication_and_invalid_upload(service):
    app, client = service
    assert client.post("/evidence", files={"file": ("image.jpg", image_bytes())}).status_code == 401
    assert client.post("/evidence", headers=HEADERS, files={"file": ("image.jpg", b"bad image")}).status_code == 415
    app.state.config.max_upload_bytes = 20
    assert client.post("/evidence", headers=HEADERS, files={"file": ("image.jpg", image_bytes())}).status_code == 413


class StubDetector:
    """Only tests orchestration; never included as a deployable detector."""
    ready = True
    version = "test-model"
    device = "cpu"
    artifact_sha256 = "test-artifact"
    error = None

    def score(self, crops):
        assert crops.shape == (3, 3, 224, 224)
        return {"ai_score": 0.6, "mean_logit": 0.4}


class StubPrivacy:
    ready = True
    error = None

    def process(self, description, context):
        return {"redacted_record": {"description": "PERSON_123 has a dent"}, "egress_payload_id": "EG-test"}


def configured_stubs(app):
    app.state.detector = StubDetector()
    app.state.privacy = StubPrivacy()
    app.state.thresholds = {"thresholds_version": "test-t-1", "tau_amber": 0.5, "tau_red": 0.9}


def test_orchestration_redacted_output_and_cross_claim_duplicate(service):
    app, client = service
    configured_stubs(app)
    evidence_id = upload(client)
    body = {"claim_id": "CLM-one", "description": "Alice Example has a dent", "evidence": [{"evidence_id": evidence_id}]}
    first = client.post("/risk/score", headers=HEADERS, json=body)
    assert first.status_code == 200
    assert first.json()["signals"]["duplicate_of"] is None
    assert "Alice Example" not in first.text
    assert "band" not in first.json()
    assert all(value >= 0 for value in first.json()["timings_ms"].values())
    retry = client.post("/risk/score", headers=HEADERS, json=body)
    assert retry.json()["signals"]["duplicate_of"] is None
    body["claim_id"] = "CLM-two"
    second = client.post("/risk/score", headers=HEADERS, json=body)
    assert second.json()["signals"]["duplicate_of"] == "CLM-one"
    assert second.json()["signals"]["duplicate_matches"][0]["hamming_distance"] == 0
    assert second.json()["thresholds_version"] == "test-t-1"


def test_privacy_missing_and_inference_failure_are_unavailable(service):
    app, client = service
    configured_stubs(app)
    evidence_id = upload(client)
    body = {"claim_id": "CLM-one", "description": "secret text", "evidence": [{"evidence_id": evidence_id}]}
    app.state.privacy = PrivacyAdapter(None)
    assert client.post("/risk/score", headers=HEADERS, json=body).status_code == 503
    app.state.privacy = StubPrivacy()
    def broken(_):
        raise RuntimeError("sensitive internal error")
    app.state.detector.score = broken
    result = client.post("/risk/score", headers=HEADERS, json=body)
    assert result.status_code == 503
    assert "sensitive" not in result.text
    assert "secret text" not in result.text
    # Failed processing cannot introduce a phantom duplicate for another claim.
    with app.state.index.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0] == 0


def test_privacy_runtime_failure_does_not_register_claim(service):
    app, client = service
    configured_stubs(app)
    evidence_id = upload(client)
    def broken_privacy(description, context):
        raise RuntimeError("privacy adapter failed")
    app.state.privacy.process = broken_privacy
    result = client.post("/risk/score", headers=HEADERS, json={
        "claim_id": "CLM-one", "description": "private", "evidence": [{"evidence_id": evidence_id}],
    })
    assert result.status_code == 503
    with app.state.index.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0] == 0


def test_privacy_adapter_rejects_incomplete_contract():
    adapter = PrivacyAdapter(None)
    adapter.ready = True
    adapter.function = lambda **kwargs: {"redacted_record": {}}
    with pytest.raises(PrivacyUnavailable):
        adapter.process("private text", {})


def test_unconfigured_detector_does_not_import_torch():
    detector = Detector(None)
    assert detector.ready is False
    assert detector.error == "model_not_configured"
