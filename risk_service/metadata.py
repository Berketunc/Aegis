"""Weak metadata signals; a present C2PA marker is never a validation claim."""
import re

EDITING_TOOLS = re.compile(r"photoshop|lightroom|gimp|affinity|stable[ -]?diffusion|midjourney|dall[ -]?e|comfyui|automatic1111|firefly|generative", re.I)


def extract_flags(raw: bytes, exif_present: bool, software: str | None) -> dict:
    # JPEG APP11/JUMBF and PNG/WebP metadata can contain this marker. Presence only:
    # arbitrary metadata can spoof it, and this does not validate a signature.
    c2pa = b"c2pa" in raw.lower()
    return {
        "exif_present": exif_present,
        "software_tag": software if software and EDITING_TOOLS.search(software) else None,
        "c2pa_manifest": c2pa,
        "c2pa_check": "marker_presence_only_unverified",
    }
