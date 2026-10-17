"""Make real-photo masters on the Modal volume.

Reads raw JPEGs from /raw/<source>/ on volume `claimshield-data`, writes
PNG masters with long side 768 (both sides multiples of 8) to
/data/real/masters/<id>.png, plus /data/real/manifest_<source>.jsonl with
id, source, original file, licence, author, damage flag, size and pHash.

Usage (plan.md §7.2):
    modal run data/prep_real.py --source cardd
    modal run data/prep_real.py --source commons --limit 50   # smoke test
"""

import json
import os
from pathlib import Path

import modal

VOL = modal.Volume.from_name("claimshield-data")
MNT = "/vol"
LONG_SIDE = 768

image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "pillow", "numpy", "imagehash"
)
app = modal.App("claimshield-prep-real", image=image)

# Per-source constants. Commons attribution comes from data/commons_manifest.csv,
# uploaded next to the images as /raw/commons/commons_manifest.csv.
SOURCES = {
    "cardd": {
        "glob": ["train2017/*.jpg", "val2017/*.jpg", "test2017/*.jpg"],
        "licence": "CarDD research licence (non-commercial, by approved form)",
        "author": "Wang, Li, Wu (CarDD, USTC)",
        "damage": True,  # every CarDD image carries at least one damage annotation
    },
    "commons": {
        "glob": ["*.jpg"],
        "licence": None,  # per file, from the manifest
        "author": None,
        "damage": True,  # all queries are damage queries; spot-check may flip some
    },
    "phone": {  # D's held-out phone photos, if they land as /raw/phone/*.jpg
        "glob": ["*.jpg", "*.jpeg", "*.JPG"],
        "licence": "Team-owned",
        "author": "Team",
        "damage": None,  # set from a damage.csv (filename,damage) if present
    },
}


def _fit(w, h):
    """Scale so the long side is LONG_SIDE, then round both sides down to a multiple of 8."""
    s = LONG_SIDE / max(w, h)
    nw, nh = max(8, int(w * s) // 8 * 8), max(8, int(h * s) // 8 * 8)
    return nw, nh


@app.function(volumes={MNT: VOL}, cpu=2, timeout=3600, max_containers=16)
def prep_batch(source: str, rel_paths: list[str], meta: dict) -> list[dict]:
    import imagehash
    from PIL import Image, ImageOps

    out_dir = Path(MNT) / "data/real/masters"
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for rel in rel_paths:
        src = Path(MNT) / "raw" / source / rel
        stem = Path(rel).stem
        mid = stem if stem.startswith(f"{source}_") else f"{source}_{stem}"  # commons files are already prefixed
        dst = out_dir / f"{mid}.png"
        try:
            im = Image.open(src)
            im = ImageOps.exif_transpose(im).convert("RGB")
            ow, oh = im.size
            if max(ow, oh) < 512:
                records.append({"id": mid, "status": "rejected_small", "orig_w": ow, "orig_h": oh})
                continue
            # Black-and-white or sepia photos (historic Commons uploads) don't look like claim
            # photos and would make "monochrome = real" learnable. Fakes are all colour.
            import numpy as np

            hsv = np.asarray(im.resize((128, 128)).convert("HSV"), np.float32)
            sat = float(hsv[..., 1].mean())
            hues = hsv[..., 0][hsv[..., 1] > 40]
            hue_std = float(hues.std()) if hues.size > 50 else 0.0
            # Single-tint prints (sepia, cyanotype) have colour but one hue: spread < 12, while
            # every colour photo checked, including a near-white car, is above 20.
            if source == "commons" and (sat < 20 or hue_std < 12):  # not CarDD: grey cars on tarmac are genuine
                dst.unlink(missing_ok=True)  # an earlier run may have written a master already
                records.append({"id": mid, "status": "rejected_monochrome", "sat_mean": round(sat, 1), "hue_std": round(hue_std, 1)})
                continue
            nw, nh = _fit(ow, oh)
            if not dst.exists():
                # Resize then crop the few pixels needed to hit multiples of 8.
                s = max(nw / ow, nh / oh)
                im2 = im.resize((max(nw, round(ow * s)), max(nh, round(oh * s))), Image.LANCZOS)
                left, top = (im2.width - nw) // 2, (im2.height - nh) // 2
                im2 = im2.crop((left, top, left + nw, top + nh))
                im2.save(dst, "PNG", optimize=False)  # no EXIF carried into the master
            else:
                im2 = Image.open(dst)
            ph = str(imagehash.phash(im2, hash_size=8))  # 64-bit perceptual hash
            m = meta.get(rel, {})
            records.append(
                {
                    "id": mid,
                    "status": "ok",
                    "source": source,
                    "file": f"data/real/masters/{mid}.png",
                    "orig_file": f"raw/{source}/{rel}",
                    "orig_w": ow,
                    "orig_h": oh,
                    "width": nw,
                    "height": nh,
                    "licence": m.get("licence", SOURCES[source]["licence"]),
                    "author": m.get("author", SOURCES[source]["author"]),
                    "page_url": m.get("page_url"),
                    "damage": m.get("damage", SOURCES[source]["damage"]),
                    "phash": ph,
                }
            )
        except Exception as e:  # noqa: BLE001
            records.append({"id": mid, "status": f"error: {e!r}"})
    VOL.commit()
    return records


@app.function(volumes={MNT: VOL}, timeout=600)
def list_raw(source: str) -> tuple[list[str], dict]:
    """List raw files for a source and load any per-file metadata."""
    import csv

    base = Path(MNT) / "raw" / source
    rels = []
    for g in SOURCES[source]["glob"]:
        rels += [str(p.relative_to(base)) for p in sorted(base.glob(g))]
    meta = {}
    man = base / "commons_manifest.csv"
    if source == "commons" and man.exists():
        with man.open(newline="") as f:
            for row in csv.DictReader(f):
                meta[row["filename"]] = {
                    "licence": row.get("licence"),
                    "author": row.get("author"),
                    "page_url": row.get("page_url"),
                }
    dmg = base / "damage.csv"
    if dmg.exists():
        with dmg.open(newline="") as f:
            for row in csv.DictReader(f):
                meta.setdefault(row["filename"], {})["damage"] = row["damage"].strip().lower() in ("1", "true", "yes")
    return rels, meta


@app.function(volumes={MNT: VOL}, timeout=600)
def write_manifest(source: str, records: list[dict]) -> str:
    out = Path(MNT) / f"data/real/manifest_{source}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    VOL.commit()
    return str(out)


@app.local_entrypoint()
def main(source: str = "cardd", limit: int = 0, batch: int = 50):
    rels, meta = list_raw.remote(source)
    if limit:
        rels = rels[:limit]
    print(f"{source}: {len(rels)} raw files")
    batches = [rels[i : i + batch] for i in range(0, len(rels), batch)]
    records = []
    for recs in prep_batch.map([source] * len(batches), batches, [meta] * len(batches)):
        records += recs
        ok = sum(r["status"] == "ok" for r in records)
        print(f"  {len(records)}/{len(rels)} processed, {ok} ok", flush=True)
    path = write_manifest.remote(source, records)
    bad = [r for r in records if r["status"] != "ok"]
    print(f"manifest: {path}")
    print(f"ok={len(records) - len(bad)} rejected/errors={len(bad)}")
    for r in bad[:10]:
        print("  ", r)
    # Keep a local copy of the manifest for the dataset card (images stay on the volume).
    local = Path("data") / f"manifest_real_{source}.jsonl"
    local.write_text("".join(json.dumps(r) + "\n" for r in records))
    print(f"local copy: {local}")
