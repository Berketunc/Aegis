"""Candidate 2 (plan.md §7.5): frozen CLIP ViT-B/16 image encoder + logistic regression (UnivFD).

Embeds every image with the production 3-crop recipe (short side 640, centre + two grid crops
at 224, embeddings averaged), fits a logistic regression on train, picks C on val, fits a
temperature on val, then reports the same evaluation rows as eval_modal.py so the two
candidates compare directly.

Outputs on the volume under /data/models/<run>/: probe.json (weights, bias, temperature,
thresholds), eval.json. Locally: eval/runs/<run>_eval.json and eval/runs/<run>_results.md.

Usage:
    modal run train/clip_probe_modal.py --run clip-b16-probe-v1
"""

import json
from pathlib import Path

import modal

VOL = modal.Volume.from_name("claimshield-data")
MNT = "/vol"
MODEL = "openai/clip-vit-base-patch16"
VARIANTS = ("jpeg_q50", "resize_half", "screenshot")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.4.1", "torchvision==0.19.1", index_url="https://download.pytorch.org/whl/cu121")
    .pip_install("transformers==4.46.3", "pillow", "numpy", "scikit-learn")
    .env({"HF_HOME": "/root/hf"})
    .add_local_python_source("train_modal", "eval_modal")
)
app = modal.App("claimshield-clip-probe", image=image)


@app.function(volumes={MNT: VOL}, gpu="A10G", cpu=8, memory=32768, timeout=3 * 3600)
def probe(run: str) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    import numpy as np
    import torch
    from PIL import Image
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score, roc_curve
    from transformers import CLIPImageProcessor, CLIPVisionModelWithProjection

    from eval_modal import ece
    from train_modal import read_manifest, three_crops

    root = Path(MNT)
    model = CLIPVisionModelWithProjection.from_pretrained(MODEL, torch_dtype=torch.float16).cuda().eval()
    proc = CLIPImageProcessor.from_pretrained(MODEL)
    pool = ThreadPoolExecutor(16)

    def load(path):
        return three_crops(Image.open(root / path).convert("RGB"))

    def embed(paths):
        out = []
        for i in range(0, len(paths), 64):
            crops = list(pool.map(load, paths[i : i + 64]))
            flat = [c for cs in crops for c in cs]
            x = proc(images=flat, do_resize=False, do_center_crop=False, return_tensors="pt")["pixel_values"]
            with torch.no_grad():
                e = model(pixel_values=x.cuda().half()).image_embeds.float()
            e = e / e.norm(dim=-1, keepdim=True)
            out.append(e.view(-1, 3, e.shape[-1]).mean(1).cpu().numpy())
            if (i // 64) % 20 == 0:
                print(f"    {i + len(crops)}/{len(paths)}", flush=True)
        return np.concatenate(out)

    data = {}
    for split in ("train", "val", "test"):
        rows = read_manifest(root, split)
        print(f"embedding {split}: {len(rows)}", flush=True)
        data[split] = (rows, embed([r["out"] for r in rows]), np.array([r["label"] == "fake" for r in rows], int))

    # C on val by TPR at 1% FPR (the plan's selection metric)
    (_, Xtr, ytr), (_, Xva, yva) = data["train"], data["val"]
    best = None
    for C in (0.1, 0.3, 1.0, 3.0, 10.0, 30.0):
        clf = LogisticRegression(C=C, max_iter=5000, class_weight="balanced").fit(Xtr, ytr)
        s = clf.decision_function(Xva)
        fpr, tpr, _ = roc_curve(yva, s)
        score = float(tpr[fpr <= 0.01].max())
        print(f"  C={C}: val AUROC {roc_auc_score(yva, s):.4f}, TPR@1%FPR {score:.4f}", flush=True)
        if best is None or score > best[0]:
            best = (score, C, clf)
    _, C, clf = best
    s_va = clf.decision_function(Xva)
    grid = np.logspace(-1.5, 1.5, 301)

    def nll(t):
        p = 1 / (1 + np.exp(-s_va / t))
        return -np.mean(yva * np.log(p + 1e-12) + (1 - yva) * np.log(1 - p + 1e-12))

    T = float(grid[int(np.argmin([nll(t) for t in grid]))])
    prob = lambda X: 1 / (1 + np.exp(-clf.decision_function(X) / T))  # noqa: E731
    p_va = prob(Xva)
    tau_red = float(np.quantile(p_va[yva == 0], 0.99))
    tau_amber = float(np.quantile(p_va[yva == 0], 0.95))

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

    te_rows, Xte, te_y = data["test"]
    te_p = prob(Xte)
    kind = np.array([r["kind"] for r in te_rows])
    src = np.array([r["source"] for r in te_rows])
    dmg = np.array([str(r["damage"]).lower() in ("true", "1") for r in te_rows])
    real = te_y == 0
    sel = lambda m, name: block(te_p[m], te_y[m], name)  # noqa: E731
    blocks = [
        sel(real | np.isin(kind, ["regenerated", "inflated", "sdxl"]), "in_distribution"),
        sel(real | (kind == "flux"), "flux_holdout"),
        sel(real | (kind == "inflated"), "inflation_only"),
        sel(real | (kind == "regenerated"), "regenerated_only"),
        sel(real | (kind == "sdxl"), "sdxl_only"),
        sel(real & dmg, "real_damage_only"),
    ]
    for s_ in sorted(set(src[real])):
        blocks.append(sel(real & (src == s_), f"real_subgroup_{s_}"))
    for v in VARIANTS:
        rows = [r for r in te_rows if (root / f"data/dataset/test_variants/{v}/{r['label']}/{r['id']}.jpg").exists()]
        if rows:
            X = embed([f"data/dataset/test_variants/{v}/{r['label']}/{r['id']}.jpg" for r in rows])
            blocks.append(block(prob(X), [r["label"] == "fake" for r in rows], f"compression_{v}"))

    res = {"run": run, "model": MODEL, "C": C, "temperature": T,
           "thresholds": {"tau_red": tau_red, "tau_amber": tau_amber, "fitted_on": "val reals, Modal FP16 CLIP + FP64 LR", "n_val_real": int((yva == 0).sum())},
           "blocks": blocks}
    d = root / "data/models" / run
    d.mkdir(parents=True, exist_ok=True)
    (d / "probe.json").write_text(json.dumps({"model": MODEL, "coef": clf.coef_[0].tolist(), "intercept": float(clf.intercept_[0]), "temperature": T, "tau_red": tau_red, "tau_amber": tau_amber}))
    (d / "eval.json").write_text(json.dumps(res, indent=2))
    VOL.commit()
    return res


@app.local_entrypoint()
def main(run: str = "clip-b16-probe-v1"):
    from eval_modal import to_markdown

    res = probe.remote(run)
    Path("eval/runs").mkdir(parents=True, exist_ok=True)
    Path(f"eval/runs/{run}_eval.json").write_text(json.dumps(res, indent=2))
    md = to_markdown(res, "probe")
    Path(f"eval/runs/{run}_results.md").write_text(md)
    print(md)
