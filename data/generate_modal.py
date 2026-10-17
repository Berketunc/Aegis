"""Generate the fake-photo sets for ClaimShield on Modal (plan.md §7.3).

Four sets, all written to the `claimshield-data` volume (mounted at /vol; paths below are volume paths):

  regen    /data/fake/regen/<source_id>.png        SD2-inpainting, empty mask, no prompt
  inflate  /data/fake/inflate/<source_id>.png      SD2-inpainting, random blob mask, damage prompt
           /data/fake/inflate/<source_id>_mask.png
  sdxl     /data/fake/sdxl/sdxl_000001.png         SDXL base 1.0, fully fabricated
  flux     /data/fake/flux/flux_000001.png         FLUX.1-schnell, fully fabricated (held-out test set)

Every image is a PNG. Every set gets a manifest at /data/fake/<set>/manifest.jsonl with one
record per image: id, kind, generator, prompt, negative_prompt, seed, source_id, width, height,
damage, params, file.

Concurrency note: many containers write at once, so each image's record is written to its own
file under /data/fake/<set>/records/<id>.json (no shared file is ever appended to by two
containers), and `finalize_manifest` concatenates them into manifest.jsonl at the end of a run.

Usage (Modal CLI in ~/.local/bin):

  modal run data/generate_modal.py --job sdxl --count 4            # smoke test
  modal run --detach data/generate_modal.py --job sdxl --count 1500
  modal run --detach data/generate_modal.py --job flux --count 500
  modal run --detach data/generate_modal.py --job regen             # every master
  modal run --detach data/generate_modal.py --job inflate --count 1000
  modal run data/generate_modal.py --job all

Regen/inflate need the masters at /data/real/masters/<id>.png (written by prep_real.py).
"""

from __future__ import annotations

import json
import math
import os
import random
import time
import zlib
from pathlib import Path

import modal

# --------------------------------------------------------------------------------------------
# Modal resources
# --------------------------------------------------------------------------------------------

APP_NAME = "claimshield-gen"
DATA_MOUNT = "/vol"  # volume root; all paths below are /data/... on the volume
HF_MOUNT = "/hf-cache"

app = modal.App(APP_NAME)

data_vol = modal.Volume.from_name("claimshield-data", create_if_missing=True)
hf_vol = modal.Volume.from_name("claimshield-hf-cache", create_if_missing=True)
VOLUMES = {DATA_MOUNT: data_vol, HF_MOUNT: hf_vol}

# FLUX.1-schnell is gated, so the HF token is always attached. It must be resolved the same way
# locally and in the container, or Modal sees a different dependency list on each side.
SECRETS = [modal.Secret.from_name("huggingface")]

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch",
        "diffusers",
        "transformers",
        "accelerate",
        "safetensors",
        "sentencepiece",
        "protobuf",
        "pillow",
        "numpy",
        "scipy",
        "huggingface_hub",
    )
    .env({"HF_HUB_CACHE": HF_MOUNT, "HF_HOME": HF_MOUNT, "TOKENIZERS_PARALLELISM": "false"})
)

MODELS = {
    # stabilityai took this repo down; sd2-community hosts an identical copy (OpenRAIL++).
    "sd2inpaint": "sd2-community/stable-diffusion-2-inpainting",
    "sdxl": "stabilityai/stable-diffusion-xl-base-1.0",
    "flux": "black-forest-labs/FLUX.1-schnell",
}

# Fully fabricated sets: generation sizes, cycled in order.
SIZES = [(1152, 896), (896, 1152), (1024, 1024), (1216, 832)]
LONG_SIDE_OUT = 768
NEGATIVE_PROMPT = "illustration, cartoon, render, text, watermark"

# SD2-inpainting is run at the master's size (both sides rounded down to a multiple of 8).
# Masters larger than this on the long side are generated at this size and upsampled back,
# which keeps A10G memory and SD2's 512-trained UNet within sane bounds.
MAX_GEN_SIDE = 1024

BATCH_SIZE = 25          # images per .map() call
COMMIT_EVERY = 20        # vol.commit() cadence inside a batch
MASTERS_DIR = f"{DATA_MOUNT}/data/real/masters"
FAKE_ROOT = f"{DATA_MOUNT}/data/fake"

