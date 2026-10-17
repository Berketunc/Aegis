"""Train the ResNet-18 detector on Modal (plan.md §7.5).

Reads /data/dataset/manifest.csv on volume `claimshield-data` (from build_dataset.py).
Training: resize short side to 640, random 224x224 crop, horizontal flip; v2 adds mild
blur, JPEG re-encode and random resize. AdamW, lr 1e-4 backbone / 1e-3 head, batch 64,
balanced sampler, AMP, cosine schedule. Validation uses the production 3-crop recipe
(centre + two grid crops at 224 after short-side-640, logits averaged).

Outputs under /data/models/<run>/: best.pt, last.pt, metrics.json (per epoch), temperature.json.
Local copy of metrics.json lands in eval/runs/<run>_metrics.json.

Usage:
    modal run train/train_modal.py --run det-r18-v1 --epochs 12
    modal run train/train_modal.py --run det-r18-v3 --epochs 15 --aug     # degradation aug + mask-guided crops
    modal run train/train_modal.py --run smoke --epochs 1 --limit 256
"""

import csv
import io
import json
import math
import random
from pathlib import Path

import modal

VOL = modal.Volume.from_name("claimshield-data")
MNT = "/vol"
SHORT = 640
CROP = 224
MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.4.1", "torchvision==0.19.1", index_url="https://download.pytorch.org/whl/cu121")
    .pip_install("pillow", "numpy", "scikit-learn")
)
app = modal.App("claimshield-train", image=image)


# ------------------------------------------------------------------ data
def read_manifest(root: Path, split: str, limit: int = 0):
    rows = []
    with (root / "data/dataset/manifest.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            if r["split"] == split:
                rows.append(r)
    if limit:
        random.Random(0).shuffle(rows)
        rows = rows[:limit]
    return rows


def three_crops(im, no_upscale: bool = True):
    """Production recipe: fit the short side (see fit_short_side), centre crop + two grid crops at 224."""
    im = fit_short_side(im, SHORT, no_upscale)
    w, h = im.size
    cx, cy = (w - CROP) // 2, (h - CROP) // 2
    boxes = [
        (cx, cy),  # centre
        (w // 4 - CROP // 2, h // 4 - CROP // 2),  # upper-left grid cell
        (3 * w // 4 - CROP // 2, 3 * h // 4 - CROP // 2),  # lower-right grid cell
    ]
    out = []
    for x, y in boxes:
        x, y = min(max(0, x), w - CROP), min(max(0, y), h - CROP)
        out.append(im.crop((x, y, x + CROP, y + CROP)))
    return out


def degrade(im):
    """Real-world handling, applied to BOTH classes: downscale (messaging apps, thumbnails),
    blur, JPEG re-encode, in that order, each independently. v1 flagged 89% of half-size real
    photos as fake; training on degraded copies of both classes removes "smooth = fake"."""
    from PIL import Image, ImageFilter

    if random.random() < 0.5:
        f = random.uniform(0.35, 1.0)
        kernel = random.choice([Image.BILINEAR, Image.BICUBIC, Image.LANCZOS])
        im = im.resize((max(CROP, int(im.width * f)), max(CROP, int(im.height * f))), kernel)
    if random.random() < 0.2:
        im = im.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.3, 1.5)))
    if random.random() < 0.5:
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=random.randint(30, 95))
        buf.seek(0)
        im = Image.open(buf).convert("RGB")
    return im


