"""Evaluate a trained detector on the full matrix (plan.md §7.6) and fit thresholds (§7.7).

Scores every test image (and the compression-suite variants) with the production 3-crop
recipe, calibrates with the run's temperature, and reports:
  - in-distribution: real vs regenerated + inflated + sdxl
  - flux hold-out: real vs flux
  - phone photos (held out): FPR of real phone photos at tau_red / tau_amber
  - inflation only: real vs inflated
  - real damage photos only: FPR at tau_red / tau_amber (the number that decides usability)
  - compression suite: jpeg_q50, resize_half, screenshot
  - FPR per real subgroup (source)
Metrics: AUROC, TPR at 1% FPR, ECE, FPR at the thresholds. Thresholds are fitted on the
val split: tau_red = real FPR <= 1%, tau_amber = real FPR <= 5% (Modal FP32 scores;
refit on LinuxONE scores with fit_thresholds_linuxone.py once B has parity numbers).

Outputs: /data/models/<run>/eval.json on the volume; eval/results.md, eval/results.json,
and eval/thresholds_<version>.json locally.

Usage:
    modal run train/eval_modal.py --run det-r18-v1 --thresholds-version t-0
"""

import json
from pathlib import Path

import modal

VOL = modal.Volume.from_name("claimshield-data")
MNT = "/vol"
VARIANTS = ("jpeg_q50", "resize_half", "screenshot")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.4.1", "torchvision==0.19.1", index_url="https://download.pytorch.org/whl/cu121")
    .pip_install("pillow", "numpy", "scikit-learn")
    .add_local_python_source("train_modal")
)
app = modal.App("claimshield-eval", image=image)


def ece(probs, labels, bins=15):
    import numpy as np

    probs, labels = np.asarray(probs), np.asarray(labels)
    edges = np.linspace(0, 1, bins + 1)
    e = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (probs > lo) & (probs <= hi)
        if m.any():
            e += m.mean() * abs(probs[m].mean() - labels[m].mean())
    return float(e)