DAMAGE_PROMPTS = [
    "deep dent in car body panel, scratched paint, phone photo",
    "large scratch and scuffed paint on car door",
    "cracked bumper, broken plastic",
    "shattered headlamp, broken glass",
    "crumpled fender with chipped paint",
]

# --------------------------------------------------------------------------------------------
# Prompt templates (fully fabricated sets)
# --------------------------------------------------------------------------------------------

CAR_TYPES = ["hatchback", "saloon", "SUV", "estate", "van", "small city car", "pickup"]
COLOURS = ["white", "black", "silver", "grey", "red", "blue", "dark green"]
DAMAGES = [
    "dented door",
    "scratched rear bumper",
    "cracked front bumper",
    "smashed headlamp",
    "cracked windscreen",
    "crumpled bonnet",
    "scuffed wing mirror",
    "dented rear quarter panel",
    "scraped side panel",
]
UNDAMAGED_VIEWS = ["side", "rear", "front", "front three-quarter", "rear three-quarter"]
SETTINGS = [
    "supermarket car park",
    "driveway",
    "residential street at night",
    "wet street in the rain",
    "underground car park",
    "roadside",
]
LIGHTING = ["overcast daylight", "harsh sunlight", "dusk", "street lights", "flash"]
PHONE_CUES = [
    "close-up photo taken on a smartphone",
    "casual snapshot, slightly tilted",
    "insurance claim photo",
    "photo taken with a phone camera",
    "amateur photo, handheld",
    "quick snapshot for an insurance claim",
]
DAMAGE_FRAMING = [
    "close-up of the damage",
    "the damage fills most of the frame",
    "medium shot showing the damaged area",
    "whole car visible with the damage in view",
]


def build_prompt(rng: random.Random) -> tuple[str, bool]:
    """Return (prompt, damage) composed from the template lists. 80% damaged, 20% undamaged."""
    car = f"{rng.choice(COLOURS)} {rng.choice(CAR_TYPES)}"
    setting = rng.choice(SETTINGS)
    light = rng.choice(LIGHTING)
    cue = rng.choice(PHONE_CUES)
    damage = rng.random() < 0.8
    if damage:
        dmg = rng.choice(DAMAGES)
        framing = rng.choice(DAMAGE_FRAMING)
        prompt = f"{dmg} on a {car}, {framing}, parked in a {setting}, {light}, {cue}"
    else:
        view = rng.choice(UNDAMAGED_VIEWS)
        prompt = f"undamaged {car}, no damage, {view} view of the car, parked in a {setting}, {light}, {cue}"
    return prompt, damage


def item_seed(*parts) -> int:
    """Deterministic 32-bit seed from string parts, so reruns reproduce the same image."""
    return zlib.crc32(":".join(str(p) for p in parts).encode("utf-8")) & 0xFFFFFFFF


def fabricated_items(kind: str, count: int) -> list[dict]:
    """Item specs for sdxl/flux: index, prompt, size, seed. Seeds depend on kind and index only."""
    items = []
    for idx in range(1, count + 1):
        seed = item_seed(kind, f"{idx:06d}")
        prompt, damage = build_prompt(random.Random(seed))
        w, h = SIZES[(idx - 1) % len(SIZES)]
        items.append(
            {"idx": idx, "prompt": prompt, "damage": damage, "width": w, "height": h, "seed": seed}
        )
    return items


def chunk(seq: list, n: int) -> list[list]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]


# --------------------------------------------------------------------------------------------
# Helpers that run inside containers
# --------------------------------------------------------------------------------------------