def make_train_tf(aug: bool, no_upscale: bool = True):
    """Returns tf(im, focus) -> tensor. focus = (u, v) relative point to centre the crop on
    (used for inflated fakes, so the crop contains the edited patch), or None for a random crop."""
    from torchvision import transforms as T

    # Global colour differs between SDXL scenes and claim photos (shortcut test: colour AUROC
    # ~0.89 on all fakes, ~0.44 on content-matched pairs). Jitter both classes so global colour
    # carries no label; fine-grained generation traces survive it.
    jitter = T.RandomApply([T.ColorJitter(brightness=0.25, contrast=0.25, saturation=0.3, hue=0.03)], p=0.6)
    norm = T.Compose([T.ToTensor(), T.Normalize(MEAN, STD)])

    def tf(im, focus=None):
        if aug:
            im = degrade(im)
        im = jitter(im)
        im = fit_short_side(im, SHORT, no_upscale)
        w, h = im.size
        if focus is not None:
            cx = focus[0] * w + random.uniform(-0.25, 0.25) * CROP
            cy = focus[1] * h + random.uniform(-0.25, 0.25) * CROP
            x, y = int(cx - CROP / 2), int(cy - CROP / 2)
        else:
            x, y = random.randint(0, w - CROP), random.randint(0, h - CROP)
        x, y = min(max(0, x), w - CROP), min(max(0, y), h - CROP)
        im = im.crop((x, y, x + CROP, y + CROP))
        if random.random() < 0.5:
            im = im.transpose(0)  # horizontal flip
        return norm(im)

    return tf


def grid_crops(im, n: int = 4, no_upscale: bool = True):
    """n x n evenly spaced, overlapping 224 crops covering the whole photo (the plan's 4x4
    heatmap). Order is row-major, so logits reshape to an n x n map."""
    im = fit_short_side(im, SHORT, no_upscale)
    w, h = im.size
    xs = [round(i * (w - CROP) / (n - 1)) for i in range(n)]
    ys = [round(j * (h - CROP) / (n - 1)) for j in range(n)]
    return [im.crop((x, y, x + CROP, y + CROP)) for y in ys for x in xs]


def fit_short_side(im, s, no_upscale: bool = True):
    """Shrink so the short side is s. With no_upscale (v4+), smaller photos keep their native
    pixels (only lifted to the 224 crop size if tinier): upscaling a small real photo adds
    interpolation smoothness that reads as synthetic (v3 flagged 23% of half-size real photos).
    no_upscale=False reproduces v1-v3, which always resized the short side to s."""
    from PIL import Image

    w, h = im.size
    target = min(s, min(w, h)) if no_upscale else s
    target = max(target, CROP)
    if target == min(w, h):
        return im
    k = target / min(w, h)
    return im.resize((max(CROP, round(w * k)), max(CROP, round(h * k))), Image.BILINEAR)


def _short_side(im, s):  # v1-v3 behaviour, kept for reproducing old runs
    return fit_short_side(im, s, no_upscale=False)


def mask_points(rows, root, n=64):
    """For inflated fakes: up to n relative (u, v) points inside the saved inpainting mask."""
    import numpy as np
    from PIL import Image

    pts = {}
    for i, r in enumerate(rows):
        if r.get("kind") != "inflated":
            continue
        mp = root / "data/fake/inflate" / (r["id"].removeprefix("inflate_") + "_mask.png")
        if not mp.exists():
            continue
        m = np.asarray(Image.open(mp).convert("L")) > 127
        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            continue
        k = np.random.default_rng(i).choice(len(xs), size=min(n, len(xs)), replace=False)
        pts[i] = [(float(xs[j]) / m.shape[1], float(ys[j]) / m.shape[0]) for j in k]
    return pts


class ImgDS:
    def __init__(self, rows, root, tf=None, crops=False, focus=None, p_focus=0.8, no_upscale=True):
        self.rows, self.root, self.tf, self.crops = rows, root, tf, crops
        self.focus, self.p_focus, self.no_upscale = focus or {}, p_focus, no_upscale

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        import torch
        from PIL import Image
        from torchvision import transforms as T

        r = self.rows[i]
        im = Image.open(self.root / r["out"]).convert("RGB")
        y = 1 if r["label"] == "fake" else 0
        if self.crops:
            norm = T.Compose([T.ToTensor(), T.Normalize(MEAN, STD)])
            cs = grid_crops(im, 4, self.no_upscale) if self.crops == "grid16" else three_crops(im, self.no_upscale)
            x = torch.stack([norm(c) for c in cs])  # [3 or 16, 3, 224, 224]
        else:
            pts = self.focus.get(i)
            f = random.choice(pts) if pts and random.random() < self.p_focus else None
            x = self.tf(im, f)
        return x, y, i


