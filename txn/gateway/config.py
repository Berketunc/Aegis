"""Gateway settings, all from environment variables so the container needs no code changes."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

TXN_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = TXN_DIR.parent


@dataclass
class Thresholds:
    version: str
    tau_red: float
    tau_amber: float


@dataclass
class Settings:
    risk_url: str = field(default_factory=lambda: os.getenv("RISK_URL", "http://localhost:9000"))
    risk_secret: str = field(default_factory=lambda: os.getenv("RISK_SECRET", "dev-shared-secret"))
    risk_timeout_ms: int = field(default_factory=lambda: int(os.getenv("RISK_TIMEOUT_MS", "300")))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", str(TXN_DIR / "clmi.db")))
    thresholds_path: str = field(default_factory=lambda: os.getenv(
        "THRESHOLDS_PATH", str(TXN_DIR / "thresholds.example.json")))
    policy_engine: str = field(default_factory=lambda: os.getenv("POLICY_ENGINE", "auto"))
    clmipol_bin: str = field(default_factory=lambda: os.getenv("CLMIPOL_BIN", str(TXN_DIR / "CLMIPOL")))
    max_upload_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024))))
    hmac_key: bytes = field(default_factory=lambda: os.getenv("HMAC_KEY", "dev-only-change-me").encode())
    granite_url: str = field(default_factory=lambda: os.getenv("GRANITE_URL", ""))
    granite_timeout_s: float = field(default_factory=lambda: float(os.getenv("GRANITE_TIMEOUT_S", "60")))
    ui_dir: str = field(default_factory=lambda: os.getenv("UI_DIR", str(REPO_DIR / "ui")))
    prompts_dir: str = field(default_factory=lambda: os.getenv("PROMPTS_DIR", str(REPO_DIR / "llm" / "prompts")))

    def load_thresholds(self) -> Thresholds:
        """Read on every claim so A can drop in t-2 without a restart; the version goes in the decision record."""
        with open(self.thresholds_path, encoding="utf-8") as f:
            raw = json.load(f)
        t = Thresholds(str(raw["version"]), float(raw["tau_red"]), float(raw["tau_amber"]))
        if not 0.0 <= t.tau_amber <= t.tau_red <= 1.0:
            raise ValueError(f"thresholds out of order: {t}")
        return t
