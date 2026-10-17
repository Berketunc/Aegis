# Spot-check log (plan.md §7.2, §7.3)

50 random images per set, viewed as contact sheets (`data/contact_sheet.py`), 17 Oct 2026.

| Set | Checked | Rejected by eye | Notes |
|---|---|---|---|
| SDXL base 1.0 | 50 | 0 | All photoreal, no cartoons or renders. About 40% of damage prompts produced no visible damage, so damage is measured with CLIP instead of taken from the prompt. Garbled number plates appear. |
| FLUX.1-schnell | 50 | 0 | Very claim-like. A few frames include a phone in the shot (from "taken on a smartphone" prompts). Test only, so this cannot become a training shortcut. |
| SD2 inpainting, inflated | 50 | 0 | Edits blend in at thumbnail size; mask-limited, so the rest of the frame is the real photo. |
| SD2 inpainting, regenerated | 4 (smoke) | 0 | Near-identical to the parent real photo, with slight texture and colour shifts: the intended B-Free self-conditioned fake. |
| Wikimedia Commons (real) | 50 | n/a, filtered by rule | Only about 10 of 50 were claim-like. Off-target: motorsport, airport fire engines, rescue scenes with people, a chart, sepia stereo cards, bumper cars, empty roads. Fixes: extended title/category block list; monochrome and single-tint filter; CLIP scene check that hard-rejects people/rescue scenes, charts and antique prints. Other off-target scenes are genuine photos and stay, each with a regenerated twin. |

## Filters that came out of the spot-checks

- Commons monochrome or single-tint (sepia) photos: rejected in `prep_real.py` (saturation < 20 or hue spread < 12).
- Commons rescue scenes, bystander groups, charts, antique prints: rejected via CLIP scene tag (`damage_tag.py`, `scene_hard_reject`).
- Measured damage: CLIP ViT-L/14 zero-shot, threshold at the 10th percentile of CarDD (all damaged) scores.

| Set | Damaged by measurement |
|---|---|
| CarDD | 99.5% at p ≥ 0.5 (calibration set) |
| SDXL | 50% |
| FLUX | 74% |
| Commons | about 15% |
