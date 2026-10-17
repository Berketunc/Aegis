import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from txn import stub_risk  # noqa: E402
from txn.gateway.app import create_app  # noqa: E402
from txn.gateway.config import Settings  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(scope="session")
def cobol_bin(tmp_path_factory):
    if not shutil.which("cobc"):
        pytest.skip("cobc (GnuCOBOL) not installed; run on the LinuxONE VM or in the container")
    out = tmp_path_factory.mktemp("cobol") / ("CLMIPOL.exe" if os.name == "nt" else "CLMIPOL")
    subprocess.run(["cobc", "-x", "-o", str(out), str(REPO / "txn" / "CLMIPOL.cbl")], check=True)
    return str(out)


@pytest.fixture
def stub():
    stub_risk._evidence.clear()
    stub_risk._first_use.clear()
    stub_risk._state.update(mode="ok", score=None)
    return stub_risk


@pytest.fixture
def settings(tmp_path):
    return Settings(risk_url="http://risk", risk_secret=stub_risk.SECRET, db_path=str(tmp_path / "t.db"),
                    policy_engine="python", hmac_key=b"test-key", granite_url="",
                    ui_dir=str(tmp_path / "no-ui"), max_upload_bytes=1024 * 1024)


@pytest.fixture
def make_client(settings, stub):
    clients = []

    def _make(transport=None, **overrides):
        for k, v in overrides.items():
            setattr(settings, k, v)
        app = create_app(settings, transport=transport or httpx.ASGITransport(app=stub.app))
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def upload(client, name="photo.jpg", data=JPEG):
    r = client.post("/evidence", files={"file": (name, data, "image/jpeg")})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def claim_body(evidence_ids, key=None, description=None):
    body = {
        "policy_number": "PN-4471-0098",
        "claimant": {"name": "Alex Doe", "email": "alex@example.com", "phone": "07700 900123"},
        "incident": {"date": "2026-10-12", "description": description or
                     "Reversed into a bollard in a supermarket car park; rear bumper dented. Call Alex on 07700 900123."},
        "evidence": [{"evidence_id": e} for e in evidence_ids],
    }
    if key:
        body["idempotency_key"] = key
    return body
