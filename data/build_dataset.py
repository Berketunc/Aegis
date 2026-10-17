"""Build the train/val/test dataset on the Modal volume (plan.md §7.4).

Inputs on volume `claimshield-data`:
  /data/real/manifest_<source>.jsonl     real masters (from prep_real.py)
  /data/real/masters/<id>.png
  /data/fake/<set>/manifest.jsonl        set in {regen, inflate, sdxl, flux} (from generate_modal.py)
  /data/fake/<set>/<file>.png

Outputs:
  /data/dataset/<split>/<label>/<id>.jpg   label in {real, fake}; split in {train, val, test}
  /data/dataset/test_variants/<variant>/<label>/<id>.jpg   compression suite on the test split
  /data/dataset/manifest.csv               one row per image (also copied to data/dataset_manifest.csv)
  data/dataset_card.md                     counts per source / kind / split / damage, filters, rejects

Steps: dedupe reals by 64-bit pHash (Hamming <= 6); group a real with its regen and
inflate children; SDXL images are their own groups; split groups 70/15/15 by seed;
FLUX and phone photos go to test only; normalise (fakes get a camera-JPEG pass first,
then every image: short side random 512-768, JPEG q65-95, no EXIF); shortcut test.

Usage:
    modal run data/build_dataset.py                # full build
    modal run data/build_dataset.py --limit 200    # quick build for pipeline tests
"""

import csv
import io
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import modal

VOL = modal.Volume.from_name("claimshield-data")
MNT = "/vol"
SEED = 20261017

image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "pillow", "numpy", "scikit-learn"
)
app = modal.App("claimshield-build-dataset", image=image)

