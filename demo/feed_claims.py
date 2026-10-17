"""Stand-in for the insurer's claims system: sends the demo claims into transaction CLMI.

In production the insurer's core claims system calls CLMI for every new motor claim. For the demo, this
script replays the fictional claims in ui/data.js against a running gateway: each photo goes to
POST /evidence, then the claim goes to POST /claims. The gateway's real pipeline does the rest
(pseudonymisation, risk call, COBOL rule, journal), and the console shows the results live.

Two ways to drive the risk signals:
  --stub URL     development stub risk service (txn/stub_risk.py). Before each claim the script sets the
                 claim's detector score on the stub, so the bands match the offline demo. Synthetic photo
                 bytes are fine because the stub doesn't decode images.
  --photos DIR   B's real risk service. DIR holds real photos named <key>.jpg (e.g. D07.jpg); the real
                 detector scores them. Claims with no photo in DIR are skipped.
Photos: by default the demo evidence photos in ui/evidence (fetched by demo/fetch_evidence.py) are sent, so
the console can show them; with --stub, claims without a photo fall back to synthetic bytes.

Each claim uses idempotency key "demo-<key>", so re-running the script doesn't create duplicates.

  python demo/feed_claims.py --gateway http://localhost:8080 --stub http://localhost:9000
  python demo/feed_claims.py --interval 5        # trickle one claim every 5 s, for a live demo
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]


def load_demo(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    body = text.split("window.AEGIS_DEMO =", 1)[1].strip().rstrip(";")
    return json.loads(body)


def expected(c: dict, t: dict) -> tuple[str, list[str]]:
    """Same truth table as CLMIPOL.cbl, used to check the gateway's answer."""
    if c["outage"]:
        return "AMBER", ["RISK_SERVICE_UNAVAILABLE"]
    fx = lambda x: max(0, min(10000, round(x * 10000)))  # noqa: E731
    reasons: list[tuple[str, int]] = []
    if c["duplicate_of"]:
        reasons.append(("DUPLICATE_PHOTO", 2))
    if fx(c["ai_score"]) >= fx(t["tau_red"]):
        reasons.append(("AI_SCORE_ABOVE_RED", 2))
    elif fx(c["ai_score"]) >= fx(t["tau_amber"]):
        reasons.append(("AI_SCORE_ABOVE_AMBER", 1))
    if c["software_tag"]:
        reasons.append(("EDITING_SOFTWARE_TAG", 1))
    band = ["GREEN", "AMBER", "RED"][max((s for _, s in reasons), default=0)]
    codes = [r for r, _ in reasons] or ["NONE"]
    if c["exif_present"] is False:
        codes = ([] if codes == ["NONE"] else codes) + ["INFO_EXIF_MISSING"]
    return band, codes


def photo_file(directory: Path, key: str, i: int) -> Path | None:
    """<key>.jpg/.png for the first photo, <key>_2.jpg for the second, and so on."""
    stem = key if i == 0 else f"{key}_{i + 1}"
    for ext in (".jpg", ".jpeg", ".png"):
        if (directory / (stem + ext)).exists():
            return directory / (stem + ext)
    return None


def synthetic_jpeg(seed: str) -> bytes:
    """JPEG magic bytes + deterministic filler. Only valid with the stub, which doesn't decode images."""
    return b"\xff\xd8\xff\xe0" + hashlib.sha256(seed.encode()).digest() * 16


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gateway", default="http://localhost:8080")
    ap.add_argument("--stub", default=None, help="stub risk service URL (dev); omit when using --photos")
    ap.add_argument("--photos", default=None, help="directory of photos named <key>.jpg (default: ui/evidence if present)")
    ap.add_argument("--interval", type=float, default=0.0, help="seconds between claims")
    ap.add_argument("--data", default=str(REPO / "ui" / "data.js"))
    args = ap.parse_args()
    if not args.stub and not args.photos:
        ap.error("pass --stub URL (development stub) or --photos DIR (real risk service)")

    demo = load_demo(Path(args.data))
    t = demo["thresholds"]
    claims = sorted(demo["claims"], key=lambda c: -c["offset_min"])  # oldest first
    first_bytes: dict[str, bytes] = {}
    photo_dir = Path(args.photos) if args.photos else REPO / "ui" / "evidence"
    gw = httpx.Client(base_url=args.gateway, timeout=10)
    stub = httpx.Client(base_url=args.stub, timeout=5) if args.stub else None
    mismatches = 0
    print(f"{'key':4} {'claim':11} {'band':5}  reasons")
    try:
        for c in claims:
            key = c["key"]
            photos: list[tuple[str, bytes]] = []
            for i in range(c["photos"]):
                if i == 0 and c["duplicate_of"]:
                    data = first_bytes[c["duplicate_of"]]
                elif (found := photo_file(photo_dir, key, i)) is not None:
                    data = found.read_bytes()
                elif stub and not args.photos:
                    data = synthetic_jpeg(f"{key}:{i}")
                else:
                    continue
                ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
                name = key + ("_gimp" if c["software_tag"] else "") + ("_noexif" if c["exif_present"] is False else "") + f"_{i + 1}{ext}"
                photos.append((name, data))
            if not photos:
                print(f"{key:4} skipped: no photo in {photo_dir}")
                continue
            first_bytes[key] = photos[0][1]

            if stub:
                mode = {"mode": "fail"} if c["outage"] else {"mode": "ok", "score": c["ai_score"]}
                stub.post("/stub/mode", json=mode).raise_for_status()

            evidence = []
            for name, data in photos:
                r = gw.post("/evidence", files={"file": (name, data, "image/jpeg")})
                r.raise_for_status()
                evidence.append({"evidence_id": r.json()["evidence_id"]})

            body = {
                "idempotency_key": f"demo-{key}",
                "policy_number": c["policy_number"],
                "claimant": c["claimant"],
                "incident": {"date": (date.today() - timedelta(days=c["incident"]["date_offset_days"])).isoformat(),
                             "description": c["incident"]["description"]},
                "evidence": evidence,
                "damage_category": c["damage_category"],
            }
            r = gw.post("/claims", json=body)
            r.raise_for_status()
            d = r.json()
            flag = ""
            if stub:  # with real photos the real detector decides, so there's nothing to compare against
                band, reasons = expected(c, t)
                if (d["band"], d["reasons"]) != (band, reasons):
                    mismatches += 1
                    flag = f"   <-- expected {band} {reasons}"
            print(f"{key:4} {d['claim_id']:11} {d['band']:5}  {', '.join(d['reasons'])}{' (replayed)' if d.get('replayed') else ''}{flag}")
            if args.interval:
                time.sleep(args.interval)
    finally:
        if stub:
            stub.post("/stub/mode", json={"mode": "ok", "score": None})
    if mismatches:
        print(f"\n{mismatches} claim(s) differ from the demo dataset. Check the thresholds file matches data.js ({t['version']}).")
        return 1
    print(f"\nSent {len(claims)} claims.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
