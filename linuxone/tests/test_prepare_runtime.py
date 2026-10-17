import base64
import importlib.util
from pathlib import Path
import stat

import pytest

spec = importlib.util.spec_from_file_location("aegis_prepare_runtime", Path(__file__).parents[1] / "prepare_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def read_env(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def test_keys_stay_stable_and_are_separated(tmp_path):
    runtime.prepare(tmp_path)
    first = read_env(tmp_path / "services.env")
    (tmp_path / "data" / "existing-evidence").write_bytes(b"encrypted fixture")
    runtime.prepare(tmp_path, with_granite=True)
    second = read_env(tmp_path / "services.env")
    assert first == second
    assert len(base64.b64decode(second["AEGIS_EVIDENCE_KEY_B64"])) == 32
    gateway = read_env(tmp_path / "gateway.env")
    granite = read_env(tmp_path / "granite.env")
    assert gateway["HMAC_KEY"] == second["HMAC_KEY"]
    assert gateway["RISK_SECRET"] == second["AEGIS_RISK_SECRET"]
    assert gateway["GRANITE_URL"] == "http://aegis-granite:8001"
    assert "AEGIS_EVIDENCE_KEY_B64" not in gateway
    assert "AEGIS_EVIDENCE_KEY_B64" not in granite
    assert "HMAC_KEY" not in granite
    assert granite["RISK_SHARED_SECRET"] == second["AEGIS_RISK_SECRET"]
    for name in ("services.env", "gateway.env", "granite.env"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_legacy_configuration_migrates_without_rotating_evidence_key(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "existing").write_bytes(b"encrypted fixture")
    original = {"AEGIS_RISK_SECRET": "existing-shared-secret",
                "RISK_SHARED_SECRET": "existing-shared-secret",
                "AEGIS_EVIDENCE_KEY_B64": base64.b64encode(bytes(range(32))).decode(),
                "AEGIS_PRIVACY_ADAPTER": "privacy_adapter:process",
                "GRANITE_PII_PROMPT_PATH": "/prompts/pii.txt",
                "GRANITE_NOTE_PROMPT_PATH": "/prompts/note.txt",
                "AEGIS_MODEL_DEVICE": "nnpa"}
    runtime.save_env(tmp_path / "services.env", original)
    runtime.prepare(tmp_path)
    current = read_env(tmp_path / "services.env")
    for name in ("AEGIS_RISK_SECRET", "RISK_SHARED_SECRET", "AEGIS_EVIDENCE_KEY_B64", "AEGIS_MODEL_DEVICE"):
        assert current[name] == original[name]
    assert current["AEGIS_PRIVACY_ADAPTER"] == "risk_service.c_privacy_adapter:process"
    assert current["GRANITE_PII_PROMPT_PATH"] == "/prompts/pii_second_pass.txt"
    assert (tmp_path / "data" / "existing").read_bytes() == b"encrypted fixture"


@pytest.mark.parametrize("directory", ["data", "gateway"])
def test_existing_data_without_keys_is_not_reinitialized(tmp_path, directory):
    (tmp_path / directory).mkdir()
    (tmp_path / directory / "existing").write_bytes(b"persisted data")
    with pytest.raises(ValueError, match="restore"):
        runtime.prepare(tmp_path)
    assert not (tmp_path / "services.env").exists()


def test_invalid_saved_key_is_not_replaced(tmp_path):
    runtime.save_env(tmp_path / "services.env", {"AEGIS_RISK_SECRET": "old-secret", "AEGIS_EVIDENCE_KEY_B64": "invalid"})
    before = (tmp_path / "services.env").read_bytes()
    with pytest.raises(ValueError, match="restore"):
        runtime.prepare(tmp_path)
    assert (tmp_path / "services.env").read_bytes() == before


def test_custom_configuration_is_preserved(tmp_path):
    runtime.prepare(tmp_path)
    configured = read_env(tmp_path / "services.env")
    configured["AEGIS_PRIVACY_ADAPTER"] = "custom_adapter:process"
    configured["GRANITE_NOTE_PROMPT_PATH"] = "/prompts/custom.txt"
    runtime.save_env(tmp_path / "services.env", configured)
    runtime.prepare(tmp_path)
    assert read_env(tmp_path / "services.env") == configured


def test_existing_gateway_vault_without_hmac_key_is_not_rekeyed(tmp_path):
    runtime.prepare(tmp_path)
    configured = read_env(tmp_path / "services.env")
    del configured["HMAC_KEY"]
    runtime.save_env(tmp_path / "services.env", configured)
    (tmp_path / "gateway" / "clmi.db").write_bytes(b"existing token vault")
    before = (tmp_path / "services.env").read_bytes()
    with pytest.raises(ValueError, match="restore"):
        runtime.prepare(tmp_path)
    assert (tmp_path / "services.env").read_bytes() == before
