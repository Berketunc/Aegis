# Dataset card

Built with seed 20261017. 10363 images. Images live on the Modal volume `claimshield-data`; only manifests are in git.

## Counts by split and label

| split | real | fake | total |
|---|---|---|---|
| train | 2982 | 4036 | 7018 |
| val | 638 | 869 | 1507 |
| test | 641 | 1197 | 1838 |

## Counts by kind and split

| kind | train | val | test |
|---|---|---|---|
| flux | 0 | 0 | 327 |
| inflated | 558 | 125 | 121 |
| real | 2982 | 638 | 641 |
| regenerated | 2982 | 638 | 641 |
| sdxl | 496 | 106 | 108 |

## Counts by source

| source | images |
|---|---|
| cardd | 8794 |
| commons | 532 |
| flux | 327 |
| sdxl | 710 |

## Damage share (must be within ~10 points, plan.md §7.1)

- Real: 94.7% damaged
- Fake: 94.2% damaged
- Gap: 0.5 points

## Shortcut tests

Logistic regression on features that carry no generation traces, trained on train and scored on val. Target ≈ 0.5 for each.

| Features | All fakes | Real vs regenerated only |
|---|---|---|
| File size and dimensions (plan.md §7.4) | 0.513 | 0.519 |
| Dimensions and aspect only | 0.502 | 0.511 |
| File size only | 0.515 | 0.521 |
| Global colour statistics | 0.554 | 0.513 |

Colour statistics on all fakes can legitimately sit above 0.5, because SDXL scenes differ in content from claim photos. The real-vs-regenerated column is content-matched, so it should stay near 0.5.

## Processing

- Real masters: EXIF orientation applied, long side 768 px, sides multiples of 8, PNG, EXIF dropped.
- Regenerated, SDXL and FLUX fakes first get a camera-JPEG pass (quality 85–98) at native size. Inflated fakes skip it, since their base is a real photo that already went through a camera JPEG.
- Then every image: short side resized to a random 512–768 px (LANCZOS), saved as JPEG quality 65–95, 4:2:0, no EXIF.
- Near-duplicate reals removed by 64-bit pHash, Hamming ≤ 6.
- Groups: a real photo with its regenerated and inflated children; each SDXL image alone. Split 70/15/15 by group, stratified by real source.
- FLUX.1-schnell images and team phone photos are test only.
- SDXL and FLUX images are cropped (not upscaled) to a size drawn from the real photos after their camera-JPEG pass. Square and landscape frames become landscape; portrait frames are kept only up to the real pile's portrait share. Size, aspect and resampling history then match the real pile.
- Damage is measured, not assumed: SDXL, FLUX and Commons images are tagged by zero-shot CLIP ViT-L/14 (data/damage_tag.py), thresholded at the 10th percentile of CarDD scores (~90% recall on known damage). Undamaged fully generated fakes are capped at the real pile's undamaged share, so the model cannot learn 'undamaged = fake'.
- Test-split compression suite: jpeg_q50, resize_half, screenshot under `data/dataset/test_variants/`.

## Rejects and filters

- fake_flux_portrait_over_real_share: 63
- fake_regenerated_orphan: 35
- fake_sdxl_portrait_over_real_share: 163
- fake_undamaged_over_real_share: 737
- info_damage_threshold_x1000: 797
- real_commons_off_target_scene: 29
- real_near_duplicate: 6
- real_rejected_monochrome: 34
- normalise errors: 0

## Licences

- CarDD: non-commercial research and education licence (form sent to the authors). Collected by the authors before May 2022; no AI-generated content.
- Wikimedia Commons: CC0, public domain, CC BY, CC BY-SA only; latest version uploaded before 1 Jan 2022; per-file attribution in `data/commons_manifest.csv`. Filtered by a title/category block list, a monochrome filter, and a zero-shot CLIP scene check that drops rescue scenes and bystanders, charts, and antique or sepia prints. Remaining off-target scenes (motorsport, emergency vehicles) are genuine photos, so they stay in the real pile, each with a regenerated twin.
- Team phone photos: team-owned, test only.
- Generated images: made by us with Stable Diffusion 2 inpainting, SDXL base 1.0 and FLUX.1-schnell on Modal.