def _round8(x: int) -> int:
    return max(64, (x // 8) * 8)


def gen_size_for(w: int, h: int) -> tuple[int, int]:
    """Size to run SD2-inpainting at: master size rounded to /8, capped at MAX_GEN_SIDE."""
    scale = min(1.0, MAX_GEN_SIDE / max(w, h))
    return _round8(int(w * scale)), _round8(int(h * scale))


def resize_long_side(img, long_side: int):
    from PIL import Image

    w, h = img.size
    if w >= h:
        nw, nh = long_side, max(1, round(h * long_side / w))
    else:
        nw, nh = max(1, round(w * long_side / h)), long_side
    return img.resize((nw, nh), Image.LANCZOS)


def write_record(set_dir: str, rec: dict) -> None:
    rec_dir = Path(set_dir) / "records"
    rec_dir.mkdir(parents=True, exist_ok=True)
    tmp = rec_dir / f"{rec['id']}.json.tmp"
    tmp.write_text(json.dumps(rec, ensure_ascii=False))
    os.replace(tmp, rec_dir / f"{rec['id']}.json")


def already_done(set_dir: str, image_id: str, out_path: Path) -> bool:
    return out_path.exists() and (Path(set_dir) / "records" / f"{image_id}.json").exists()


def save_png(img, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.png")
    img.save(tmp, format="PNG", compress_level=4)
    os.replace(tmp, path)


def blob_mask(w: int, h: int, rng: random.Random):
    """Random blob covering 3-12% of the image, built from 2-4 overlapping ellipses whose centres
    lie in the central 60% of the frame, Gaussian-blurred and thresholded. Returns uint8 HxW
    array with 255 inside the blob."""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    area = w * h
    target = rng.uniform(0.03, 0.12)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    scale = 1.0
    best = None
    for _ in range(30):
        n = rng.randint(2, 4)
        base_r = math.sqrt(target * area / (n * math.pi)) * 1.15 * scale
        # Cluster centre inside the central ~50%; ellipses jitter around it, so the blob stays
        # inside the central 60% of the frame.
        cx0 = rng.uniform(0.25 * w, 0.75 * w)
        cy0 = rng.uniform(0.25 * h, 0.75 * h)
        field = np.zeros((h, w), np.float32)
        for _ in range(n):
            cx = min(max(cx0 + rng.uniform(-0.8, 0.8) * base_r, 0.2 * w), 0.8 * w)
            cy = min(max(cy0 + rng.uniform(-0.8, 0.8) * base_r, 0.2 * h), 0.8 * h)
            rx = base_r * rng.uniform(0.6, 1.5)
            ry = base_r * rng.uniform(0.6, 1.5)
            th = rng.uniform(0, math.pi)
            dx, dy = xx - cx, yy - cy
            u = dx * math.cos(th) + dy * math.sin(th)
            v = -dx * math.sin(th) + dy * math.cos(th)
            field = np.maximum(field, ((u / rx) ** 2 + (v / ry) ** 2 <= 1.0).astype(np.float32))
        sigma = max(2.0, 0.12 * base_r)
        mask = gaussian_filter(field, sigma) > 0.5
        cov = float(mask.mean())
        if best is None or abs(cov - target) < abs(best[1] - target):
            best = (mask, cov)
        if 0.03 <= cov <= 0.12:
            return (mask.astype(np.uint8) * 255), cov
        scale *= math.sqrt(target / max(cov, 1e-6))
    mask, cov = best
    return (mask.astype(np.uint8) * 255), cov


def feather_paste(original, generated, mask_u8, feather_px: int = 6):
    """Composite `generated` onto `original` only where mask is set, with a feathered edge of
    about `feather_px` pixels. All at the original's size."""
    import numpy as np
    from PIL import Image, ImageFilter

    if generated.size != original.size:
        generated = generated.resize(original.size, Image.LANCZOS)
    alpha_img = Image.fromarray(mask_u8).filter(ImageFilter.GaussianBlur(feather_px / 2))
    alpha = np.asarray(alpha_img, dtype=np.float32)[..., None] / 255.0
    o = np.asarray(original.convert("RGB"), dtype=np.float32)
    g = np.asarray(generated.convert("RGB"), dtype=np.float32)
    out = o * (1.0 - alpha) + g * alpha
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8))


# --------------------------------------------------------------------------------------------
# Listing / download / manifest helpers (CPU)
# --------------------------------------------------------------------------------------------


@app.function(image=image, volumes=VOLUMES, timeout=600)
def list_masters(masters_dir: str = MASTERS_DIR) -> list[str]:
    """Return the sorted list of master image paths on the data volume."""
    data_vol.reload()
    p = Path(masters_dir)
    if not p.is_dir():
        return []
    exts = {".png", ".jpg", ".jpeg"}
    return sorted(str(f) for f in p.iterdir() if f.suffix.lower() in exts)


@app.function(image=image, volumes=VOLUMES, secrets=SECRETS, timeout=3600, cpu=4)
def download_model(model_id: str) -> str:
    """Download one model's weights into the HF cache volume once (so eight GPU containers don't
    race to write the same files), then commit."""
    from huggingface_hub import snapshot_download

    t0 = time.time()
    kwargs: dict = {"repo_id": model_id, "cache_dir": HF_MOUNT}
    if model_id == MODELS["sdxl"]:
        kwargs["allow_patterns"] = ["*.json", "*.txt", "*.model", "*fp16.safetensors"]
    elif model_id == MODELS["sd2inpaint"]:
        kwargs["allow_patterns"] = ["*.json", "*.txt", "*.model", "*fp16.safetensors"]
    elif model_id == MODELS["flux"]:
        kwargs["ignore_patterns"] = ["flux1-schnell.safetensors", "ae.safetensors", "*.md", "*.png"]
    path = snapshot_download(**kwargs)
    hf_vol.commit()
    return f"{model_id} -> {path} ({time.time() - t0:.0f}s)"


@app.function(image=image, volumes=VOLUMES, timeout=1800)
def finalize_manifest(kind: str) -> dict:
    """Concatenate /data/fake/<kind>/records/*.json into /data/fake/<kind>/manifest.jsonl."""
    data_vol.reload()
    set_dir = Path(FAKE_ROOT) / kind
    rec_dir = set_dir / "records"
    recs = []
    if rec_dir.is_dir():
        for f in sorted(rec_dir.glob("*.json")):
            try:
                recs.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                pass
    recs.sort(key=lambda r: r["id"])
    set_dir.mkdir(parents=True, exist_ok=True)
    tmp = set_dir / "manifest.jsonl.tmp"
    with tmp.open("w") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, set_dir / "manifest.jsonl")
    data_vol.commit()
    n_damage = sum(1 for r in recs if r.get("damage"))
    return {"kind": kind, "images": len(recs), "damage": n_damage}


