"""Versioned preprocessing, identical to A's training recipe (train/train_modal.py).

fit_short_side: shrink so the short side is 640, never enlarge (photos with a short
side between 224 and 640 keep their native pixels; tinier ones are lifted to 224),
bilinear. three_crops: centre plus the upper-left and lower-right 4x4 grid cells.
grid_crops: 4x4 evenly spaced windows for the heatmap. Any change here must be
mirrored in training and bump PREPROCESS_VERSION, or stored crops become invalid.
"""
from dataclasses import dataclass
from io import BytesIO
import warnings

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

PREPROCESS_VERSION = "rgb-fit640-noupscale-bilinear-centre-gridcells-v2"
SHORT = 640
CROP = 224
PHASH_VERSION = "phash64-dct32-median64-v1"
Image.MAX_IMAGE_PIXELS = 40_000_000


class InvalidImage(ValueError):
    pass


@dataclass
class PreparedImage:
    crops: np.ndarray  # uint8 [3, 3, 224, 224], unnormalised RGB
    phash: str
    metadata: dict
    crop_boxes: list


def fit_short_side(image: Image.Image) -> Image.Image:
    """A's fit_short_side(im, 640, no_upscale=True): shrink only, bilinear."""
    width, height = image.size
    target = max(min(SHORT, min(width, height)), CROP)
    if target == min(width, height):
        return image
    k = target / min(width, height)
    return image.resize((max(CROP, round(width * k)), max(CROP, round(height * k))), Image.Resampling.BILINEAR)


def three_crop_boxes(width: int, height: int) -> list:
    """A's three_crops geometry: centre, upper-left grid cell, lower-right grid cell, clamped."""
    centres = [((width - CROP) // 2, (height - CROP) // 2),
               (width // 4 - CROP // 2, height // 4 - CROP // 2),
               (3 * width // 4 - CROP // 2, 3 * height // 4 - CROP // 2)]
    boxes = []
    for x, y in centres:
        x, y = min(max(0, x), width - CROP), min(max(0, y), height - CROP)
        boxes.append([x, y, x + CROP, y + CROP])
    return boxes


def grid_crop_boxes(width: int, height: int, n: int = 4) -> list:
    """A's grid_crops geometry: n x n evenly spaced windows, row-major."""
    xs = [round(i * (width - CROP) / (n - 1)) for i in range(n)]
    ys = [round(j * (height - CROP) / (n - 1)) for j in range(n)]
    return [[x, y, x + CROP, y + CROP] for y in ys for x in xs]


def _oriented_rgb(source: Image.Image) -> Image.Image:
    return fit_short_side(ImageOps.exif_transpose(source).convert("RGB"))


def _stack(image: Image.Image, boxes: list) -> np.ndarray:
    return np.stack([np.asarray(image.crop(tuple(box)), dtype=np.uint8).transpose(2, 0, 1) for box in boxes])


def heatmap_crops(raw: bytes):
    """Cover the oriented, fitted image with a 4x4 grid of 224 px windows.

    Windows overlap where needed; no full image is downsampled to 224 pixels.
    Originals have already passed upload decoding limits before this is called.
    """
    with Image.open(BytesIO(raw)) as source:
        rgb = _oriented_rgb(source)
        width, height = rgb.size
        boxes = grid_crop_boxes(width, height)
        return _stack(rgb, boxes), boxes, [width, height]


def phash64(image: Image.Image) -> str:
    """64-bit DCT hash, compatible with this service's versioned index only."""
    pixels = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    k = np.arange(8, dtype=np.float64)[:, None]
    x = np.arange(32, dtype=np.float64)[None, :]
    basis = np.cos(np.pi * (2 * x + 1) * k / 64)
    low = basis @ pixels @ basis.T
    # A normalised DCT; the DC component remains one of the 64 median inputs.
    low[0, :] /= np.sqrt(2)
    low[:, 0] /= np.sqrt(2)
    bits = (low > np.median(low)).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def prepare_image(raw: bytes) -> PreparedImage:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise InvalidImage("Only JPEG, PNG and WebP images are supported")
                source.load()
                exif = source.getexif()
                software = exif.get(305)
                if not isinstance(software, str):
                    software = None
                # Keep only a short, printable software tag; all EXIF stays in the encrypted original.
                software = "".join(c for c in software if c.isprintable())[:160] if software else None
                from .metadata import extract_flags
                metadata = extract_flags(raw, bool(exif) or bool(source.info.get("exif")), software)
                rgb = ImageOps.exif_transpose(source).convert("RGB")
                width, height = rgb.size
                if max(width, height) / min(width, height) > 20:
                    raise InvalidImage("Image aspect ratio exceeds the supported limit")
                perceptual = phash64(rgb)
                native_short_side = min(width, height)
                rgb = fit_short_side(rgb)
                width, height = rgb.size
                boxes = three_crop_boxes(width, height)
                metadata["native_short_side"] = native_short_side
                return PreparedImage(_stack(rgb, boxes), perceptual, metadata, boxes)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        if isinstance(exc, InvalidImage):
            raise
        raise InvalidImage("Image could not be decoded safely") from exc
