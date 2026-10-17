"""Egress allowlist: anything that leaves IBM Z is built by copying named fields, never by deleting.

Every payload built here is also logged by the caller (the gateway journals it).
"""
from __future__ import annotations

from typing import Any

from .rules import contains_pii

# Free-text fields must pass the PII re-scan; the rest are enums, numbers or booleans.
TEXT_FIELDS = ("description_redacted",)
ALLOWED_FIELDS = (
    "description_redacted",
    "damage_category",
    "band",
    "ai_score",
    "exif_present",
    "software_tag_present",
    "c2pa_manifest",
    "duplicate_detected",
)


def build_egress(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return (payload, dropped_fields). Unknown keys in `record` are dropped by construction."""
    payload: dict[str, Any] = {}
    dropped: list[str] = []
    for field in ALLOWED_FIELDS:
        if field not in record or record[field] is None:
            continue
        value = record[field]
        if field in TEXT_FIELDS:
            if not isinstance(value, str) or contains_pii(value):
                dropped.append(field)
                continue
        elif isinstance(value, str) and contains_pii(value):
            dropped.append(field)
            continue
        payload[field] = value
    dropped.extend(sorted(k for k in record if k not in ALLOWED_FIELDS))
    return payload, dropped
