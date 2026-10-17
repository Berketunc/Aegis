from dataclasses import dataclass
import json
import math
import os
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path
    secret: str
    evidence_key_b64: str
    model_manifest: str | None = None
    privacy_adapter: str | None = None
    thresholds_path: str | None = None
    device: str = "cpu"
    threads: int = 1
    max_upload_bytes: int = 15 * 1024 * 1024

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.environ.get("AEGIS_DATA_DIR", "/data")),
            secret=os.environ.get("AEGIS_RISK_SECRET", ""),
            evidence_key_b64=os.environ.get("AEGIS_EVIDENCE_KEY_B64", ""),
            model_manifest=os.environ.get("AEGIS_MODEL_MANIFEST"),
            privacy_adapter=os.environ.get("AEGIS_PRIVACY_ADAPTER"),
            thresholds_path=os.environ.get("AEGIS_THRESHOLDS_PATH"),
            device=os.environ.get("AEGIS_MODEL_DEVICE", "cpu"),
            threads=int(os.environ.get("AEGIS_TORCH_THREADS", "1")),
        )


def read_thresholds(path):
    if not path:
        return None
    config = json.loads(Path(path).read_text())
    amber, red = float(config["tau_amber"]), float(config["tau_red"])
    if (not config.get("thresholds_version") or not all(map(math.isfinite, [amber, red]))
            or not 0 <= amber <= red <= 1):
        raise ValueError("Invalid versioned detector thresholds")
    return {"thresholds_version": config["thresholds_version"], "tau_amber": amber, "tau_red": red}