@app.function(volumes={MNT: VOL}, gpu="L40S", timeout=2 * 3600, cpu=8, memory=32768)
def evaluate(run: str, agg: str = "mean") -> dict:
    import numpy as np
    import torch
    import torch.nn as nn
    from sklearn.metrics import roc_auc_score, roc_curve
    from torch.utils.data import DataLoader
    from torchvision import models

    from train_modal import CROP, ImgDS, no_upscale_of, read_manifest

    root = Path(MNT)
    d = root / "data/models" / run
    T = json.loads((d / "temperature.json").read_text())["temperature"]
    NO_UP = no_upscale_of(d)
    model = models.resnet18()
    model.fc = nn.Linear(512, 1)
    model.load_state_dict(torch.load(d / "best.pt", map_location="cpu"))
    model = model.cuda().eval()

    def crop_logits(rows, path_key="out"):
        rows = [dict(r, out=r[path_key]) for r in rows]
        mode = "grid16" if agg == "grid16max" else True
        k = 16 if mode == "grid16" else 3
        dl = DataLoader(ImgDS(rows, root, crops=mode, no_upscale=NO_UP), batch_size=32 if k == 3 else 8, num_workers=8, pin_memory=True)
        out = []
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            for x, _, _ in dl:
                b = x.shape[0]
                out.append(model(x.view(-1, 3, CROP, CROP).cuda()).view(b, k).float().cpu().numpy())
        return np.concatenate(out)

    # agg: how the 3 crop logits become one. "mean" is the plan's recipe; "max" keeps a small
    # edited patch that only one crop sees from being averaged away.
    # "grid16max": max over the 4x4 heatmap scan, which covers the whole frame, so a small
    # edit anywhere is seen by at least one crop.
    reduce = (lambda L: L.mean(1)) if agg == "mean" else (lambda L: L.max(1))  # noqa: E731

    va = read_manifest(root, "val")
    va_L = reduce(crop_logits(va))
    va_y = np.array([r["label"] == "fake" for r in va], int)
    if agg != "mean":  # the saved temperature was fitted on mean logits; refit for this aggregation
        grid = np.logspace(-1.5, 1.5, 301)
        nll = [np.mean(np.logaddexp(0, -va_L / g) * va_y + np.logaddexp(0, va_L / g) * (1 - va_y)) for g in grid]
        T = float(grid[int(np.argmin(nll))])

    def score_rows(rows, path_key="out"):
        return list(1 / (1 + np.exp(-reduce(crop_logits(rows, path_key)) / T)))  # calibrated probability

    # ---- thresholds from val reals
    va_p = 1 / (1 + np.exp(-va_L / T))
    real_p = np.sort(va_p[va_y == 0])
    tau_red = float(np.quantile(real_p, 0.99))
    tau_amber = float(np.quantile(real_p, 0.95))
    thresholds = {"tau_red": tau_red, "tau_amber": tau_amber, "fitted_on": "val reals, Modal FP32", "n_val_real": int((va_y == 0).sum())}

    def block(p, y, name):
        p, y = np.asarray(p), np.asarray(y)
        r = {"name": name, "n_real": int((y == 0).sum()), "n_fake": int((y == 1).sum())}
        if (y == 0).any():
            r["fpr_at_red"] = float((p[y == 0] >= tau_red).mean())
            r["fpr_at_amber"] = float((p[y == 0] >= tau_amber).mean())
        if (y == 1).any():
            r["tpr_at_red"] = float((p[y == 1] >= tau_red).mean())
            r["tpr_at_amber"] = float((p[y == 1] >= tau_amber).mean())
        if (y == 0).any() and (y == 1).any():
            r["auroc"] = float(roc_auc_score(y, p))
            fpr, tpr, _ = roc_curve(y, p)
            r["tpr_at_fpr1"] = float(tpr[fpr <= 0.01].max())
            r["ece"] = ece(p, y)
        return r

    te = read_manifest(root, "test")
    te_p = np.array(score_rows(te))
    te_y = np.array([r["label"] == "fake" for r in te], int)
    kind = np.array([r["kind"] for r in te])
    src = np.array([r["source"] for r in te])
    dmg = np.array([str(r["damage"]).lower() in ("true", "1") for r in te])
    is_real = te_y == 0

    results = {"run": run, "agg": agg, "no_upscale": NO_UP, "temperature": T, "thresholds": thresholds, "blocks": []}
    results["blocks"].append(block(te_p[is_real | np.isin(kind, ["regenerated", "inflated", "sdxl"])], te_y[is_real | np.isin(kind, ["regenerated", "inflated", "sdxl"])], "in_distribution"))
    results["blocks"].append(block(te_p[is_real | (kind == "flux")], te_y[is_real | (kind == "flux")], "flux_holdout"))
    results["blocks"].append(block(te_p[is_real | (kind == "inflated")], te_y[is_real | (kind == "inflated")], "inflation_only"))
    # Content-matched: each regenerated fake has its real parent in the same split, so scene
    # content cannot separate the classes here. A big gap between this row and sdxl_only
    # means the model is leaning on content rather than generation traces.
    results["blocks"].append(block(te_p[is_real | (kind == "regenerated")], te_y[is_real | (kind == "regenerated")], "regenerated_only"))
    results["blocks"].append(block(te_p[is_real | (kind == "sdxl")], te_y[is_real | (kind == "sdxl")], "sdxl_only"))
    results["blocks"].append(block(te_p[is_real & dmg], te_y[is_real & dmg], "real_damage_only"))
    if (src == "phone").any():
        results["blocks"].append(block(te_p[src == "phone"], te_y[src == "phone"], "phone_photos"))
    for s in sorted(set(src[is_real])):
        results["blocks"].append(block(te_p[is_real & (src == s)], te_y[is_real & (src == s)], f"real_subgroup_{s}"))

    # Diagnostic for partial edits: can the model see an edit when a crop actually contains it?
    # One 224 crop centred on the inpainting mask (inflated fakes) vs one centre crop (reals).
    # High here but low in inflation_only means the 3-crop recipe misses the patch (coverage),
    # not that the model is blind to edits; the 4x4 heatmap scan is then the fix.
    from PIL import Image
    from torchvision import transforms as TV

    from train_modal import MEAN, STD, fit_short_side

    norm = TV.Compose([TV.ToTensor(), TV.Normalize(MEAN, STD)])

    def one_crop_logit(path, centre_uv=None):
        im = fit_short_side(Image.open(root / path).convert("RGB"), 640, NO_UP)
        w, h = im.size
        u, v = centre_uv if centre_uv else (0.5, 0.5)
        x = min(max(0, int(u * w - CROP / 2)), w - CROP)
        y = min(max(0, int(v * h - CROP / 2)), h - CROP)
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            return float(model(norm(im.crop((x, y, x + CROP, y + CROP)))[None].cuda()).float().item())

    infl = [r for r in te if r["kind"] == "inflated"]
    patch_l = []
    for r in infl:
        mp = root / "data/fake/inflate" / (r["id"].removeprefix("inflate_") + "_mask.png")
        if not mp.exists():
            continue
        m = np.asarray(Image.open(mp).convert("L")) > 127
        ys, xs = np.nonzero(m)
        if len(xs):
            patch_l.append(one_crop_logit(r["out"], (xs.mean() / m.shape[1], ys.mean() / m.shape[0])))
    real_l = [one_crop_logit(r["out"]) for r in te if r["label"] == "real"]
    if patch_l and real_l:
        yy = np.r_[np.zeros(len(real_l)), np.ones(len(patch_l))]
        ss = np.r_[real_l, patch_l]
        fpr_, tpr_, _ = roc_curve(yy, ss)
        results["blocks"].append({"name": "diag_inflation_patch_crop", "n_real": len(real_l), "n_fake": len(patch_l),
                                  "auroc": float(roc_auc_score(yy, ss)), "tpr_at_fpr1": float(tpr_[fpr_ <= 0.01].max())})

    # compression suite on the test split (both classes)
    for v in VARIANTS:
        rows = [dict(r, out=f"data/dataset/test_variants/{v}/{r['label']}/{r['id']}.jpg") for r in te if (root / f"data/dataset/test_variants/{v}/{r['label']}/{r['id']}.jpg").exists()]
        if rows:
            p = score_rows(rows)
            y = [r["label"] == "fake" for r in rows]
            results["blocks"].append(block(p, y, f"compression_{v}"))

    np.save(d / f"test_probs_{agg}.npy", te_p)
    (d / f"eval_{agg}.json").write_text(json.dumps(results, indent=2))
    VOL.commit()
    return results