# --------------------------------------------------------------------------------------------
# SD2-inpainting: regen + inflate (A10G)
# --------------------------------------------------------------------------------------------


@app.cls(
    image=image,
    gpu="A10G",
    volumes=VOLUMES,
    secrets=SECRETS,
    timeout=3600,
    max_containers=8,
)
class Inpainter:
    @modal.enter()
    def load(self):
        import torch
        from diffusers import StableDiffusionInpaintPipeline

        t0 = time.time()
        self.pipe = StableDiffusionInpaintPipeline.from_pretrained(
            MODELS["sd2inpaint"],
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True,
            safety_checker=None,
            requires_safety_checker=False,
        ).to("cuda")
        self.pipe.set_progress_bar_config(disable=True)
        self.pipe.vae.enable_slicing()
        self.torch = torch
        print(f"[inpainter] loaded in {time.time() - t0:.0f}s")

    def _run(self, image, mask, prompt: str, guidance: float, steps: int, seed: int, gw: int, gh: int):
        from PIL import Image

        g = self.torch.Generator(device="cuda").manual_seed(seed)
        img_in = image.resize((gw, gh), Image.LANCZOS) if image.size != (gw, gh) else image
        mask_in = mask.resize((gw, gh), Image.NEAREST) if mask.size != (gw, gh) else mask
        out = self.pipe(
            prompt=prompt,
            image=img_in,
            mask_image=mask_in,
            height=gh,
            width=gw,
            num_inference_steps=steps,
            guidance_scale=guidance,
            generator=g,
        ).images[0]
        return out

    @modal.method()
    def regen_batch(self, master_paths: list[str], out_dir: str = f"{FAKE_ROOT}/regen") -> dict:
        """Regenerate each master: empty (all-zero) mask + empty prompt. With the 9-channel SD2
        inpainting UNet the whole latent is generated from noise and only conditioned on the
        masked-image latents, so every pixel is new but the content is the master's (B-Free's
        "self-conditioned" fakes). A fully white mask would instead throw the content away."""
        from PIL import Image

        data_vol.reload()
        done = skipped = failed = 0
        t0 = time.time()
        for i, mp in enumerate(master_paths):
            source_id = Path(mp).stem
            image_id = f"regen_{source_id}"
            out_path = Path(out_dir) / f"{source_id}.png"
            if already_done(out_dir, image_id, out_path):
                skipped += 1
                continue
            try:
                master = Image.open(mp).convert("RGB")
                W, H = master.size
                out_w, out_h = _round8(W), _round8(H)
                gw, gh = gen_size_for(W, H)
                seed = item_seed("regen", source_id)
                mask = Image.new("L", (gw, gh), 0)
                out = self._run(master, mask, "", 1.0, 30, seed, gw, gh)
                if out.size != (out_w, out_h):
                    out = out.resize((out_w, out_h), Image.LANCZOS)
                save_png(out, out_path)
                write_record(
                    out_dir,
                    {
                        "id": image_id,
                        "kind": "regen",
                        "generator": MODELS["sd2inpaint"],
                        "prompt": None,
                        "negative_prompt": None,
                        "seed": seed,
                        "source_id": source_id,
                        "width": out.size[0],
                        "height": out.size[1],
                        "damage": None,
                        "params": {
                            "steps": 30,
                            "guidance": 1.0,
                            "mask": "empty",
                            "gen_width": gw,
                            "gen_height": gh,
                            "master_width": W,
                            "master_height": H,
                            "dtype": "fp16",
                        },
                        "file": str(out_path.relative_to(DATA_MOUNT)),
                    },
                )
                done += 1
            except Exception as e:  # keep the batch going; log and count
                failed += 1
                print(f"[regen] FAILED {mp}: {type(e).__name__}: {e}")
            if (done + failed) % COMMIT_EVERY == 0:
                data_vol.commit()
        data_vol.commit()
        return {"done": done, "skipped": skipped, "failed": failed, "seconds": time.time() - t0}

    @modal.method()
    def inflate_batch(self, master_paths: list[str], out_dir: str = f"{FAKE_ROOT}/inflate") -> dict:
        """Add damage inside a random blob mask and paste back only the edited patch."""
        from PIL import Image

        data_vol.reload()
        done = skipped = failed = 0
        t0 = time.time()
        for mp in master_paths:
            source_id = Path(mp).stem
            image_id = f"inflate_{source_id}"
            out_path = Path(out_dir) / f"{source_id}.png"
            mask_path = Path(out_dir) / f"{source_id}_mask.png"
            if already_done(out_dir, image_id, out_path) and mask_path.exists():
                skipped += 1
                continue
            try:
                master = Image.open(mp).convert("RGB")
                W, H = master.size
                seed = item_seed("inflate", source_id)
                rng = random.Random(seed)
                mask_u8, coverage = blob_mask(W, H, rng)
                prompt = rng.choice(DAMAGE_PROMPTS)
                gw, gh = gen_size_for(W, H)
                mask_img = Image.fromarray(mask_u8)
                gen = self._run(master, mask_img, prompt, 7.5, 30, seed, gw, gh)
                out = feather_paste(master, gen, mask_u8, feather_px=6)
                save_png(mask_img, mask_path)
                save_png(out, out_path)
                write_record(
                    out_dir,
                    {
                        "id": image_id,
                        "kind": "inflate",
                        "generator": MODELS["sd2inpaint"],
                        "prompt": prompt,
                        "negative_prompt": None,
                        "seed": seed,
                        "source_id": source_id,
                        "width": W,
                        "height": H,
                        "damage": True,
                        "params": {
                            "steps": 30,
                            "guidance": 7.5,
                            "mask_coverage": round(coverage, 4),
                            "mask_file": str(mask_path.relative_to(DATA_MOUNT)),
                            "feather_px": 6,
                            "gen_width": gw,
                            "gen_height": gh,
                            "dtype": "fp16",
                        },
                        "file": str(out_path.relative_to(DATA_MOUNT)),
                    },
                )
                done += 1
            except Exception as e:
                failed += 1
                print(f"[inflate] FAILED {mp}: {type(e).__name__}: {e}")
            if (done + failed) % COMMIT_EVERY == 0:
                data_vol.commit()
        data_vol.commit()
        return {"done": done, "skipped": skipped, "failed": failed, "seconds": time.time() - t0}


