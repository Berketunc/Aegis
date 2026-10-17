"""Contact sheets for the spot-checks in plan.md §7.2 and §7.3.

Picks N random images from one set on the volume, tiles them into a numbered grid, and
downloads the sheet to logs/sheet_<set>.jpg. The tile numbers map to ids in
logs/sheet_<set>.txt, so rejects can be written to data/rejects_<set>.txt (one id per line);
build_dataset.py drops those.

Sets: real (masters), regen, inflate, sdxl, flux, commons (masters from Commons only).

Usage:
    modal run data/contact_sheet.py --set sdxl --n 50
"""

import random
import subprocess
from pathlib import Path

import modal

VOL = modal.Volume.from_name("claimshield-data")
MNT = "/vol"
image = modal.Image.debian_slim(python_version="3.11").pip_install("pillow")
app = modal.App("claimshield-contact-sheet", image=image)

DIRS = {
    "real": ("data/real/masters", "*.png"),
    "commons": ("data/real/masters", "commons_*.png"),
    "regen": ("data/fake/regen", "*.png"),
    "inflate": ("data/fake/inflate", "*.png"),
    "sdxl": ("data/fake/sdxl", "*.png"),
    "flux": ("data/fake/flux", "*.png"),
}


@app.function(volumes={MNT: VOL}, timeout=900, cpu=2)
def sheet(set_name: str, n: int, seed: int, tile: int = 256, cols: int = 10) -> tuple[str, list[str]]:
    from PIL import Image, ImageDraw

    VOL.reload()
    d, pat = DIRS[set_name]
    files = sorted(p for p in (Path(MNT) / d).glob(pat) if not p.stem.endswith("_mask") and ".tmp" not in p.name)
    random.Random(seed).shuffle(files)
    files = files[:n]
    rows = (len(files) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * tile, max(1, rows) * tile), "white")
    draw = ImageDraw.Draw(canvas)
    ids = []
    for i, p in enumerate(files):
        im = Image.open(p).convert("RGB")
        im.thumbnail((tile - 4, tile - 4))
        x, y = (i % cols) * tile, (i // cols) * tile
        canvas.paste(im, (x + 2, y + 2))
        draw.rectangle((x + 2, y + 2, x + 34, y + 20), fill="black")
        draw.text((x + 5, y + 5), str(i), fill="yellow")
        ids.append(p.stem)
    out = Path(MNT) / f"data/sheets/sheet_{set_name}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, "JPEG", quality=85)
    VOL.commit()
    return str(out.relative_to(MNT)), ids


@app.local_entrypoint()
def main(set: str = "sdxl", n: int = 50, seed: int = 0):
    path, ids = sheet.remote(set, n, seed)
    Path("logs").mkdir(exist_ok=True)
    local = Path("logs") / f"sheet_{set}.jpg"
    subprocess.run(["modal", "volume", "get", "--force", "claimshield-data", "/" + path, str(local)], check=True)
    (Path("logs") / f"sheet_{set}.txt").write_text("".join(f"{i}\t{x}\n" for i, x in enumerate(ids)))
    print(f"{len(ids)} images -> {local}  (index -> id in logs/sheet_{set}.txt)")