FAKE_SETS = {"regen": "regenerated", "inflate": "inflated", "sdxl": "sdxl", "flux": "flux"}
TEST_ONLY_KINDS = {"flux", "phone"}
VARIANTS = ("jpeg_q50", "resize_half", "screenshot")


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ---------------------------------------------------------------- planning (one container)
@app.function(volumes={MNT: VOL}, timeout=1800, cpu=2)
def plan(limit: int = 0, fresh: bool = True) -> dict:
    """Read manifests, dedupe, group, split. Returns the list of items to normalise."""
    import shutil

    root = Path(MNT)
    VOL.reload()
    if fresh:  # normalise() skips files that exist, so stale outputs from older settings must go
        shutil.rmtree(root / "data/dataset", ignore_errors=True)
        VOL.commit()
    rng = random.Random(SEED)
    rejects = Counter()

    # ---- reals
    reals = []
    for man in sorted((root / "data/real").glob("manifest_*.jsonl")):
        for line in man.open():
            r = json.loads(line)
            if r.get("status") != "ok":
                rejects[f"real_{r.get('status')}"] += 1
                continue
            if not (root / r["file"]).exists():
                rejects["real_missing_file"] += 1
                continue
            reals.append(r)
    reals.sort(key=lambda r: r["id"])
    if limit:
        rng.shuffle(reals)
        reals = sorted(reals[:limit], key=lambda r: r["id"])

    # ---- measured damage (data/damage_tag.py). Prompts and search terms are not reliable labels.
    tags, scene_ok = {}, {}
    for tf in (root / "data/tags").glob("damage_*.jsonl") if (root / "data/tags").exists() else []:
        for line in tf.open():
            t = json.loads(line)
            tags[t["id"]] = t["p_damage"]
            if "scene_hard_reject" in t:
                scene_ok[t["id"]] = not t["scene_hard_reject"]
    cardd_p = sorted(v for k, v in tags.items() if k.startswith("cardd_"))
    # Threshold: the 10th percentile of CarDD (all genuinely damaged), so the tag has ~90% recall.
    dmg_thr = cardd_p[len(cardd_p) // 10] if cardd_p else 0.5
    for r in reals:
        if r["source"] != "cardd" and r["id"] in tags:
            r["damage"] = tags[r["id"]] >= dmg_thr
    # Commons "car crash" searches also return rescue scenes with people, charts and antique
    # prints. Drop those (ethics and shortcut risk). Untagged Commons photos are dropped too.
    n0 = len(reals)
    reals = [r for r in reals if r["source"] != "commons" or scene_ok.get(r["id"], False)]
    rejects["real_commons_off_target_scene"] += n0 - len(reals)

    # ---- dedupe reals by 64-bit pHash, Hamming <= 6, all pairs, greedy in id order
    import numpy as np

    bits = np.array([[int(c) for c in bin(int(r["phash"], 16))[2:].zfill(64)] for r in reals], np.float32)
    keep_mask = np.ones(len(reals), bool)
    for i in range(len(reals)):
        if not keep_mask[i]:
            continue
        d = np.abs(bits[i + 1 :] - bits[i]).sum(1)  # Hamming distance to every later photo
        dup = np.where(d <= 6)[0] + i + 1
        keep_mask[dup] = False
    rejects["real_near_duplicate"] += int((~keep_mask).sum())
    reals = [r for r, k in zip(reals, keep_mask) if k]
    real_by_id = {r["id"]: r for r in reals}
    real_ids = set(real_by_id)

    # ---- fakes
    fakes = []
    for set_name, kind in FAKE_SETS.items():
        # Per-image records are written as each image lands, so runs still in progress count.
        # manifest.jsonl is only rewritten when a run finishes.
        rec_dir = root / "data/fake" / set_name / "records"
        man = root / "data/fake" / set_name / "manifest.jsonl"
        if rec_dir.is_dir():
            lines = []
            for rp in sorted(rec_dir.glob("*.json")):
                try:
                    lines.append(rp.read_text())
                except OSError:
                    rejects[f"fake_{kind}_unreadable_record"] += 1
        elif man.exists():
            lines = man.read_text().splitlines()
        else:
            continue
        for line in lines:
            try:
                f = json.loads(line)
            except json.JSONDecodeError:
                rejects[f"fake_{kind}_bad_record"] += 1
                continue
            p = root / f["file"]  # generator writes volume-relative paths like data/fake/sdxl/x.png
            if not p.exists():
                rejects[f"fake_{kind}_missing_file"] += 1
                continue
            if f.get("rejected"):
                rejects[f"fake_{kind}_spotcheck_reject"] += 1
                continue
            f["kind"], f["file"] = kind, str(p.relative_to(root))
            if kind in ("regenerated", "inflated"):
                if f.get("source_id") not in real_ids:
                    rejects[f"fake_{kind}_orphan"] += 1  # parent was deduped or missing
                    continue
                if kind == "regenerated":  # same picture as the parent, so same damage flag
                    f["damage"] = real_by_id[f["source_id"]].get("damage")
            elif f["id"] in tags:  # sdxl / flux: what the picture shows, not what the prompt asked for
                f["prompt_damage"] = f.get("damage")
                f["damage"] = tags[f["id"]] >= dmg_thr
            fakes.append(f)

    # ---- damage-share balance (plan.md §7.1): undamaged fakes may not outnumber, in share,
    # the undamaged reals, or the model learns "undamaged = fake". Excess undamaged
    # fully generated fakes are dropped deterministically.
    n_real_undmg = sum(1 for r in reals if r.get("damage") is False)
    real_undmg_share = n_real_undmg / max(1, len(reals))
    undmg_fab = [f for f in fakes if f["kind"] in ("sdxl", "flux") and f.get("damage") is False]
    undmg_other = sum(1 for f in fakes if f["kind"] not in ("sdxl", "flux") and f.get("damage") is False)
    allowed = max(0, int(real_undmg_share * len(fakes)) - undmg_other)
    if len(undmg_fab) > allowed:
        rng_b = random.Random(SEED + 1)
        undmg_fab.sort(key=lambda f: f["id"])
        rng_b.shuffle(undmg_fab)
        drop = {f["id"] for f in undmg_fab[allowed:]}
        rejects["fake_undamaged_over_real_share"] += len(drop)
        fakes = [f for f in fakes if f["id"] not in drop]

    # ---- orientation / size matching for fully generated fakes. Generators output square and
    # portrait frames far more often than claim photos are taken. Each fake gets a target size
    # drawn from real photos of a target orientation, reached by cropping only (never upscaling):
    # landscape and square frames -> landscape; portrait frames -> portrait, kept only up to the
    # real pile's portrait share (turning a portrait into a landscape would need upscaling).
    orient_of = lambda w, h: "landscape" if w > h else "portrait" if h > w else "square"  # noqa: E731
    real_sizes = {"landscape": [], "portrait": []}
    for r0 in reals:
        o = orient_of(r0["width"], r0["height"])
        real_sizes["portrait" if o == "portrait" else "landscape"].append((r0["width"], r0["height"]))
    p_portrait = len(real_sizes["portrait"]) / max(1, len(reals))
    target_orient, drop = {}, set()
    for kind in ("sdxl", "flux"):
        fab = sorted((f for f in fakes if f["kind"] == kind), key=lambda f: f["id"])
        portraits = [f for f in fab if orient_of(f["width"], f["height"]) == "portrait"]
        random.Random(f"{SEED}:portrait:{kind}").shuffle(portraits)
        keep_p = round(p_portrait * len(fab)) if real_sizes["portrait"] else 0
        for i, f in enumerate(portraits):
            if i < keep_p:
                target_orient[f["id"]] = "portrait"
            else:
                drop.add(f["id"])
        for f in fab:
            if orient_of(f["width"], f["height"]) != "portrait":
                target_orient[f["id"]] = "landscape"
        rejects[f"fake_{kind}_portrait_over_real_share"] += sum(1 for f in portraits[keep_p:])
    fakes = [f for f in fakes if f["id"] not in drop]

    # ---- groups
    items = []
    for r in reals:
        items.append(
            dict(id=r["id"], label="real", kind="real", source=r["source"], group=r["id"],
                 damage=r.get("damage"), src=r["file"], licence=r.get("licence"), author=r.get("author"))
        )
    for f in fakes:
        group = f["source_id"] if f["kind"] in ("regenerated", "inflated") else f["id"]
        src_name = f["kind"] if f["kind"] in ("sdxl", "flux") else real_by_id[f["source_id"]]["source"]
        items.append(
            dict(id=f["id"], label="fake", kind=f["kind"], source=src_name, group=group,
                 damage=f.get("damage", True), src=f["file"], licence="generated", author="generated")
        )

    # ---- split by group; test-only kinds pinned to test; stratify by real source
    groups = defaultdict(list)
    for it in items:
        groups[it["group"]].append(it)
    split_of = {}
    by_source = defaultdict(list)
    for g, its in groups.items():
        if any(it["kind"] in TEST_ONLY_KINDS or it["source"] in TEST_ONLY_KINDS for it in its):
            split_of[g] = "test"
        else:
            by_source[its[0]["source"]].append(g)
    for src, gs in by_source.items():
        gs = sorted(gs)
        rng.shuffle(gs)
        n = len(gs)
        n_train, n_val = int(n * 0.70), int(n * 0.15)
        for i, g in enumerate(gs):
            split_of[g] = "train" if i < n_train else "val" if i < n_train + n_val else "test"
    for it in items:
        it["split"] = split_of[it["group"]]
        # per-item deterministic parameters for normalisation
        r = random.Random(f"{SEED}:{it['id']}")
        # Camera-JPEG pass only for fully generated pixels. Inflated fakes are a real photo with a
        # pasted patch, so they already carry the real photo's camera JPEG history; another pass
        # would give them one more compression round than real photos (a whole-image shortcut).
        it["cam_q"] = r.randint(85, 98) if it["kind"] in ("regenerated", "sdxl", "flux") else None
        it["short_side"] = r.randint(512, 768)
        it["q"] = r.randint(65, 95)
        it["out"] = f"data/dataset/{it['split']}/{it['label']}/{it['id']}.jpg"
        # Fully generated fakes come in generator-native aspect ratios (square, 9:7, ...).
        # Match each one to a real photo's size so resampling history can't separate the classes.
        if it["kind"] in ("sdxl", "flux"):
            it["match_size"] = list(r.choice(real_sizes[target_orient[it["id"]]]))

    rejects["info_damage_threshold_x1000"] = int(round(dmg_thr * 1000))
    return {"items": items, "rejects": dict(rejects)}


# ---------------------------------------------------------------- normalise (fan out)
def _jpeg_roundtrip(im, q):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q, subsampling=2)  # 4:2:0 like a phone camera
    buf.seek(0)
    from PIL import Image

    return Image.open(buf).convert("RGB")