# --------------------------------------------------------------------------------------------
# Fully fabricated: SDXL and FLUX (L40S)
# --------------------------------------------------------------------------------------------


def _fabricate_loop(kind: str, generator_id: str, items: list[dict], run_one, params_fn) -> dict:
    """Shared loop for sdxl/flux: idempotent, resizes to long side 768, writes records."""
    out_dir = Path(FAKE_ROOT) / kind
    data_vol.reload()
    done = skipped = failed = 0
    t0 = time.time()
    gen_seconds = 0.0
    for it in items:
        image_id = f"{kind}_{it['idx']:06d}"
        out_path = out_dir / f"{image_id}.png"
        if already_done(str(out_dir), image_id, out_path):
            skipped += 1
            continue
        try:
            t1 = time.time()
            img = run_one(it)
            gen_seconds += time.time() - t1
            img = resize_long_side(img, LONG_SIDE_OUT)
            save_png(img, out_path)
            write_record(
                str(out_dir),
                {
                    "id": image_id,
                    "kind": kind,
                    "generator": generator_id,
                    "prompt": it["prompt"],
                    "negative_prompt": NEGATIVE_PROMPT if kind == "sdxl" else None,
                    "seed": it["seed"],
                    "source_id": None,
                    "width": img.size[0],
                    "height": img.size[1],
                    "damage": it["damage"],
                    "params": {
                        **params_fn(),
                        "gen_width": it["width"],
                        "gen_height": it["height"],
                        "resize": f"long side {LONG_SIDE_OUT}, LANCZOS",
                    },
                    "file": str(out_path.relative_to(DATA_MOUNT)),
                },
            )
            done += 1
        except Exception as e:
            failed += 1
            print(f"[{kind}] FAILED {image_id}: {type(e).__name__}: {e}")
        if (done + failed) % COMMIT_EVERY == 0:
            data_vol.commit()
    data_vol.commit()
    return {
        "done": done,
        "skipped": skipped,
        "failed": failed,
        "seconds": time.time() - t0,
        "gen_seconds": gen_seconds,
    }