def to_markdown(res: dict, version: str) -> str:
    th = res["thresholds"]
    lines = [f"# Detector results: `{res['run']}`", "",
             f"Crop aggregation: {res.get('agg', 'mean')}. Temperature {res['temperature']:.3f}. Thresholds `{version}`: τ_red = {th['tau_red']:.4f} (val real FPR ≤ 1%), τ_amber = {th['tau_amber']:.4f} (≤ 5%). Fitted on {th['fitted_on']}, n = {th['n_val_real']} real val photos.", "",
             "| Test set | n real | n fake | AUROC | TPR @1% FPR | FPR @τ_red | FPR @τ_amber | TPR @τ_red | ECE |",
             "|---|---|---|---|---|---|---|---|---|"]
    f = lambda d, k, fmt="{:.3f}": fmt.format(d[k]) if k in d else "—"  # noqa: E731
    for b in res["blocks"]:
        lines.append(f"| {b['name']} | {b['n_real']} | {b['n_fake']} | {f(b,'auroc')} | {f(b,'tpr_at_fpr1')} | {f(b,'fpr_at_red')} | {f(b,'fpr_at_amber')} | {f(b,'tpr_at_red')} | {f(b,'ece')} |")
    lines += ["", "Rows: `in_distribution` = real vs regenerated + inflated + SDXL; `flux_holdout` = generator never trained on; "
              "`inflation_only` = partial edits on real photos; `regenerated_only` = content-matched pairs, so only generation traces can separate them; "
              "`sdxl_only` = fully fabricated scenes; `diag_inflation_patch_crop` = one crop centred on the edit vs one centre crop of real photos (diagnostic, no thresholds); `real_damage_only` = how often genuine damage photos are flagged; "
              "`real_subgroup_*` = FPR per real-photo source; `compression_*` = test split after JPEG q50 / half-size / screenshot re-encode.", ""]
    return "\n".join(lines)


@app.local_entrypoint()
def main(run: str = "det-r18-v1", thresholds_version: str = "t-0", agg: str = "mean"):
    res = evaluate.remote(run, agg)
    Path("eval").mkdir(exist_ok=True)
    Path("eval/results.json").write_text(json.dumps(res, indent=2))
    Path("eval/results.md").write_text(to_markdown(res, thresholds_version))
    Path("eval/runs").mkdir(parents=True, exist_ok=True)
    Path(f"eval/runs/{run}_{agg}_results.md").write_text(to_markdown(res, thresholds_version))
    Path(f"eval/runs/{run}_{agg}_eval.json").write_text(json.dumps(res, indent=2))
    th = {"version": thresholds_version, "model_version": run, "agg": agg, **res["thresholds"], "temperature": res["temperature"]}
    Path(f"eval/thresholds_{thresholds_version}.json").write_text(json.dumps(th, indent=2))
    print(to_markdown(res, thresholds_version))
