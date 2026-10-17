# Detector results

All numbers are on the held-out **test** split (split by source photo, so no test photo or any of its fakes was seen in training). Thresholds are fitted on the validation split: τ_red where real-photo FPR ≤ 1%, τ_amber where it is ≤ 5%. These are Modal FP32 numbers; LinuxONE parity results go in `eval/parity_t-1.md`.

## Chosen model: `det-r18-v4`

Temperature 0.676; τ_red = 0.754; τ_amber = 0.580 (`eval/thresholds_t-0.json`).

| Test set | n real | n fake | AUROC | TPR @1% FPR | Real FPR @τ_red | Real FPR @τ_amber | TPR @τ_red |
|---|---|---|---|---|---|---|---|
| In-distribution (regenerated + inflated + SDXL) | 641 | 870 | 0.936 | 0.674 | 1.2% | 5.1% | 0.698 |
| **FLUX.1-schnell, never trained on** | 641 | 327 | 0.944 | 0.502 | 1.2% | 5.1% | 0.520 |
| Damage inflation (partial edits) | 641 | 121 | 0.622 | 0.041 | 1.2% | 5.1% | 0.041 |
| Regenerated only (content-matched) | 641 | 641 | 0.985 | 0.746 | 1.2% | 5.1% | 0.778 |
| SDXL only (fully fabricated) | 641 | 108 | 0.997 | 0.954 | 1.2% | 5.1% | 0.954 |
| **Real damage photos** | 605 | 0 | — | — | 1.2% | 5.0% | — |
| Real: CarDD | 600 | 0 | — | — | 1.2% | 4.8% | — |
| Real: Wikimedia Commons | 41 | 0 | — | — | 2.4% | 9.8% | — |
| Diagnostic: one crop on the edit | 641 | 121 | 0.839 | 0.198 | — | — | — |
| Compression: JPEG q50 | 641 | 1197 | 0.908 | 0.402 | 2.3% | 7.8% | 0.490 |
| Compression: half size | 641 | 1197 | 0.807 | 0.183 | 16.8% | 31.0% | 0.616 |
| Compression: screenshot re-encode | 641 | 1197 | 0.935 | 0.593 | 0.8% | 3.1% | 0.561 |

## Candidates compared

Selection rule (plan.md §7.5): TPR at 1% FPR on the FLUX hold-out and FPR on real damage photos, then robustness of the FPR under real-world handling, then latency.

| Model | In-dist AUROC | FLUX AUROC | FLUX TPR @1% | Real damage FPR @red | JPEG q50 FPR @red | Half-size FPR @red | Inflation AUROC |
|---|---|---|---|---|---|---|---|
| ResNet-18 v1 (plain) | 0.940 | 0.960 | 0.676 | 0.7% | 5.6% | 89.2% | 0.602 |
| CLIP ViT-B/16 linear probe | 0.913 | 0.919 | 0.450 | 0.3% | 2.5% | 15.3% | 0.599 |
| ResNet-18 v3 (degradation aug + mask crops) | 0.941 | 0.960 | 0.495 | 1.0% | 3.7% | 23.1% | 0.612 |
| ResNet-18 v3, 4×4 scan max | 0.940 | 0.938 | 0.333 | 2.3% | 1.9% | 2.3% | 0.662 |
| **ResNet-18 v4 (v3 + never upscale) — chosen** | 0.936 | 0.944 | 0.502 | 1.2% | 2.3% | 16.8% | 0.622 |

Why v4: it ties v3 on the plan's two criteria and wrongly flags fewer honest photos after compression and resizing. Its scores also track unrelated photo properties less (see the audits below). The CLIP probe detected less on every row (AUROC and TPR), including the unseen generator (FLUX TPR 0.45 vs 0.50), though its false-positive rates were lower (real damage 0.3%, half size 15.3%); at equal FPR it catches fewer fakes.

## What the model learned (and didn't)

- **Generation traces, not content.** On content-matched pairs (each real photo against its own regenerated copy) AUROC is 0.985. Scene content cannot separate those pairs, so the model is reading pixel-level traces.
- **Generalises to an unseen generator.** FLUX.1-schnell was never in training: AUROC 0.944, TPR 0.502 at 1% FPR. Lower than in-distribution, as expected, and reported rather than hidden.
- **Partial edits are the main weakness.** Damage inflation scores AUROC 0.622 with the standard 3 crops, but 0.839 when a crop sits on the edit. The model can see edits; the crops usually miss a patch covering 3–12% of the frame. Scanning 16 crops and taking the max did not fix it (real photos' max rises too). The heatmap is still useful to show adjusters where to look.
- **Small photos are risky.** On test photos halved in size, 16.8% of real ones exceed τ_red (v1: 89%). Recommended policy rule in `train/HANDOFF.md`: below 480 px on the short side the AI score alone can give AMBER but not RED.

## Shortcut evidence

Dataset level (`data/dataset_card.md`): logistic regressions on file size and dimensions, and on global colour, score AUROC 0.50–0.55; on content-matched pairs 0.51–0.52. Damage share differs by 0.5 points between classes.

Model level (`eval/runs/det-r18-v4_mean_audit.md`): correlation of the score with unrelated properties among real test photos, and RED rate by subgroup.

| Feature | Real photos | Fakes |
|---|---|---|
| Final JPEG quality | -0.09 | +0.11 |
| Short side (px) | -0.08 | +0.01 |
| Bytes per pixel | -0.22 | +0.30 |
| Brightness | -0.01 | -0.16 |
| Saturation | -0.13 | +0.07 |

RED rate among real test photos stays between 0.4% and 2.8% across JPEG quality, size, compressibility, source and damage subgroups (full table in the audit file; the Commons and undamaged groups are small, n = 41 and 36).