@app.cls(
    image=image,
    gpu="L40S",
    volumes=VOLUMES,
    secrets=SECRETS,
    timeout=3600,
    max_containers=8,
)
class SDXL:
    @modal.enter()
    def load(self):
        import torch
        from diffusers import StableDiffusionXLPipeline

        t0 = time.time()
        self.pipe = StableDiffusionXLPipeline.from_pretrained(
            MODELS["sdxl"], torch_dtype=torch.float16, variant="fp16", use_safetensors=True
        ).to("cuda")
        self.pipe.set_progress_bar_config(disable=True)
        self.torch = torch
        print(f"[sdxl] loaded in {time.time() - t0:.0f}s")

    @modal.method()
    def generate(self, items: list[dict]) -> dict:
        def run_one(it):
            g = self.torch.Generator(device="cuda").manual_seed(it["seed"])
            return self.pipe(
                prompt=it["prompt"],
                negative_prompt=NEGATIVE_PROMPT,
                width=it["width"],
                height=it["height"],
                num_inference_steps=30,
                guidance_scale=6.5,
                generator=g,
            ).images[0]

        return _fabricate_loop(
            "sdxl",
            MODELS["sdxl"],
            items,
            run_one,
            lambda: {"steps": 30, "guidance": 6.5, "dtype": "fp16"},
        )


@app.cls(
    image=image,
    gpu="L40S",
    volumes=VOLUMES,
    secrets=SECRETS,
    timeout=3600,
    max_containers=8,
)
class Flux:
    @modal.enter()
    def load(self):
        import torch
        from diffusers import FluxPipeline

        t0 = time.time()
        self.pipe = FluxPipeline.from_pretrained(MODELS["flux"], torch_dtype=torch.bfloat16)
        self.offload = os.environ.get("FLUX_CPU_OFFLOAD", "0") == "1"
        if self.offload:
            self.pipe.enable_model_cpu_offload()
        else:
            self.pipe.to("cuda")
        self.pipe.set_progress_bar_config(disable=True)
        self.torch = torch
        print(f"[flux] loaded in {time.time() - t0:.0f}s (cpu_offload={self.offload})")

    def _gen(self, it):
        g = self.torch.Generator(device="cuda").manual_seed(it["seed"])
        return self.pipe(
            prompt=it["prompt"],
            width=it["width"],
            height=it["height"],
            num_inference_steps=4,
            guidance_scale=0.0,
            max_sequence_length=256,
            generator=g,
        ).images[0]

    @modal.method()
    def generate(self, items: list[dict]) -> dict:
        def run_one(it):
            try:
                return self._gen(it)
            except self.torch.cuda.OutOfMemoryError:
                # Fall back to CPU offload once, then retry (L40S has 48 GB; schnell in bf16
                # needs ~34 GB plus activations, so this should be rare).
                if not self.offload:
                    print("[flux] OOM: enabling model CPU offload and retrying")
                    self.torch.cuda.empty_cache()
                    self.pipe.enable_model_cpu_offload()
                    self.offload = True
                    return self._gen(it)
                raise

        return _fabricate_loop(
            "flux",
            MODELS["flux"],
            items,
            run_one,
            lambda: {
                "steps": 4,
                "guidance": 0.0,
                "max_sequence_length": 256,
                "dtype": "bf16",
                "cpu_offload": self.offload,
            },
        )


