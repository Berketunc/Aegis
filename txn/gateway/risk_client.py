"""Gateway -> risk service call. Any failure returns None, which the policy turns into AMBER."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger("clmi.risk")


class Metadata(BaseModel):
    exif_present: bool | None = None
    software_tag: str | None = None
    c2pa_manifest: bool | None = None


class Signals(BaseModel):
    ai_score: float
    metadata: Metadata = Metadata()
    duplicate_of: str | None = None


class RiskResponse(BaseModel):
    claim_id: str
    model_version: str | None = None
    signals: Signals
    egress_payload_id: str | None = None
    thresholds_version: str | None = None
    timings_ms: dict[str, float] = {}


async def score(client: httpx.AsyncClient, url: str, secret: str, timeout_ms: int,
                claim_id: str, description: str, evidence: list[dict[str, str]],
                privacy_context: dict[str, Any] | None = None, policy_token: str | None = None) -> RiskResponse | None:
    # httpx timeouts are per phase (connect, read, ...); wait_for makes 300 ms a hard end-to-end deadline.
    try:
        resp = await asyncio.wait_for(client.post(
            f"{url.rstrip('/')}/risk/score",
            json={"claim_id": claim_id, "description": description, "evidence": evidence,
                  "privacy_context": privacy_context or {}, "policy_token": policy_token},
            # X-Risk-Secret is canonical. The legacy header keeps C's explicitly
            # labelled stub compatible; it is not accepted as auth by B's API.
            headers={"X-Risk-Secret": secret, "X-Shared-Secret": secret},
            timeout=timeout_ms / 1000,
        ), timeout=timeout_ms / 1000)
        resp.raise_for_status()
        parsed = RiskResponse.model_validate(resp.json())
    except (httpx.HTTPError, ValueError, ValidationError, asyncio.TimeoutError) as e:
        log.warning("risk service unavailable for %s: %s", claim_id, type(e).__name__)
        return None
    if not 0.0 <= parsed.signals.ai_score <= 1.0 or parsed.claim_id != claim_id:
        log.warning("risk service returned an invalid response for %s", claim_id)
        return None
    return parsed


async def upload_evidence(client: httpx.AsyncClient, url: str, secret: str, filename: str,
                          content: bytes, content_type: str) -> dict[str, Any]:
    resp = await client.post(f"{url.rstrip('/')}/evidence", headers={"X-Risk-Secret": secret, "X-Shared-Secret": secret},
                             files={"file": (filename, content, content_type)}, timeout=10.0)
    resp.raise_for_status()
    return resp.json()
