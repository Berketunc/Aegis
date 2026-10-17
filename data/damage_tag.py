"""Measure visible damage with zero-shot CLIP instead of trusting prompts (plan.md §7.1).

SDXL/FLUX often ignore the damage part of a prompt, and some Commons crash-scene photos show
no damaged car. If the damage flag is wrong, "clean car" can end up only in the fake pile and
the detector learns "undamaged = fake". This tags each image with p_damage from CLIP
ViT-L/14 and writes /data/tags/damage_<set>.jsonl ({"id", "p_damage"}). build_dataset.py uses
these tags for sdxl, flux and commons. CarDD is tagged too, as an accuracy check: every CarDD
image is damaged, so its mean p_damage and recall show how far to trust the tag.

Usage:
    modal run data/damage_tag.py --set sdxl
    modal run data/damage_tag.py --set cardd      # accuracy check
"""

import json
from pathlib import Path

import modal

VOL = modal.Volume.from_name("claimshield-data")
MNT = "/vol"
MODEL = "openai/clip-vit-large-patch14"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.4.1", index_url="https://download.pytorch.org/whl/cu121")
    .pip_install("transformers==4.46.3", "pillow", "numpy")
    # Own throwaway cache: CLIP is 1.7 GB and this keeps the tagger off the generators' shared cache.
    .env({"HF_HOME": "/root/hf"})
)
app = modal.App("claimshield-damage-tag", image=image)

SETS = {
    "cardd": ("data/real/masters", "cardd_*.png"),
    "commons": ("data/real/masters", "commons_*.png"),
    "sdxl": ("data/fake/sdxl", "sdxl_*.png"),
    "flux": ("data/fake/flux", "flux_*.png"),
}
DAMAGED = [
    "a photo of a damaged car",
    "a photo of a car with a dent",
    "a photo of a car with scratched paint",
    "a photo of a crashed car",
    "a photo of a car with a broken headlight",
    "a photo of a car with a cracked windscreen",
    "a photo of a car with a crumpled bumper",
    "a photo of a car with a flat tyre",
]
# Scene check (Commons only uses it): does this look like a photo of a damaged road car, as a
# claimant would take? The "car crash" searches also return motorsport, emergency vehicles,
# rescue scenes with people, charts, historic photos and empty roads.
SCENE_OK = [
    "a photo of a damaged car",
    "a close-up photo of a dented car body panel",
    "a photo of a car after a road accident",
    "a photo of a crashed car parked on a street",
    "a photo of a wrecked passenger car",
]
SCENE_BAD = [
    "a photo of a race car on a race track",
    "a photo of a fire engine",
    "a photo of an ambulance and paramedics",
    "a photo of injured people being rescued",
    "a photo of a group of people",
    "a chart or diagram",
    "an old black and white photograph",
    "a photo of a highway with traffic",
    "a photo of a bus or lorry",
    "a photo of bumper cars at a fairground",
    "a photo of a road sign",
    "a photo of a toy car",
    "a photo of a crane or tow truck",
    "a photo of a landscape",
    "a photo of an aircraft",
    "a sepia-toned antique photograph",
    "a stereoscopic photo card with two images side by side",
    "a photo of police officers standing at a scene",
    "a photo of a formula one car",
    "a photo of a stock car covered in sponsor stickers",
]
# Hard rejects: people (ethics: never train on rescue scenes or bystanders), non-photos, and
# monochrome/antique prints (a "monochrome = real" shortcut). Other off-target scenes are still
# genuine photos, so their "real" label is right; regenerated twins neutralise their content.
SCENE_HARD_BAD = {
    "a photo of an ambulance and paramedics",
    "a photo of injured people being rescued",
    "a photo of a group of people",
    "a chart or diagram",
    "an old black and white photograph",
    "a sepia-toned antique photograph",
    "a stereoscopic photo card with two images side by side",
    "a photo of police officers standing at a scene",
}
INTACT = [
    "a photo of an undamaged car",
    "a photo of a clean car in good condition",
    "a photo of an intact car with no damage",
    "a photo of a parked car with no scratches or dents",
]


@app.function(volumes={MNT: VOL}, gpu="A10G", cpu=8, timeout=3600)
def tag(set_name: str) -> dict:
    import numpy as np
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    VOL.reload()
    d, pat = SETS[set_name]
    files = sorted(p for p in (Path(MNT) / d).glob(pat) if ".tmp" not in p.name)
    model = CLIPModel.from_pretrained(MODEL, torch_dtype=torch.float16).cuda().eval()
    proc = CLIPProcessor.from_pretrained(MODEL)
    print(f"loaded {MODEL}; tagging {len(files)} images", flush=True)
    with torch.no_grad():
        t = proc(text=DAMAGED + INTACT, return_tensors="pt", padding=True).to("cuda")
        tf = model.get_text_features(**t)
        tf = tf / tf.norm(dim=-1, keepdim=True)
        t2 = proc(text=SCENE_OK + SCENE_BAD, return_tensors="pt", padding=True).to("cuda")
        sf = model.get_text_features(**t2)
        sf = sf / sf.norm(dim=-1, keepdim=True)
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(16)
    out, probs = [], []
    for i in range(0, len(files), 64):
        batch = files[i : i + 64]
        ims = list(pool.map(lambda p: Image.open(p).convert("RGB"), batch))  # volume reads are I/O-bound
        with torch.no_grad():
            x = proc(images=ims, return_tensors="pt")["pixel_values"].to("cuda", torch.float16)
            f = model.get_image_features(pixel_values=x)
            f = f / f.norm(dim=-1, keepdim=True)
            p = (100.0 * f @ tf.T).float().softmax(-1)
            p_dmg = p[:, : len(DAMAGED)].sum(-1).cpu().numpy()
            ps = (100.0 * f @ sf.T).float().softmax(-1)
            p_ok = ps[:, : len(SCENE_OK)].sum(-1).cpu().numpy()
            top = ps.argmax(-1).cpu().numpy()
        labels = SCENE_OK + SCENE_BAD
        for path, pd, po, tp in zip(batch, p_dmg, p_ok, top):
            out.append({"id": path.stem, "p_damage": round(float(pd), 4), "p_scene_ok": round(float(po), 4),
                        "scene_top": labels[int(tp)], "scene_hard_reject": labels[int(tp)] in SCENE_HARD_BAD})
            probs.append(float(pd))
        if (i // 64) % 10 == 0:
            print(f"  {i + len(batch)}/{len(files)}", flush=True)
    dst = Path(MNT) / f"data/tags/damage_{set_name}.jsonl"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("".join(json.dumps(r) + "\n" for r in out))
    VOL.commit()
    probs = np.array(probs)
    return {
        "set": set_name,
        "n": len(probs),
        "mean_p_damage": float(probs.mean()) if len(probs) else None,
        "share_damaged_at_0.5": float((probs >= 0.5).mean()) if len(probs) else None,
        "share_scene_ok_at_0.5": float(np.mean([r["p_scene_ok"] >= 0.5 for r in out])) if out else None,
        "file": str(dst.relative_to(MNT)),
    }


@app.local_entrypoint()
def main(set: str = "sdxl"):
    res = tag.remote(set)
    print(json.dumps(res, indent=2))
    Path("data/tags").mkdir(parents=True, exist_ok=True)
    Path(f"data/tags/damage_{set}_summary.json").write_text(json.dumps(res, indent=2))
