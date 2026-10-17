#!/usr/bin/env python3
"""Create/migrate private runtime configuration without rotating existing keys."""
import argparse
import base64
import os
from pathlib import Path
import secrets
import tempfile


def save_env(path, values):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for key, value in values.items():
                if "\n" in value or "\r" in value:
                    raise ValueError("Runtime environment values must be single-line")
                stream.write(f"{key}={value}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    path.chmod(0o600)


def prepare(root, with_granite=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in ("data", "models", "integrations", "prompts", "gateway"):
        (root / directory).mkdir(exist_ok=True, mode=0o700)
    env = root / "services.env"
    values = {}
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator:
                raise ValueError("Invalid services.env line; restore the original runtime configuration")
            values[key] = value
    else:
        if any((root / "data").rglob("*")) or any((root / "gateway").rglob("*")):
            raise ValueError("Existing data has no services.env; restore its original encryption and HMAC keys")
        values["AEGIS_RISK_SECRET"] = secrets.token_urlsafe(32)
        values["AEGIS_EVIDENCE_KEY_B64"] = base64.b64encode(secrets.token_bytes(32)).decode()
    if not values.get("AEGIS_RISK_SECRET"):
        raise ValueError("AEGIS_RISK_SECRET is missing; restore the original configuration")
    try:
        key = base64.b64decode(values["AEGIS_EVIDENCE_KEY_B64"], validate=True)
        if len(key) != 32:
            raise ValueError
    except (KeyError, ValueError) as error:
        raise ValueError("The saved evidence key is missing or invalid; restore its original value") from error
    values.setdefault("RISK_SHARED_SECRET", values["AEGIS_RISK_SECRET"])
    if values["RISK_SHARED_SECRET"] != values["AEGIS_RISK_SECRET"]:
        raise ValueError("RISK_SHARED_SECRET must match AEGIS_RISK_SECRET")
    if "HMAC_KEY" not in values and any((root / "gateway").rglob("*")):
        raise ValueError("Existing gateway data has no saved HMAC_KEY; restore its original key")
    values.setdefault("HMAC_KEY", secrets.token_urlsafe(32))
    if len(values["HMAC_KEY"].encode()) < 32:
        raise ValueError("HMAC_KEY must contain at least 32 bytes; do not rotate an existing vault key")
    defaults = {
        "AEGIS_DATA_DIR": "/data",
        "AEGIS_MODEL_MANIFEST": "/models/manifest.json",
        "AEGIS_THRESHOLDS_PATH": "/models/thresholds.json",
        "AEGIS_MODEL_DEVICE": "cpu",
        # One thread put three ResNet-18 crops over the gateway's 300 ms deadline on
        # the VM; the stock benchmark reached ~105 ms with three threads.
        "AEGIS_TORCH_THREADS": str(max(1, min(4, os.cpu_count() or 1))),
        "AEGIS_PRIVACY_ADAPTER": "risk_service.c_privacy_adapter:process",
        "PYTHONPATH": "/app:/opt/aegis:/models:/integrations",
        "GRANITE_MODEL_PATH": "/models/granite.gguf",
        "GRANITE_PII_PROMPT_PATH": "/prompts/pii_second_pass.txt",
        "GRANITE_NOTE_PROMPT_PATH": "/prompts/adjuster_note.txt",
        "GRANITE_THREADS": "2", "GRANITE_CONTEXT": "1024",
    }
    for name, value in defaults.items():
        values.setdefault(name, value)
    # Migrate only the placeholder values in the original Person B package.
    replacements = {
        "AEGIS_TORCH_THREADS": ("1", defaults["AEGIS_TORCH_THREADS"]),
        "AEGIS_PRIVACY_ADAPTER": ("privacy_adapter:process", defaults["AEGIS_PRIVACY_ADAPTER"]),
        "GRANITE_PII_PROMPT_PATH": ("/prompts/pii.txt", defaults["GRANITE_PII_PROMPT_PATH"]),
        "GRANITE_NOTE_PROMPT_PATH": ("/prompts/note.txt", defaults["GRANITE_NOTE_PROMPT_PATH"]),
    }
    for name, (old, new) in replacements.items():
        if values[name] == old:
            values[name] = new
    save_env(env, values)
    save_env(root / "granite.env", {name: value for name, value in values.items()
                                  if name.startswith("GRANITE_") or name == "RISK_SHARED_SECRET"})
    save_env(root / "gateway.env", {
        "RISK_URL": "http://aegis-risk:8000", "RISK_SECRET": values["AEGIS_RISK_SECRET"],
        "HMAC_KEY": values["HMAC_KEY"], "DB_PATH": "/data/clmi.db",
        "THRESHOLDS_PATH": values["AEGIS_THRESHOLDS_PATH"], "POLICY_ENGINE": "cobol",
        "CLMIPOL_BIN": "/app/txn/CLMIPOL", "UI_DIR": "/app/ui",
        "GRANITE_URL": "http://aegis-granite:8001" if with_granite else "",
        "GRANITE_TIMEOUT_S": "60", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--with-granite", action="store_true")
    args = parser.parse_args()
    prepare(args.directory, args.with_granite)
    print("Private runtime configuration prepared; existing evidence and HMAC keys retained.")