def no_upscale_of(run_dir: Path) -> bool:
    """Preprocessing mode a run was trained with. Runs before v4 have no record and upscaled."""
    p = run_dir / "preprocess.json"
    return json.loads(p.read_text()).get("no_upscale", False) if p.exists() else False


# ------------------------------------------------------------------ metrics
def metrics(scores, labels):
    import numpy as np
    from sklearn.metrics import roc_auc_score, roc_curve

    scores, labels = np.asarray(scores), np.asarray(labels)
    out = {"n": int(len(labels)), "n_fake": int(labels.sum()), "n_real": int((1 - labels).sum())}
    if labels.min() == labels.max():
        return out
    out["auroc"] = float(roc_auc_score(labels, scores))
    fpr, tpr, thr = roc_curve(labels, scores)
    for cap in (0.01, 0.05):
        ok = fpr <= cap
        out[f"tpr_at_fpr{int(cap * 100)}"] = float(tpr[ok].max()) if ok.any() else 0.0
        out[f"thr_at_fpr{int(cap * 100)}"] = float(thr[ok][tpr[ok].argmax()]) if ok.any() else 1.0
    return out


def cache_locally(rows, vol_root: Path, local_root: Path, extra=()):
    """Copy the images a run needs from the volume to container disk once. Reading from the
    volume every epoch made data loading the bottleneck (~26 images/s)."""
    import shutil
    from concurrent.futures import ThreadPoolExecutor

    rels = {r["out"] for r in rows} | set(extra)

    def cp(rel):
        dst = local_root / rel
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(vol_root / rel, dst)

    with ThreadPoolExecutor(64) as ex:
        list(ex.map(cp, sorted(rels)))
    return local_root