# --------------------------------------------------------------------------------------------
# Local entrypoint
# --------------------------------------------------------------------------------------------

DEFAULT_COUNTS = {"sdxl": 1500, "flux": 500, "inflate": 1000, "regen": 0}


def _report(kind: str, results: list[dict], n_items: int) -> None:
    done = sum(r["done"] for r in results)
    skipped = sum(r["skipped"] for r in results)
    failed = sum(r["failed"] for r in results)
    secs = sum(r["seconds"] for r in results)
    gen = sum(r.get("gen_seconds", 0.0) for r in results)
    per = f"{secs / done:.1f}s/image wall in-container" if done else "n/a"
    if done and gen:
        per += f", {gen / done:.1f}s/image pure generation"
    print(
        f"[{kind}] items={n_items} batches={len(results)} done={done} skipped={skipped} "
        f"failed={failed} ({per})"
    )


def _run_fabricated(kind: str, count: int) -> None:
    items = fabricated_items(kind, count)
    batches = chunk(items, BATCH_SIZE)
    print(f"[{kind}] {len(items)} items in {len(batches)} batches of <= {BATCH_SIZE}")
    print(f"[{kind}] " + download_model.remote(MODELS[kind]))
    cls = SDXL if kind == "sdxl" else Flux
    t0 = time.time()
    results = list(cls().generate.map(batches, order_outputs=False))
    print(f"[{kind}] map finished in {time.time() - t0:.0f}s")
    _report(kind, results, len(items))
    print(f"[{kind}] manifest: {finalize_manifest.remote(kind)}")


def _run_inpaint(kind: str, count: int, limit_masters: int, masters_dir: str, out_dir: str | None, prefix: str = "") -> None:
    masters = list_masters.remote(masters_dir)
    if prefix:  # e.g. cardd_ or commons_, so each source gets its own fixed sample
        masters = [m for m in masters if Path(m).name.startswith(prefix)]
    if not masters:
        print(f"[{kind}] no masters found at {masters_dir}; run prep_real.py first")
        return
    if limit_masters:
        masters = masters[:limit_masters]
    if kind == "inflate":
        n = min(count or DEFAULT_COUNTS["inflate"], len(masters))
        masters = sorted(random.Random(20251017).sample(masters, n))
    print(f"[{kind}] {len(masters)} masters from {masters_dir}")
    print(f"[{kind}] " + download_model.remote(MODELS["sd2inpaint"]))
    batches = chunk(masters, BATCH_SIZE)
    method = Inpainter().regen_batch if kind == "regen" else Inpainter().inflate_batch
    kwargs = {"out_dir": out_dir} if out_dir else {}
    t0 = time.time()
    results = list(method.map(batches, kwargs=kwargs, order_outputs=False))
    print(f"[{kind}] map finished in {time.time() - t0:.0f}s")
    _report(kind, results, len(masters))
    if not out_dir:
        print(f"[{kind}] manifest: {finalize_manifest.remote(kind)}")


@app.local_entrypoint()
def main(
    job: str = "sdxl",
    count: int = 0,
    limit_masters: int = 0,
    masters_dir: str = MASTERS_DIR,
    out_dir: str = "",
    prefix: str = "",
):
    """job in {sdxl, flux, regen, inflate, all}. count=0 means the plan's default count.
    masters_dir/out_dir are for regen/inflate smoke tests against other folders on the volume."""
    jobs = ["sdxl", "flux", "regen", "inflate"] if job == "all" else [job]
    for j in jobs:
        if j in ("sdxl", "flux"):
            _run_fabricated(j, count or DEFAULT_COUNTS[j])
        elif j in ("regen", "inflate"):
            _run_inpaint(j, count, limit_masters, masters_dir, out_dir or None, prefix)
        else:
            raise SystemExit(f"unknown job {j!r}; use sdxl, flux, regen, inflate or all")
