"""Asset-verification and smoke-guardrail tests. No real weights or inference."""
import hashlib
import io
import json

import pytest

from linuxone.provision_granite import download_verified, provision, verify_file
from llm.real_smoke import DISCLAIMER, SmokeFailure, apply_findings, check_note, metrics, run_smoke


def test_download_is_atomic_and_checks_hash_and_be_header(tmp_path):
    fixture = b"GGUF\x00\x00\x00\x03test-fixture-not-a-model"
    target = tmp_path / "granite.gguf"
    digest = hashlib.sha256(fixture).hexdigest()
    seen = []

    def opener(request, timeout):
        seen.append(request.full_url)
        return io.BytesIO(fixture)

    result = download_verified("https://example.invalid/fixture", target, len(fixture), digest,
                               "4747554600000003", opener=opener)
    assert result["verified"] is True
    assert target.read_bytes() == fixture
    assert seen == ["https://example.invalid/fixture"]
    assert not list(tmp_path.glob("*.part"))


def test_bad_download_never_becomes_the_model(tmp_path):
    fixture = b"GGUF\x00\x00\x00\x03bad-fixture"
    target = tmp_path / "granite.gguf"
    with pytest.raises(ValueError, match="SHA-256"):
        download_verified("https://example.invalid/fixture", target, len(fixture), "0" * 64,
                          "4747554600000003", opener=lambda *args, **kwargs: io.BytesIO(fixture))
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_little_endian_header_is_rejected(tmp_path):
    fixture = b"GGUF\x03\x00\x00\x00fixture"
    target = tmp_path / "granite.gguf"
    target.write_bytes(fixture)
    with pytest.raises(ValueError, match="big-endian"):
        verify_file(target, len(fixture), hashlib.sha256(fixture).hexdigest(), "4747554600000003")


def test_existing_model_is_not_overwritten(tmp_path):
    target = tmp_path / "granite.gguf"
    target.write_bytes(b"existing-model-fixture")
    with pytest.raises(ValueError, match="size"):
        download_verified("https://example.invalid/fixture", target, 12345, "0" * 64,
                          opener=lambda *args, **kwargs: pytest.fail("Must not download over an existing file"))
    assert target.read_bytes() == b"existing-model-fixture"


def test_offline_provenance_never_claims_inference(tmp_path):
    model = b"GGUF\x00\x00\x00\x03test-fixture-not-a-model"
    license_text = b"test licence fixture"
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "granite.gguf").write_bytes(model)
    (model_dir / "granite.LICENSE-2.0.txt").write_bytes(license_text)
    source = {"repository": "fixture/repo", "revision": "0" * 40, "filename": "fixture.gguf",
              "size_bytes": len(model), "sha256": hashlib.sha256(model).hexdigest(),
              "gguf_header_hex": "4747554600000003", "license_size_bytes": len(license_text),
              "license_sha256": hashlib.sha256(license_text).hexdigest()}
    report = provision(tmp_path, source, False)
    assert report["status"] == "verified_assets_only"
    assert report["runtime_validated"] is False
    assert json.loads((model_dir / "granite.provenance.json").read_text())["runtime_validated"] is False


def test_smoke_pii_cannot_accept_a_hallucinated_identifier():
    with pytest.raises(SmokeFailure, match="INVALID_PII_SPAN"):
        apply_findings("Bumper dented.", {"review_status": "model_second_pass",
                       "findings": [{"kind": "name", "text": "Invented Person"}]}, b"test")


def test_smoke_rejects_speculation_and_output_identity():
    valid = {"advisory": True, "changes_band": False,
             "bullets": ["Review the supplied evidence.", "Request the original file.", DISCLAIMER]}
    check_note(valid, ("Priya", "Shah"))
    for line in ("The claimant is lying.", "Priya Shah caused it.", "Approve this claim GREEN."):
        with pytest.raises(SmokeFailure, match="GUARDRAIL_FAILED"):
            check_note({**valid, "bullets": [line, "Request the original file.", DISCLAIMER]}, ("Priya", "Shah"))


def test_smoke_does_not_turn_unready_into_success():
    class UnreadyApi:
        def request(self, *args, **kwargs):
            return 503, {"model_loaded": False}

    result = run_smoke(UnreadyApi(), "0" * 64, 1, architecture="s390x")
    assert result["status"] == "not_ready"
    assert result["real_model_exercised"] is False
    assert result["cases"] == []


def test_smoke_metrics_require_an_actual_positive_generation():
    with pytest.raises(SmokeFailure, match="MISSING_REAL_GENERATION_METRICS"):
        metrics({"runtime": "local_llama_cpp", "generation_ms": 1, "completion_tokens": 0})
