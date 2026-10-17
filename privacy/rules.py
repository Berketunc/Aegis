"""Privacy rules on the decision path: regex + gazetteer detection, HMAC pseudonyms.

Stdlib only, so the gateway and the risk service can both import it.
The detector never sees this output; it reads the original image (plan.md §6.1).
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

# Order matters: more specific patterns first so a card number is not
# half-eaten by the phone pattern, a policy number not by the VRM pattern, etc.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("POLICY", re.compile(r"\bPN-\d{4}-\d{4}\b", re.I)),
    ("CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b")),
    ("NINO", re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", re.I)),
    ("PHONE", re.compile(r"(?<![\w+])(?:\+44\s?\(?0?\)?\s?|0)(?:\d\s?){9,10}(?!\w)")),
    ("DOB", re.compile(
        r"\b(?:\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2}"
        r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(?:19|20)\d{2})\b",
        re.I)),
    ("POSTCODE", re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.I)),
    # Current GB format (AB12 CDE), then prefix (A123 BCD) and suffix (ABC 123D) formats.
    ("VRM", re.compile(
        r"\b(?:[A-Z]{2}\d{2}\s?[A-Z]{3}|[A-Z]\d{1,3}\s?[A-Z]{3}|[A-Z]{3}\s?\d{1,3}[A-Z])\b", re.I)),
]

KINDS = ("PERSON",) + tuple(k for k, _ in _PATTERNS)


@dataclass(frozen=True)
class Finding:
    token: str
    kind: str
    value: str


def luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _normalise(kind: str, value: str) -> str:
    v = value.strip().lower()
    if kind in ("PHONE", "CARD", "NINO", "POSTCODE", "VRM", "POLICY"):
        v = re.sub(r"[\s()-]", "", v)
    if kind == "PHONE" and v.startswith("+44"):
        v = "0" + v[3:].lstrip("0")
    return v


def pseudonym(key: bytes, kind: str, value: str) -> str:
    """Deterministic token, e.g. PERSON_7f3a91c2. Same value -> same token, so records stay linkable."""
    digest = hmac.new(key, f"{kind}|{_normalise(kind, value)}".encode(), hashlib.sha256).hexdigest()
    return f"{kind}_{digest[:8]}"


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    return any(span[0] < e and s < span[1] for s, e in taken)


def detect(text: str, gazetteer: dict[str, str] | None = None) -> list[tuple[int, int, str, str]]:
    """Return non-overlapping (start, end, kind, value) spans, gazetteer first."""
    spans: list[tuple[int, int, str, str]] = []
    taken: list[tuple[int, int]] = []
    for kind, value in sorted((gazetteer or {}).items(), key=lambda kv: -len(kv[1])):
        if not value or len(value.strip()) < 2:
            continue
        for m in re.finditer(re.escape(value.strip()), text, re.I):
            if not _overlaps(m.span(), taken):
                spans.append((m.start(), m.end(), _gazetteer_kind(kind), m.group()))
                taken.append(m.span())
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            if _overlaps(m.span(), taken):
                continue
            if kind == "CARD" and not luhn_ok(m.group()):
                continue
            spans.append((m.start(), m.end(), kind, m.group()))
            taken.append(m.span())
    # Name parts: "Alex Doe" in the form also catches a lone "Alex" or "Doe" in the text.
    name = (gazetteer or {}).get("name", "")
    for part in name.split():
        if len(part) < 3:
            continue
        for m in re.finditer(rf"\b{re.escape(part)}\b", text, re.I):
            if not _overlaps(m.span(), taken):
                spans.append((m.start(), m.end(), "PERSON", m.group()))
                taken.append(m.span())
    return sorted(spans)


def _gazetteer_kind(field: str) -> str:
    return {"name": "PERSON", "email": "EMAIL", "phone": "PHONE", "policy_number": "POLICY",
            "postcode": "POSTCODE", "registration": "VRM"}.get(field, "PERSON")


def redact(text: str, key: bytes, gazetteer: dict[str, str] | None = None) -> tuple[str, list[Finding]]:
    """Replace every detected value with its pseudonym. Returns (redacted_text, findings for the vault)."""
    out, findings, pos = [], [], 0
    for start, end, kind, value in detect(text, gazetteer):
        tok = pseudonym(key, kind, value)
        out.append(text[pos:start])
        out.append(tok)
        findings.append(Finding(tok, kind, value))
        pos = end
    out.append(text[pos:])
    return "".join(out), findings


def contains_pii(text: str) -> bool:
    """Regex-only check, used as the last gate on egress payloads and LLM output."""
    return bool(detect(text))
