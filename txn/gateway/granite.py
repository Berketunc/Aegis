"""Async Granite jobs after the decision: PII second pass + adjuster note.

Runs only after the decision has been committed, so nothing here can change the band.
Contract with B's Granite service: POST {GRANITE_URL}/pii and /note with
{"prompt": str, "max_tokens": int}, response {"text": str}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from privacy import contains_pii, pseudonym

log = logging.getLogger("clmi.granite")

NOTE_MAX_CHARS = 600
_one_job = asyncio.Semaphore(1)  # plan.md §8.5: one Granite job at a time
_PII_KINDS = {"PERSON", "ADDRESS", "EMAIL", "PHONE", "VRM", "POLICY", "NINO", "CARD", "DOB", "OTHER"}
_TOKEN = re.compile(r"\b[A-Z]+_[0-9a-f]{8}\b")


def load_prompt(prompts_dir: str, name: str) -> str:
    return (Path(prompts_dir) / f"{name}.txt").read_text(encoding="utf-8")


def fence(text: str) -> str:
    """Stop the claimant closing our delimiter early."""
    return text.replace("CLAIM_TEXT>>>", "CLAIM_TEXT> > >").replace("<<<CLAIM_TEXT", "< < <CLAIM_TEXT")


def render(template: str, **fields: Any) -> str:
    # str.replace, not str.format: the templates contain literal JSON braces.
    for k, v in fields.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def parse_pii(raw: str, source_text: str) -> list[tuple[str, str]] | None:
    """Validate the PII pass. Only values that literally occur in the text are accepted."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    items = obj.get("pii") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return None
    found = []
    for it in items:
        if not isinstance(it, dict):
            return None
        kind, value = str(it.get("kind", "")).upper(), it.get("value")
        if kind not in _PII_KINDS or not isinstance(value, str):
            return None
        value = value.strip()
        if len(value) < 2 or value not in source_text or _TOKEN.fullmatch(value):
            continue  # hallucinated or already a token: ignore, don't fail the whole pass
        found.append((kind, value))
    return found


def validate_note(raw: str) -> str | None:
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    bullets = [ln for ln in lines if ln.startswith(("- ", "* ", "• "))]
    if not 1 <= len(bullets) <= 3 or len(bullets) != len(lines):
        return None
    note = "\n".join("- " + b[2:].strip() for b in bullets)
    if len(note) > NOTE_MAX_CHARS or contains_pii(note):
        return None
    return note


def apply_pii(text: str, found: list[tuple[str, str]], key: bytes) -> tuple[str, list[tuple[str, str, str]]]:
    vault = []
    for kind, value in sorted(found, key=lambda kv: -len(kv[1])):
        tok = pseudonym(key, "PERSON" if kind in ("OTHER", "ADDRESS") else kind, value)
        text = text.replace(value, tok)
        vault.append((tok, kind, value))
    return text, vault


async def _complete(client: httpx.AsyncClient, url: str, endpoint: str, prompt: str,
                    max_tokens: int, timeout_s: float) -> str:
    resp = await client.post(f"{url.rstrip('/')}/{endpoint}", json={"prompt": prompt, "max_tokens": max_tokens},
                             timeout=timeout_s)
    resp.raise_for_status()
    text = resp.json().get("text")
    if not isinstance(text, str):
        raise ValueError("granite response missing text")
    return text


async def run_job(*, client: httpx.AsyncClient, granite_url: str, timeout_s: float, prompts_dir: str,
                  key: bytes, redacted: dict[str, Any], decision: dict[str, Any],
                  signals: dict[str, Any]) -> dict[str, Any]:
    """Return {status, note, pii_added, vault, redacted_or_None}. Never raises."""
    desc = redacted.get("incident", {}).get("description", "")
    out: dict[str, Any] = {"status": "unavailable", "note": None, "pii_added": [], "vault": [], "redacted": None}
    async with _one_job:
        # 1. PII second pass: adds anything the rules missed, before the note is written.
        try:
            raw = await _complete(client, granite_url, "pii",
                                  render(load_prompt(prompts_dir, "pii_second_pass"), claim_text=fence(desc)),
                                  256, timeout_s)
            found = parse_pii(raw, desc)
            if found:
                new_desc, vault = apply_pii(desc, found, key)
                updated = json.loads(json.dumps(redacted))
                updated["incident"]["description"] = new_desc
                out.update(vault=vault, pii_added=[t for t, _, _ in vault], redacted=updated)
                desc = new_desc
        except (httpx.HTTPError, ValueError, OSError) as e:
            log.warning("PII second pass failed for %s: %s", decision["claim_id"], type(e).__name__)

        # 2. Adjuster note from the redacted record and the signals only.
        md = signals.get("metadata") or {}
        try:
            prompt = render(load_prompt(prompts_dir, "adjuster_note"),
                            band=decision["band"], reasons=", ".join(decision["reasons"]),
                            ai_score=f"{signals.get('ai_score', 0):.2f}",
                            duplicate="yes" if signals.get("duplicate_of") else "no",
                            exif_present=md.get("exif_present"), software_tag=md.get("software_tag") or "none",
                            c2pa=md.get("c2pa_manifest"), claim_text=fence(desc))
            note = validate_note(await _complete(client, granite_url, "note", prompt, 200, timeout_s))
            if note:
                out.update(status="ready", note=note)
        except (httpx.HTTPError, ValueError, OSError) as e:
            log.warning("note failed for %s: %s", decision["claim_id"], type(e).__name__)
    return out
