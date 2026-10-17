"""Post-commit Granite jobs through B's authenticated, typed, local job API.

The PII pass must succeed before a note is requested. Neither job can change a band.
C's prompts live in the Granite service; untrusted text is never inserted in a prompt here.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import re
import time
from typing import Any

import httpx

from privacy import contains_pii, pseudonym

log = logging.getLogger("clmi.granite")
NOTE_MAX_CHARS = 600
NOTE_DISCLAIMER = (
    "The detector score is one signal, not proof; this note is advisory and does not change the band."
)
_one_job = asyncio.Semaphore(1)
_PII_KINDS = {
    "name": "PERSON", "address": "ADDRESS", "email": "EMAIL", "phone": "PHONE",
    "postcode": "POSTCODE", "vehicle_registration": "VRM", "policy_number": "POLICY",
    "national_insurance": "NINO", "card_number": "CARD", "date_of_birth": "DOB",
    "other_identifier": "OTHER",
}
_TOKEN = re.compile(r"\b[A-Z]+_[0-9a-f]{8}\b")
_JOB_ID = re.compile(r"LLM-[0-9a-f]{24}")


def fence(text: str) -> str:
    """Defang legacy claim delimiters as well as using separate JSON data fields."""
    return text.replace("CLAIM_TEXT>>>", "CLAIM_TEXT> > >").replace("<<<CLAIM_TEXT", "< < <CLAIM_TEXT")


def parse_pii(raw: str | dict[str, Any], source_text: str) -> list[tuple[str, str]] | None:
    """Fail closed on malformed, guessed, or already-pseudonymised model findings."""
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or obj.get("review_status") != "model_second_pass":
        return None
    items = obj.get("findings")
    if not isinstance(items, list) or len(items) > 40:
        return None
    found = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"kind", "text"}:
            return None
        kind, value = item["kind"], item["text"]
        if (not isinstance(kind, str) or kind not in _PII_KINDS or not isinstance(value, str)
                or not 2 <= len(value.strip()) <= 1000 or value not in source_text or _TOKEN.fullmatch(value)):
            return None
        pair = (_PII_KINDS[kind], value)
        if pair not in found:
            found.append(pair)
    return found


def validate_note(raw: str, blocked_values: list[str] | None = None) -> str | None:
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if len(lines) != 3 or any(not line.startswith("- ") for line in lines):
        return None
    if lines[2][2:].strip() != NOTE_DISCLAIMER:
        return None
    note = "\n".join("- " + line[2:].strip() for line in lines)
    if (len(note) > NOTE_MAX_CHARS or contains_pii(note)
            or any(value.casefold() in note.casefold() for value in (blocked_values or []))):
        return None
    return note


def apply_pii(text: str, found: list[tuple[str, str]], key: bytes) -> tuple[str, list[tuple[str, str, str]]]:
    vault = []
    for kind, value in sorted(found, key=lambda item: -len(item[1])):
        token = pseudonym(key, "PERSON" if kind in ("OTHER", "ADDRESS") else kind, value)
        text = text.replace(value, token)
        vault.append((token, kind, value))
    return text, vault


async def _complete(*, client: httpx.AsyncClient, url: str, shared_secret: str, endpoint: str,
                    payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    """Submit a typed job and poll its fixed local path within a bounded total deadline."""
    if endpoint not in {"pii", "note"} or timeout_s <= 0 or not shared_secret:
        raise ValueError("Invalid Granite job configuration")
    deadline = time.monotonic() + timeout_s
    headers = {"X-Risk-Secret": shared_secret}
    base = url.rstrip("/")

    def remaining() -> float:
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("Local Granite job timed out")
        return seconds

    response = await asyncio.wait_for(
        client.post(f"{base}/{endpoint}", json=payload, headers=headers, timeout=remaining()),
        timeout=remaining(),
    )
    response.raise_for_status()
    submitted = response.json()
    job_id = submitted.get("job_id") if isinstance(submitted, dict) else None
    if response.status_code != 202 or not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise ValueError("Granite did not acknowledge a valid queued job")
    # Never follow a server-supplied poll_url to another host.
    while True:
        response = await asyncio.wait_for(
            client.get(f"{base}/jobs/{job_id}", headers=headers, timeout=remaining()),
            timeout=remaining(),
        )
        response.raise_for_status()
        status = response.json()
        if (not isinstance(status, dict) or status.get("job_id") != job_id
                or status.get("claim_id") != payload["claim_id"] or status.get("kind") != endpoint):
            raise ValueError("Granite returned a mismatched job")
        if status.get("status") == "succeeded":
            result = status.get("result")
            if not isinstance(result, dict):
                raise ValueError("Granite job result was not an object")
            return result
        if status.get("status") == "failed":
            raise ValueError("Granite job failed")
        if status.get("status") not in {"queued", "running"}:
            raise ValueError("Granite returned an unknown job status")
        await asyncio.sleep(min(0.1, remaining()))


def _note_signals(signals: dict[str, Any]) -> dict[str, Any]:
    """Allow only typed non-identifying signals; never forward a raw software tag."""
    score = signals.get("ai_score")
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid detector score")
        score = float(score)
    metadata = signals.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("Invalid metadata signals")
    flags = []
    if metadata.get("exif_present") is False:
        flags.append("exif_absent")
    if metadata.get("software_tag"):
        flags.append("editing_software_tag")
    if metadata.get("c2pa_manifest") is True:
        flags.append("c2pa_present")
    return {"ai_score": score, "duplicate_found": bool(signals.get("duplicate_of")), "flags": flags}


async def run_job(*, client: httpx.AsyncClient, granite_url: str, timeout_s: float,
                  shared_secret: str, key: bytes, redacted: dict[str, Any], decision: dict[str, Any],
                  signals: dict[str, Any], prompts_dir: str | None = None) -> dict[str, Any]:
    """Return a result for the journal; the committed decision is never mutated.

    prompts_dir remains accepted for caller compatibility. Prompt files are loaded only
    by the local Granite runtime, never rendered with claimant-controlled text here.
    """
    out: dict[str, Any] = {"status": "unavailable", "note": None, "pii_added": [], "vault": [], "redacted": None}
    if decision.get("band") not in {"AMBER", "RED"}:
        return out
    description = redacted.get("incident", {}).get("description", "")
    if not isinstance(description, str) or not description:
        return out
    common = {"claim_id": decision["claim_id"], "band": decision["band"]}
    async with _one_job:
        try:
            result = await _complete(client=client, url=granite_url, shared_secret=shared_secret,
                                     endpoint="pii", payload={**common, "description": fence(description)},
                                     timeout_s=timeout_s)
            found = parse_pii(result, description)
            if found is None:
                raise ValueError("PII second pass returned invalid findings")
            if found:
                description, vault = apply_pii(description, found, key)
                updated = copy.deepcopy(redacted)
                updated["incident"]["description"] = description
                out.update(vault=vault, pii_added=[token for token, _, _ in vault], redacted=updated)
        except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError):
            # A failed PII pass must never be interpreted as an empty successful pass.
            log.warning("PII second pass unavailable for %s", decision["claim_id"])
            return out

        try:
            result = await _complete(
                client=client, url=granite_url, shared_secret=shared_secret, endpoint="note",
                payload={**common, "redacted_description": fence(description),
                         "damage_category": "unspecified", "signals": _note_signals(signals)},
                timeout_s=timeout_s,
            )
            bullets = result.get("bullets")
            if (result.get("advisory") is not True or result.get("changes_band") is not False
                    or not isinstance(bullets, list) or len(bullets) != 3
                    or any(not isinstance(item, str) or "\n" in item or "\r" in item for item in bullets)):
                raise ValueError("Invalid advisory note contract")
            note = validate_note("\n".join(f"- {item}" for item in bullets), [value for _, value in found])
            if note is None:
                raise ValueError("Note failed guardrails")
            out.update(status="ready", note=note)
        except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError):
            log.warning("Adjuster note unavailable for %s", decision["claim_id"])
    return out