@app.function(volumes={MNT: VOL}, gpu="L40S", timeout=4 * 3600, cpu=16, memory=49152)
def train(run: str, epochs: int = 12, aug: bool = False, limit: int = 0, batch: int = 64, workers: int = 14, no_upscale: bool = True):
    import numpy as np
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, WeightedRandomSampler
    from torchvision import models

    torch.manual_seed(0)
    random.seed(0)
    root = Path(MNT)
    out_dir = root / "data/models" / run
    out_dir.mkdir(parents=True, exist_ok=True)
    dev = "cuda"

    tr_rows = read_manifest(root, "train", limit)
    va_rows = read_manifest(root, "val", limit // 4 if limit else 0)
    print(f"train {len(tr_rows)} ({sum(r['label']=='fake' for r in tr_rows)} fake), val {len(va_rows)}")
    import time

    t0 = time.time()
    masks = [f"data/fake/inflate/{r['id'].removeprefix('inflate_')}_mask.png" for r in tr_rows if r["kind"] == "inflated"]
    masks = [m for m in masks if (root / m).exists()]
    local = cache_locally(tr_rows + va_rows, root, Path("/tmp/ds"), masks)
    focus = mask_points(tr_rows, local)
    print(f"cached {len(tr_rows) + len(va_rows)} images + {len(masks)} masks locally in {time.time() - t0:.0f}s; "
          f"{len(focus)} inflated fakes get mask-guided crops", flush=True)

    # balanced sampler: equal expected draws per class
    y = np.array([r["label"] == "fake" for r in tr_rows], int)
    w_class = 1.0 / np.bincount(y)
    sampler = WeightedRandomSampler(torch.as_tensor(w_class[y], dtype=torch.double), num_samples=len(tr_rows), replacement=True)
    (out_dir / "preprocess.json").write_text(json.dumps({"short_side": SHORT, "crop": CROP, "no_upscale": no_upscale}))
    tr_dl = DataLoader(ImgDS(tr_rows, local, make_train_tf(aug, no_upscale), focus=focus), batch_size=batch, sampler=sampler, num_workers=workers, pin_memory=True, drop_last=True, persistent_workers=True, worker_init_fn=lambda w: random.seed(torch.initial_seed() % 2**32))
    va_dl = DataLoader(ImgDS(va_rows, local, crops=True, no_upscale=no_upscale), batch_size=32, shuffle=False, num_workers=workers, pin_memory=True)

    model = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1)
    model.fc = nn.Linear(512, 1)
    model = model.to(dev).to(memory_format=torch.channels_last)
    head = list(model.fc.parameters())
    backbone = [p for n, p in model.named_parameters() if not n.startswith("fc.")]
    opt = torch.optim.AdamW([{"params": backbone, "lr": 1e-4}, {"params": head, "lr": 1e-3}], weight_decay=1e-4)
    steps = epochs * len(tr_dl)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: 0.5 * (1 + math.cos(math.pi * min(s, steps) / steps)))
    scaler = torch.amp.GradScaler("cuda")
    loss_fn = nn.BCEWithLogitsLoss()

    history, best = [], -1.0
    for ep in range(epochs):
        model.train()
        tot, n = 0.0, 0
        for x, yb, _ in tr_dl:
            x = x.to(dev, non_blocking=True).to(memory_format=torch.channels_last)
            yb = yb.float().to(dev, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.float16):
                logit = model(x).squeeze(1)
                loss = loss_fn(logit, yb)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            tot += loss.item() * len(yb)
            n += len(yb)

        # validation with the 3-crop recipe
        model.eval()
        scores, labels = [], []
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            for x, yb, _ in va_dl:
                b = x.shape[0]
                logit = model(x.view(-1, 3, CROP, CROP).to(dev)).view(b, 3).float().mean(1)
                scores += logit.cpu().tolist()
                labels += yb.tolist()
        m = metrics(scores, labels)
        m.update({"epoch": ep + 1, "train_loss": tot / max(1, n)})
        history.append(m)
        print(json.dumps(m), flush=True)
        torch.save(model.state_dict(), out_dir / "last.pt")
        key = m.get("tpr_at_fpr1", m.get("auroc", 0.0))
        if key > best:
            best = key
            torch.save(model.state_dict(), out_dir / "best.pt")
            np.save(out_dir / "val_logits.npy", np.array(scores))
            np.save(out_dir / "val_labels.npy", np.array(labels))
        (out_dir / "metrics.json").write_text(json.dumps({"run": run, "aug": aug, "no_upscale": no_upscale, "epochs": epochs, "history": history, "best_tpr_at_fpr1": best}, indent=2))
        VOL.commit()

    # temperature scaling on the best epoch's val logits (plan.md §7.7)
    logits = torch.tensor(np.load(out_dir / "val_logits.npy")).float()
    lbl = torch.tensor(np.load(out_dir / "val_labels.npy")).float()
    # Grid search over positive temperatures (LBFGS can step T through zero and flip every score).
    grid = torch.logspace(-1.5, 1.5, 301)
    nll = torch.stack([nn.functional.binary_cross_entropy_with_logits(logits / g, lbl) for g in grid])
    t = grid[int(nll.argmin())]
    (out_dir / "temperature.json").write_text(json.dumps({"temperature": float(t), "val_nll": float(nll.min()), "fitted_on": "val, 3-crop mean logit, Modal FP32, grid 10^-1.5..10^1.5"}))
    VOL.commit()
    return (out_dir / "metrics.json").read_text()


@app.local_entrypoint()
def main(run: str = "det-r18-v1", epochs: int = 12, aug: bool = False, limit: int = 0, no_upscale: bool = True):
    res = train.remote(run, epochs, aug, limit, no_upscale=no_upscale)
    Path("eval/runs").mkdir(parents=True, exist_ok=True)
    Path(f"eval/runs/{run}_metrics.json").write_text(res)
    hist = json.loads(res)["history"]
    print("\nepoch  loss    auroc   tpr@1%fpr")
    for h in hist:
        print(f"{h['epoch']:>5}  {h['train_loss']:.4f}  {h.get('auroc', float('nan')):.4f}  {h.get('tpr_at_fpr1', float('nan')):.4f}")
