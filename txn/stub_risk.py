"""Stand-in for B's risk service, implementing the plan.md §14 contract. Dev and demo only.

Scores come from the upload's filename so demo photos are predictable:
  *fake*  -> 0.93    *edit* / *inflat* -> 0.72    *gimp* / *ps_* -> software tag set    otherwise 0.08
The same file bytes used on a second claim -> duplicate_of = the first claim.
*small* in the filename -> native_short_side 320 (low-resolution rule), otherwise 1080.
POST /stub/mode {"mode": "ok"|"fail"|"slow"|"garbage", "score": float|null} drives the fail-safe demo.

Run: uvicorn txn.stub_risk:app --port 9000
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Risk service STUB")
SECRET = os.getenv("RISK_SECRET", "dev-shared-secret")
_evidence: dict[str, dict] = {}         # evidence_id -> {sha256, filename}
_first_use: dict[str, str] = {}         # sha256 -> first claim_id that used it
_state = {"mode": os.getenv("STUB_MODE", "ok"), "score": None}


def _auth(secret: str | None) -> None:
    if secret != SECRET:
        raise HTTPException(401, "bad shared secret")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "stub": True, **_state}


@app.post("/stub/mode")
async def set_mode(request: Request) -> dict:
    body = await request.json()
    _state["mode"] = body.get("mode", _state["mode"])
    _state["score"] = body.get("score")
    return _state


@app.post("/evidence")
async def evidence(file: UploadFile = File(...), x_shared_secret: str | None = Header(default=None)) -> dict:
    _auth(x_shared_secret)
    data = await file.read()
    sha = hashlib.sha256(data).hexdigest()
    eid = f"EV-{sha[:8].upper()}"
    _evidence[eid] = {"sha256": sha, "filename": (file.filename or "").lower()}
    return {"evidence_id": eid, "sha256": sha}


@app.post("/risk/score")
async def score(request: Request, x_shared_secret: str | None = Header(default=None)):
    _auth(x_shared_secret)
    t0 = time.perf_counter()
    mode = _state["mode"]
    if mode == "fail":
        raise HTTPException(500, "stub failure")
    if mode == "slow":
        await asyncio.sleep(1.0)
    if mode == "garbage":
        return PlainTextResponse("not json")
    body = await request.json()
    claim_id = body["claim_id"]
    items = [_evidence.get(e["evidence_id"], {"sha256": "", "filename": ""}) for e in body["evidence"]]
    names = " ".join(i["filename"] for i in items)
    if _state["score"] is not None:
        ai = float(_state["score"])
    elif "fake" in names:
        ai = 0.93
    elif "edit" in names or "inflat" in names:
        ai = 0.72
    else:
        ai = 0.08
    dup = None
    for it in items:
        first = _first_use.setdefault(it["sha256"], claim_id) if it["sha256"] else claim_id
        if first != claim_id:
            dup = first
    sw = "GIMP 2.10" if ("gimp" in names or "ps_" in names) else None
    total = (time.perf_counter() - t0) * 1000
    return {
        "claim_id": claim_id,
        "model_version": "stub-0",
        "signals": {"ai_score": ai,
                    "metadata": {"exif_present": "noexif" not in names, "software_tag": sw, "c2pa_manifest": False,
                                 "native_short_side": 320 if "small" in names else 1080},
                    "duplicate_of": dup},
        "egress_payload_id": None,
        "timings_ms": {"privacy": 0.0, "load_crops": 0.0, "inference": 0.0, "metadata": 0.0, "dup": 0.0,
                       "total": round(total, 2)},
    }
