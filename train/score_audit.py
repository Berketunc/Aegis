"""Residual-shortcut audit of a trained detector (local; needs numpy).

The dataset-level shortcut tests check that size, file size and colour cannot separate the
classes. This checks the trained model: among real photos, does the score track things that
are not generation traces (JPEG quality, size, compressibility, colour)? And does the RED rate
differ across those subgroups? Writes eval/runs/<run>_<agg>_audit.md.

Inputs: data/dataset_manifest.csv (full columns, from the volume) and the run's
test_probs_<agg>.npy + thresholds from eval/runs/<run>_<agg>_eval.json.

Usage:
    modal volume get claimshield-data /data/models/<run>/test_probs_mean.npy models/<run>/
    python train/score_audit.py --run det-r18-v3 --agg mean
"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def spearman(a, b):
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--agg", default="mean")
    a = ap.parse_args()

    p = np.load(f"models/{a.run}/test_probs_{a.agg}.npy")
    ev = json.loads(Path(f"eval/runs/{a.run}_{a.agg}_eval.json").read_text())
    tau_red, tau_amber = ev["thresholds"]["tau_red"], ev["thresholds"]["tau_amber"]
    rows = [r for r in csv.DictReader(open("data/dataset_manifest.csv")) if r["split"] == "test"]
    assert len(rows) == len(p), f"manifest test rows {len(rows)} != scores {len(p)}"

    feats = {
        "Final JPEG quality": lambda r: float(r["q"]),
        "Short side (px)": lambda r: min(float(r["width"]), float(r["height"])),
        "Bytes per pixel": lambda r: float(r["bytes"]) / (float(r["width"]) * float(r["height"])),
        "Brightness": lambda r: (float(r["mean_r"]) + float(r["mean_g"]) + float(r["mean_b"])) / 3,
        "Saturation": lambda r: float(r["sat_mean"]),
    }
    lines = [f"# Score audit: `{a.run}` ({a.agg})", "",
             "Spearman correlation between the model's score and features that carry no generation traces, "
             "within each class. Near 0 is good; a large value among real photos means some honest photos are "
             "flagged for reasons unrelated to AI editing.", "",
             "| Feature | Real photos | Fakes |", "|---|---|---|"]
    is_real = np.array([r["label"] == "real" for r in rows])
    for name, f in feats.items():
        vals = np.array([f(r) for r in rows])
        lines.append(f"| {name} | {spearman(vals[is_real], p[is_real]):+.2f} | {spearman(vals[~is_real], p[~is_real]):+.2f} |")

    lines += ["", f"RED and AMBER rates among **real** test photos by subgroup (τ_red = {tau_red:.3f}, τ_amber = {tau_amber:.3f}).", "",
              "| Subgroup | n | RED rate | AMBER-or-RED rate |", "|---|---|---|---|"]
    pr = p[is_real]
    rr = [r for r, k in zip(rows, is_real) if k]

    def band(label, mask):
        mask = np.asarray(mask)
        if mask.sum():
            lines.append(f"| {label} | {int(mask.sum())} | {np.mean(pr[mask] >= tau_red):.3f} | {np.mean(pr[mask] >= tau_amber):.3f} |")

    q = np.array([float(r["q"]) for r in rr])
    for lo, hi in ((65, 75), (75, 85), (85, 96)):
        band(f"JPEG quality {lo}–{hi - 1}", (q >= lo) & (q < hi))
    ss = np.array([min(float(r["width"]), float(r["height"])) for r in rr])
    for lo, hi in ((512, 600), (600, 690), (690, 769)):
        band(f"Short side {lo}–{hi - 1}", (ss >= lo) & (ss < hi))
    bpp = np.array([float(r["bytes"]) / (float(r["width"]) * float(r["height"])) for r in rr])
    t1, t2 = np.quantile(bpp, [1 / 3, 2 / 3])
    band("Least compressible third (most detail)", bpp >= t2)
    band("Middle third", (bpp >= t1) & (bpp < t2))
    band("Most compressible third (smoothest)", bpp < t1)
    src = np.array([r["source"] for r in rr])
    for s in sorted(set(src)):
        band(f"Source: {s}", src == s)
    dmg = np.array([str(r["damage"]).lower() in ("true", "1") for r in rr])
    band("Damaged", dmg)
    band("Undamaged", ~dmg)

    out = Path(f"eval/runs/{a.run}_{a.agg}_audit.md")
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