@app.function(volumes={MNT: VOL}, timeout=3600, cpu=2, max_containers=24)
def normalise(batch: list[dict]) -> list[dict]:
    from PIL import Image, ImageOps

    root = Path(MNT)
    out = []
    for it in batch:
        dst = root / it["out"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not dst.exists():
                im = Image.open(root / it["src"])
                im = ImageOps.exif_transpose(im).convert("RGB")
                if it["cam_q"]:  # camera-JPEG pass for fakes at native size
                    im = _jpeg_roundtrip(im, it["cam_q"])
                if it.get("match_size"):  # same path a real photo takes: camera JPEG, then resize to master size
                    tw, th = it["match_size"]
                    w, h = im.size
                    k = max(tw / w, th / h)  # scale so the target fits, then centre-crop the excess
                    im = im.resize((max(tw, round(w * k)), max(th, round(h * k))), Image.LANCZOS)
                    left, top = (im.width - tw) // 2, (im.height - th) // 2
                    im = im.crop((left, top, left + tw, top + th))
                w, h = im.size
                s = it["short_side"] / min(w, h)
                im = im.resize((max(8, round(w * s)), max(8, round(h * s))), Image.LANCZOS)
                im.save(dst, "JPEG", quality=it["q"], subsampling=2)  # PIL writes no EXIF here
            with Image.open(dst) as im:
                w, h = im.size
                small = im.convert("RGB").resize((64, 64), Image.BILINEAR)
                hsv = small.convert("HSV")
                lum = small.convert("L")
                import numpy as np

                rgb = np.asarray(small, np.float32)
                stats = {
                    "mean_r": float(rgb[..., 0].mean()), "mean_g": float(rgb[..., 1].mean()), "mean_b": float(rgb[..., 2].mean()),
                    "lum_std": float(np.asarray(lum, np.float32).std()),
                    "sat_mean": float(np.asarray(hsv, np.float32)[..., 1].mean()),
                }
            out.append({**it, "width": w, "height": h, "bytes": dst.stat().st_size, "status": "ok", **stats})
        except Exception as e:  # noqa: BLE001
            out.append({**it, "status": f"error: {e!r}"})
    VOL.commit()
    return out


@app.function(volumes={MNT: VOL}, timeout=3600, cpu=2, max_containers=24)
def make_variants(batch: list[dict]) -> int:
    """Compression suite for the test split: JPEG q50, half-size, screenshot-style re-encode."""
    from PIL import Image

    root = Path(MNT)
    n = 0
    for it in batch:
        src = root / it["out"]
        im = Image.open(src).convert("RGB")
        for v in VARIANTS:
            dst = root / f"data/dataset/test_variants/{v}/{it['label']}/{it['id']}.jpg"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                continue
            if v == "jpeg_q50":
                im.save(dst, "JPEG", quality=50, subsampling=2)
            elif v == "resize_half":
                im.resize((im.width // 2, im.height // 2), Image.BILINEAR).save(dst, "JPEG", quality=85)
            else:  # screenshot: PNG round trip, slight downscale, then a generic JPEG q80
                buf = io.BytesIO()
                im.resize((int(im.width * 0.9), int(im.height * 0.9)), Image.BICUBIC).save(buf, "PNG")
                Image.open(buf).convert("RGB").save(dst, "JPEG", quality=80, subsampling=0)
            n += 1
    VOL.commit()
    return n


@app.function(volumes={MNT: VOL}, timeout=600)
def finalise(rows: list[dict]) -> dict:
    """Write the manifest, run the shortcut test, and return counts for the dataset card."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler

    root = Path(MNT)
    cols = ["id", "label", "kind", "source", "group", "split", "damage", "width", "height", "bytes", "q", "out", "licence", "author", "mean_r", "mean_g", "mean_b", "lum_std", "sat_mean"]
    man = root / "data/dataset/manifest.csv"
    with man.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    VOL.commit()

    # Shortcut tests (plan.md §7.4): a logistic regression on features that carry no
    # generation traces. Trained on train, scored on val. Each should sit near 0.5.
    FILE_FEATS = lambda r: [r["bytes"], r["width"], r["height"], r["width"] * r["height"], r["bytes"] / (r["width"] * r["height"])]  # noqa: E731
    COLOUR_FEATS = lambda r: [r["mean_r"], r["mean_g"], r["mean_b"], r["lum_std"], r["sat_mean"]]  # noqa: E731
    DIMS_FEATS = lambda r: [r["width"], r["height"], r["width"] / r["height"], r["width"] * r["height"]]  # noqa: E731
    BYTES_FEATS = lambda r: [r["bytes"], r["bytes"] / (r["width"] * r["height"])]  # noqa: E731

    def shortcut_auroc(feats, keep=lambda r: True):
        def xy(split):
            rs = [r for r in rows if r["split"] == split and keep(r)]
            return np.array([feats(r) for r in rs], float), np.array([r["label"] == "fake" for r in rs], int)

        Xtr, ytr = xy("train")
        Xva, yva = xy("val")
        if len(set(ytr)) < 2 or len(set(yva)) < 2:
            return None
        sc = StandardScaler().fit(Xtr)
        clf = LogisticRegression(max_iter=2000).fit(sc.transform(Xtr), ytr)
        return float(roc_auc_score(yva, clf.predict_proba(sc.transform(Xva))[:, 1]))

    matched = lambda r: r["kind"] in ("real", "regenerated")  # noqa: E731
    shortcut = {
        "file_all": shortcut_auroc(FILE_FEATS),
        "dims_all": shortcut_auroc(DIMS_FEATS),
        "bytes_all": shortcut_auroc(BYTES_FEATS),
        "dims_real_vs_regen": shortcut_auroc(DIMS_FEATS, matched),
        "bytes_real_vs_regen": shortcut_auroc(BYTES_FEATS, matched),
        "file_real_vs_regen": shortcut_auroc(FILE_FEATS, matched),
        "colour_all": shortcut_auroc(COLOUR_FEATS),
        "colour_real_vs_regen": shortcut_auroc(COLOUR_FEATS, matched),
    }
    auroc = shortcut["file_all"]

    counts = Counter((r["split"], r["label"], r["kind"], r["source"], bool(r["damage"])) for r in rows)
    return {"auroc_shortcut": auroc, "shortcut": shortcut, "counts": [list(k) + [v] for k, v in counts.items()], "manifest": str(man)}


# ---------------------------------------------------------------- driver
@app.local_entrypoint()
def main(limit: int = 0, batch: int = 40, variants: bool = True, fresh: bool = True):
    p = plan.remote(limit, fresh)
    items, rejects = p["items"], p["rejects"]
    print(f"planned {len(items)} images; rejects: {rejects}")
    batches = [items[i : i + batch] for i in range(0, len(items), batch)]
    rows = []
    for out in normalise.map(batches):
        rows += out
        print(f"  normalised {len(rows)}/{len(items)}", flush=True)
    errs = [r for r in rows if r["status"] != "ok"]
    rows = [r for r in rows if r["status"] == "ok"]
    for e in errs[:10]:
        print("  error:", e["id"], e["status"])

    if variants:
        test_rows = [r for r in rows if r["split"] == "test"]
        tb = [test_rows[i : i + batch] for i in range(0, len(test_rows), batch)]
        n = sum(make_variants.map(tb))
        print(f"  wrote {n} test-variant images")

    res = finalise.remote(rows)
    fmt = lambda v: "n/a" if v is None else f"{v:.3f}"  # noqa: E731
    for k, v in res["shortcut"].items():
        print(f"shortcut AUROC {k:<22} {fmt(v)}  (want ~0.5)")

    # ---- local artefacts: manifest copy + dataset card
    Path("data").mkdir(exist_ok=True)
    with open("data/dataset_manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "label", "kind", "source", "group", "split", "damage", "width", "height", "bytes", "q", "out", "licence", "author", "mean_r", "mean_g", "mean_b", "lum_std", "sat_mean"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    by = lambda key: Counter(key(r) for r in rows)  # noqa: E731
    lines = ["# Dataset card", "", f"Built with seed {SEED}. {len(rows)} images. Images live on the Modal volume `claimshield-data`; only manifests are in git.", ""]
    lines += ["## Counts by split and label", "", "| split | real | fake | total |", "|---|---|---|---|"]
    for s in ("train", "val", "test"):
        nr = sum(1 for r in rows if r["split"] == s and r["label"] == "real")
        nf = sum(1 for r in rows if r["split"] == s and r["label"] == "fake")
        lines.append(f"| {s} | {nr} | {nf} | {nr + nf} |")
    lines += ["", "## Counts by kind and split", "", "| kind | train | val | test |", "|---|---|---|---|"]
    for k in sorted(by(lambda r: r["kind"])):
        c = [sum(1 for r in rows if r["kind"] == k and r["split"] == s) for s in ("train", "val", "test")]
        lines.append(f"| {k} | {c[0]} | {c[1]} | {c[2]} |")
    lines += ["", "## Counts by source", "", "| source | images |", "|---|---|"]
    for k, v in sorted(by(lambda r: r["source"]).items()):
        lines.append(f"| {k} | {v} |")
    dmg_real = [bool(r["damage"]) for r in rows if r["label"] == "real"]
    dmg_fake = [bool(r["damage"]) for r in rows if r["label"] == "fake"]
    share_r = 100 * sum(dmg_real) / max(1, len(dmg_real))
    share_f = 100 * sum(dmg_fake) / max(1, len(dmg_fake))
    lines += ["", "## Damage share (must be within ~10 points, plan.md §7.1)", "",
              f"- Real: {share_r:.1f}% damaged", f"- Fake: {share_f:.1f}% damaged", f"- Gap: {abs(share_r - share_f):.1f} points"]
    sc = res["shortcut"]
    lines += ["", "## Shortcut tests", "",
              "Logistic regression on features that carry no generation traces, trained on train and scored on val. Target ≈ 0.5 for each.", "",
              "| Features | All fakes | Real vs regenerated only |", "|---|---|---|",
              f"| File size and dimensions (plan.md §7.4) | {fmt(sc['file_all'])} | {fmt(sc['file_real_vs_regen'])} |",
              f"| Dimensions and aspect only | {fmt(sc['dims_all'])} | {fmt(sc['dims_real_vs_regen'])} |",
              f"| File size only | {fmt(sc['bytes_all'])} | {fmt(sc['bytes_real_vs_regen'])} |",
              f"| Global colour statistics | {fmt(sc['colour_all'])} | {fmt(sc['colour_real_vs_regen'])} |", "",
              "Colour statistics on all fakes can legitimately sit above 0.5, because SDXL scenes differ in content from claim photos. "
              "The real-vs-regenerated column is content-matched, so it should stay near 0.5.", ""]
    lines += ["## Processing", "",
              "- Real masters: EXIF orientation applied, long side 768 px, sides multiples of 8, PNG, EXIF dropped.",
              "- Regenerated, SDXL and FLUX fakes first get a camera-JPEG pass (quality 85–98) at native size. Inflated fakes skip it, since their base is a real photo that already went through a camera JPEG.",
              "- Then every image: short side resized to a random 512–768 px (LANCZOS), saved as JPEG quality 65–95, 4:2:0, no EXIF.",
              "- Near-duplicate reals removed by 64-bit pHash, Hamming ≤ 6.",
              "- Groups: a real photo with its regenerated and inflated children; each SDXL image alone. Split 70/15/15 by group, stratified by real source.",
              "- FLUX.1-schnell images and team phone photos are test only.",
              "- SDXL and FLUX images are cropped (not upscaled) to a size drawn from the real photos after their camera-JPEG pass. Square and landscape frames become landscape; portrait frames are kept only up to the real pile's portrait share. Size, aspect and resampling history then match the real pile.",
              "- Damage is measured, not assumed: SDXL, FLUX and Commons images are tagged by zero-shot CLIP ViT-L/14 (data/damage_tag.py), thresholded at the 10th percentile of CarDD scores (~90% recall on known damage). Undamaged fully generated fakes are capped at the real pile's undamaged share, so the model cannot learn 'undamaged = fake'.",
              f"- Test-split compression suite: {', '.join(VARIANTS)} under `data/dataset/test_variants/`.", ""]
    lines += ["## Rejects and filters", ""] + [f"- {k}: {v}" for k, v in sorted(rejects.items())] + [f"- normalise errors: {len(errs)}", ""]
    lines += ["## Licences", "",
              "- CarDD: non-commercial research and education licence (form sent to the authors). Collected by the authors before May 2022; no AI-generated content.",
              "- Wikimedia Commons: CC0, public domain, CC BY, CC BY-SA only; latest version uploaded before 1 Jan 2022; per-file attribution in `data/commons_manifest.csv`. Filtered by a title/category block list, a monochrome filter, and a zero-shot CLIP scene check that drops rescue scenes and bystanders, charts, and antique or sepia prints. Remaining off-target scenes (motorsport, emergency vehicles) are genuine photos, so they stay in the real pile, each with a regenerated twin.",
              "- Team phone photos: team-owned, test only.",
              "- Generated images: made by us with Stable Diffusion 2 inpainting, SDXL base 1.0 and FLUX.1-schnell on Modal.", ""]
    Path("data/dataset_card.md").write_text("\n".join(lines))
    print("wrote data/dataset_manifest.csv and data/dataset_card.md")
