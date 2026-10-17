#!/usr/bin/env python3
"""Fetch real car-damage photos from Wikimedia Commons (plan.md section 7.2).

Downloads originals into data/raw/commons/<id>.jpg and writes an attribution
manifest to data/commons_manifest.csv (incrementally, so the run is resumable).

Standard library only (requests is not installed in this environment).
Run from the repo root:  python3 data/fetch_commons.py
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone

# ---------------------------------------------------------------- settings
API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "AegisClaimShield/0.1 (https://github.com/Berketunc; IBM Z Datathon 2026 research dataset) python-urllib"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "data", "raw", "commons")
MANIFEST = os.path.join(REPO_ROOT, "data", "commons_manifest.csv")

QUERIES = [
    "car crash",
    "car accident",
    "damaged car",
    "crashed car",
    "car wreck",
    "traffic collision",
    "fender bender",
    "dented car",
    "collision damage",
]

TARGET = 1000      # minimum we want
STOP_AT = 2000     # hard stop
MIN_WIDTH = 800
CUTOFF = "2022-01-01T00:00:00Z"   # latest file version must be strictly before this

# upload.wikimedia.org throttles bulk fetches of *originals* (HTTP 429, "use
# thumbnail images in standard sizes"). Standard widths are
# 20,40,60,120,250,330,500,960,1280,1920,3840. We download the largest standard
# width strictly below the original width (so nothing is upscaled); originals
# narrower than 961 px are fetched as-is, with bounded retries.
THUMB_STEPS = (960,)  # masters are 768 px long side; 960 is plenty
MAX_RETRY_AFTER = 300

API_SLEEP = 0.3
DOWNLOAD_SLEEP = 10.0  # upload.wikimedia.org throttles bulk clients; stay well under it
RETRIES = 5
TIMEOUT = 60

SEARCH_LIMIT = 50   # srlimit max for anonymous clients
BATCH = 50          # titles per imageinfo request

# Licence short names we accept (case-insensitive). Anything with NC or ND is rejected first.
LICENCE_OK = re.compile(
    r"^(?:"
    r"cc0(?:\s*1\.0)?"
    r"|public\s*domain"
    r"|pd(?:[-\s].*)?"
    r"|cc[\s-]by(?:[\s-]sa)?[\s-]?\d\.\d(?:[\s-][a-z]{2,3})?"
    r")$",
    re.IGNORECASE,
)
LICENCE_BAD = re.compile(r"\b(?:nc|nd)\b", re.IGNORECASE)

# Drop if the title or any category matches. Word boundaries so that
# "toy" does not match "Toyota" and "render" does not match "surrender".
BLOCK = re.compile(
    r"(?<![a-z])(?:"
    r"ai[\s-]?generated|computer[\s-]?generated|render(?:ed|ing|ings|s)?"
    r"|illustrations?|drawings?|toys?|model\s+cars?|die[\s-]?cast"
    r"|video\s*games?|fatal(?:ity|ities)?|victims?"
    r"|scale\s+models?|lego|paintings?|artworks?|screenshots?"
    # Off-target scenes seen in the first spot-check (17 Oct): motorsport, emergency and
    # airport vehicles, fairground rides, drills, charts, stereo cards, historic photos.
    r"|rac(?:e|es|ing)|nascar|indy(?:car)?|indianapolis|formula\s*(?:one|1)|grand\s+prix|rall(?:y|ies)|motorsports?|speedway"
    r"|fire\s*(?:engines?|trucks?|brigade)|crash\s+tenders?|airports?|aircraft|aeroplanes?|airplanes?"
    r"|bumper\s+cars?|dodgems?|fairground|drills?|exercises?|training"
    r"|diagrams?|charts?|maps?|infographics?|stereo(?:graphs?|views?)?|memorials?"
    r"|trains?|railways?|trams?|buses|bus|lorr(?:y|ies)|tractors?"
    r"|19[0-6]\d|18\d\d|historic(?:al)?"
    r")(?![a-z])",
    re.IGNORECASE,
)

CSV_FIELDS = [
    "id", "filename", "title", "page_url", "author", "licence",
    "upload_date", "width", "height", "source_query", "source_url",
    "dl_url", "dl_width", "dl_height",
]

# ---------------------------------------------------------------- helpers
TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    s = TAG_RE.sub("", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def log(*a):
    print(*a, flush=True)


def http_get(url: str, *, binary: bool = False, what: str = ""):
    """GET with retries and exponential backoff. Returns bytes."""
    last_err = None
    for attempt in range(1, RETRIES + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (404, 403, 410) and binary:
                break  # not going to get better
            retry_after = e.headers.get("Retry-After") if e.headers else None
            wait = 2 ** attempt
            if e.code == 429:  # throttled: back off hard instead of hammering
                wait = 60 * attempt
            if retry_after and retry_after.isdigit():
                wait = min(max(wait, int(retry_after)), MAX_RETRY_AFTER)
            log(f"  ! HTTP {e.code} on {what or url} (attempt {attempt}/{RETRIES}); sleeping {wait}s")
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            wait = 2 ** attempt
            log(f"  ! {type(e).__name__}: {e} on {what or url} (attempt {attempt}/{RETRIES}); sleeping {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"failed after {RETRIES} attempts: {last_err}")


def api(params: dict) -> dict:
    params = dict(params, format="json", formatversion="2", maxlag="5")
    url = API + "?" + urllib.parse.urlencode(params)
    for _ in range(5):
        raw = http_get(url, what="api:" + params.get("action", ""))
        time.sleep(API_SLEEP)
        data = json.loads(raw)
        err = data.get("error")
        if err and err.get("code") == "maxlag":
            log("  ! maxlag; sleeping 5s")
            time.sleep(5)
            continue
        if err:
            raise RuntimeError(f"API error: {err}")
        return data
    raise RuntimeError("API kept returning maxlag")


def search_titles(query: str):
    """Yield file titles for a search query, paging with sroffset."""
    offset = 0
    while True:
        data = api({
            "action": "query",
            "list": "search",
            "srsearch": f"{query} filetype:bitmap",
            "srnamespace": "6",
            "srlimit": str(SEARCH_LIMIT),
            "sroffset": str(offset),
            "srprop": "",
        })
        hits = data.get("query", {}).get("search", [])
        for h in hits:
            yield h["title"]
        cont = data.get("continue", {}).get("sroffset")
        if cont is None or not hits:
            return
        offset = cont


def fetch_info(titles: list[str]) -> dict[str, dict]:
    """Return {title: {"imageinfo": {...}, "categories": [...]}} for a batch of titles.

    Follows API continuation so that long category lists are complete.
    """
    base = {
        "action": "query",
        "prop": "imageinfo|categories",
        "titles": "|".join(titles),
        "iiprop": "url|size|mime|timestamp|extmetadata|user",
        "iiextmetadatafilter": "LicenseShortName|License|Artist|DateTime|ImageDescription|Credit",
        "cllimit": "max",
    }
    out: dict[str, dict] = {}
    cont: dict = {}
    for _ in range(40):
        data = api({**base, **cont})
        for page in data.get("query", {}).get("pages", []):
            t = page.get("title")
            if not t:
                continue
            rec = out.setdefault(t, {"imageinfo": None, "categories": [], "missing": page.get("missing", False)})
            ii = page.get("imageinfo")
            if ii and rec["imageinfo"] is None:
                rec["imageinfo"] = ii[0]
            for c in page.get("categories", []) or []:
                rec["categories"].append(c["title"])
        cont = data.get("continue")
        if not cont:
            break
        cont = {k: v for k, v in cont.items() if k != "continue"}
    return out


def licence_ok(short: str) -> bool:
    s = (short or "").strip()
    if not s:
        return False
    if LICENCE_BAD.search(s):
        return False
    return bool(LICENCE_OK.match(s))


def load_manifest() -> tuple[dict[str, dict], int]:
    """Return ({title: row}, max_id_number)."""
    rows: dict[str, dict] = {}
    max_id = 0
    if not os.path.exists(MANIFEST):
        return rows, 0
    with open(MANIFEST, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows[row["title"]] = row
            m = re.match(r"commons_(\d+)$", row["id"])
            if m:
                max_id = max(max_id, int(m.group(1)))
    return rows, max_id


def thumb_url(original_url: str, width: int) -> tuple[str, int | None]:
    """Return (download_url, requested_thumb_width_or_None) for an original."""
    want = next((w for w in THUMB_STEPS if w < width), None)
    clean = original_url.split("?", 1)[0]
    if want is None:
        return clean, None
    parsed = urllib.parse.urlsplit(clean)
    path = parsed.path  # /wikipedia/commons/a/ab/Name.jpg
    parts = path.split("/")
    name = parts[-1]
    new_path = "/".join(parts[:3] + ["thumb"] + parts[3:]) + f"/{want}px-{name}"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, new_path, "", "")), want


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Read (width, height) from JPEG SOF marker without decoding."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        if marker == 0xDA:
            break
        i += 2 + seglen
    return None


def download(url: str, dest: str) -> tuple[int, int] | None:
    """Download url to dest. Returns (width, height) of the saved JPEG or None."""
    tmp = dest + ".part"
    try:
        data = http_get(url, binary=True, what="download")
    except RuntimeError as e:
        log(f"  ! download failed: {e}")
        return None
    finally:
        time.sleep(DOWNLOAD_SLEEP)
    size = jpeg_size(data) if len(data) >= 1024 else None
    if not size:
        log("  ! download did not look like a JPEG; skipping")
        return None
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)
    return size


# ---------------------------------------------------------------- main
def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest, max_id = load_manifest()
    kept = len(manifest)
    next_id = max_id + 1
    seen: set[str] = set(manifest)          # titles already decided (kept) -> skip
    considered: set[str] = set()            # titles evaluated this run (any outcome)
    stats = Counter()
    licences = Counter(r["licence"] for r in manifest.values())
    started = time.time()

    log(f"Output dir: {OUT_DIR}")
    log(f"Manifest:   {MANIFEST} ({kept} rows already, next id commons_{next_id:06d})")

    # Re-download anything in the manifest whose file is missing (resumability).
    for row in manifest.values():
        dest = os.path.join(OUT_DIR, row["filename"])
        if not os.path.exists(dest) and row.get("dl_url"):
            log(f"  re-downloading missing {row['filename']}")
            if not download(row["dl_url"], dest):
                stats["download_failed_resume"] += 1

    new_file = not os.path.exists(MANIFEST) or os.path.getsize(MANIFEST) == 0
    mf = open(MANIFEST, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(mf, fieldnames=CSV_FIELDS)
    if new_file:
        writer.writeheader()
        mf.flush()

    def done() -> bool:
        return kept >= STOP_AT

    try:
        for query in QUERIES:
            if done():
                break
            log(f"\n=== query: {query!r} ===")
            q_seen = q_kept = 0
            batch: list[str] = []

            def process(batch: list[str]):
                nonlocal kept, next_id, q_kept
                if not batch:
                    return
                try:
                    info = fetch_info(batch)
                except RuntimeError as e:
                    log(f"  ! imageinfo batch failed: {e}")
                    stats["batch_failed"] += len(batch)
                    return
                for title in batch:
                    if done():
                        return
                    rec = info.get(title)
                    stats["candidates"] += 1
                    if not rec or rec.get("missing") or not rec.get("imageinfo"):
                        stats["reject:missing_info"] += 1
                        continue
                    ii = rec["imageinfo"]
                    cats = rec["categories"]
                    meta = ii.get("extmetadata", {}) or {}
                    mime = ii.get("mime", "")
                    width = int(ii.get("width") or 0)
                    height = int(ii.get("height") or 0)
                    ts = ii.get("timestamp", "")
                    lic = strip_html((meta.get("LicenseShortName") or {}).get("value", ""))

                    if mime != "image/jpeg":
                        stats["reject:not_jpeg"] += 1
                        continue
                    if width < MIN_WIDTH:
                        stats["reject:too_small"] += 1
                        continue
                    if not ts or ts >= CUTOFF:
                        stats["reject:too_recent"] += 1
                        continue
                    if not licence_ok(lic):
                        stats["reject:bad_licence"] += 1
                        stats[f"badlic:{lic or '(none)'}"] += 1
                        continue
                    hay = title + "\n" + "\n".join(cats)
                    m = BLOCK.search(hay)
                    if m:
                        stats["reject:blocked_keyword"] += 1
                        stats[f"blocked:{m.group(0).lower()}"] += 1
                        continue

                    url = ii.get("url")
                    if not url:
                        stats["reject:missing_info"] += 1
                        continue

                    fid = f"commons_{next_id:06d}"
                    fname = fid + ".jpg"
                    dest = os.path.join(OUT_DIR, fname)
                    dl_url, _want = thumb_url(url, width)
                    size = download(dl_url, dest)
                    if not size:
                        stats["reject:download_failed"] += 1
                        continue
                    dl_w, dl_h = size
                    if dl_w < MIN_WIDTH:
                        # Should not happen (we never request below 960 or the original)
                        log(f"  ! downloaded file only {dl_w}px wide; dropping")
                        os.remove(dest)
                        stats["reject:download_too_small"] += 1
                        continue

                    author = strip_html((meta.get("Artist") or {}).get("value", "")) or ii.get("user", "")
                    row = {
                        "id": fid,
                        "filename": fname,
                        "title": title,
                        "page_url": "https://commons.wikimedia.org/wiki/"
                                    + urllib.parse.quote(title.replace(" ", "_"), safe=":/()',!-_.~"),
                        "author": author[:500],
                        "licence": lic,
                        "upload_date": ts,
                        "width": width,
                        "height": height,
                        "source_query": query,
                        "source_url": url.split("?", 1)[0],
                        "dl_url": dl_url,
                        "dl_width": dl_w,
                        "dl_height": dl_h,
                    }
                    writer.writerow(row)
                    mf.flush()
                    manifest[title] = row
                    seen.add(title)
                    licences[lic] += 1
                    next_id += 1
                    kept += 1
                    q_kept += 1
                    stats["kept"] += 1
                    elapsed = time.time() - started
                    log(f"[{kept:5d} kept | {stats['candidates']:5d} cand | {elapsed/60:5.1f} min] {fid}  {width}x{height}->{dl_w}x{dl_h}  {lic}  {title[:70]}")

            try:
                for title in search_titles(query):
                    if done():
                        break
                    q_seen += 1
                    if title in seen or title in considered:
                        stats["duplicate"] += 1
                        continue
                    considered.add(title)
                    batch.append(title)
                    if len(batch) >= BATCH:
                        process(batch)
                        batch = []
            except RuntimeError as e:
                log(f"  ! search failed for {query!r}: {e}")
                stats["search_failed"] += 1
            if not done():
                process(batch)
            log(f"--- {query!r}: {q_seen} search hits, {q_kept} kept this query; total kept {kept}")
    except KeyboardInterrupt:
        log("\nInterrupted; progress is saved in the manifest.")
    finally:
        mf.close()

    # ------------------------------------------------------------ summary
    elapsed = time.time() - started
    total_bytes = sum(
        os.path.getsize(os.path.join(OUT_DIR, f))
        for f in os.listdir(OUT_DIR) if f.endswith(".jpg")
    )
    log("\n================ SUMMARY ================")
    log(f"elapsed:          {elapsed/60:.1f} min")
    log(f"candidates seen:  {stats['candidates']}")
    log(f"duplicates:       {stats['duplicate']}")
    log(f"kept this run:    {stats['kept']}")
    log(f"kept total:       {kept} (target {TARGET}, stop {STOP_AT})")
    log(f"files on disk:    {sum(1 for f in os.listdir(OUT_DIR) if f.endswith('.jpg'))}  "
        f"({total_bytes/1e6:.1f} MB)")
    log("rejections:")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        if k.startswith("reject:") or k in ("batch_failed", "search_failed", "download_failed_resume"):
            log(f"  {k:28s} {v}")
    log("licence breakdown (kept):")
    for k, v in licences.most_common():
        log(f"  {k:28s} {v}")
    log("top blocked keywords:")
    for k, v in [(k, v) for k, v in stats.most_common() if k.startswith("blocked:")][:15]:
        log(f"  {k[8:]:28s} {v}")
    log("top rejected licences:")
    for k, v in [(k, v) for k, v in stats.most_common() if k.startswith("badlic:")][:15]:
        log(f"  {k[7:]:28s} {v}")
    return 0 if kept >= TARGET else 1


if __name__ == "__main__":
    sys.exit(main())
