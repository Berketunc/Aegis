"""Versioned preprocessing: RGB, short side 640, three 224 px crops."""
from dataclasses import dataclass
from io import BytesIO
import warnings

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

PREPROCESS_VERSION = "rgb-short640-bicubic-centre-grid25-75-v1"
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


def heatmap_crops(raw: bytes):
    """Cover the oriented, short640 image with a 4×4 grid of 224 px windows.

    Windows overlap where needed; no full image is downsampled to 224 pixels.
    Originals have already passed upload decoding limits before this is called.
    """
    with Image.open(BytesIO(raw)) as source:
        rgb = ImageOps.exif_transpose(source).convert("RGB")
        width, height = rgb.size
        scale = 640 / min(width, height)
        width, height = max(640, round(width * scale)), max(640, round(height * scale))
        rgb = rgb.resize((width, height), Image.Resampling.BICUBIC)
        boxes = []
        for row in range(4):
            for column in range(4):
                left, top = round((width - 224) * column / 3), round((height - 224) * row / 3)
                boxes.append([left, top, left + 224, top + 224])
        crops = np.stack([np.asarray(rgb.crop(box), dtype=np.uint8).transpose(2, 0, 1) for box in boxes])
        return crops, boxes, [width, height]


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
                scale = 640 / min(width, height)
                width, height = max(640, round(width * scale)), max(640, round(height * scale))
                rgb = rgb.resize((width, height), Image.Resampling.BICUBIC)
                boxes = []
                for fraction in (0.5, 0.25, 0.75):
                    left = round((width - 224) * fraction)
                    top = round((height - 224) * fraction)
                    boxes.append([left, top, left + 224, top + 224])
                crops = np.stack([np.asarray(rgb.crop(box), dtype=np.uint8).transpose(2, 0, 1) for box in boxes])
                return PreparedImage(crops, perceptual, metadata, boxes)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        if isinstance(exc, InvalidImage):
            raise
        raise InvalidImage("Image could not be decoded safely") from exc
