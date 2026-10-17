# Detector results: `det-r18-v3`

Crop aggregation: mean. Temperature 0.776. Thresholds `t-0`: τ_red = 0.7141 (val real FPR ≤ 1%), τ_amber = 0.5292 (≤ 5%). Fitted on val reals, Modal FP32, n = 638 real val photos.

| Test set | n real | n fake | AUROC | TPR @1% FPR | FPR @τ_red | FPR @τ_amber | TPR @τ_red | ECE |
|---|---|---|---|---|---|---|---|---|
| in_distribution | 641 | 870 | 0.941 | 0.717 | 0.011 | 0.034 | 0.753 | 0.067 |
| flux_holdout | 641 | 327 | 0.960 | 0.495 | 0.011 | 0.034 | 0.557 | 0.076 |
| inflation_only | 641 | 121 | 0.612 | 0.033 | 0.011 | 0.034 | 0.058 | 0.090 |
| regenerated_only | 641 | 641 | 0.993 | 0.810 | 0.011 | 0.034 | 0.850 | 0.089 |
| sdxl_only | 641 | 108 | 0.998 | 0.935 | 0.011 | 0.034 | 0.954 | 0.130 |
| real_damage_only | 605 | 0 | — | — | 0.010 | 0.033 | — | — |
| real_subgroup_cardd | 600 | 0 | — | — | 0.010 | 0.032 | — | — |
| real_subgroup_commons | 41 | 0 | — | — | 0.024 | 0.073 | — | — |
| diag_inflation_patch_crop | 641 | 121 | 0.858 | 0.256 | — | — | — | — |
| compression_jpeg_q50 | 641 | 1197 | 0.920 | 0.361 | 0.037 | 0.112 | 0.632 | 0.091 |
| compression_resize_half | 641 | 1197 | 0.836 | 0.255 | 0.231 | 0.540 | 0.737 | 0.068 |
| compression_screenshot | 641 | 1197 | 0.942 | 0.653 | 0.008 | 0.037 | 0.631 | 0.125 |

Rows: `in_distribution` = real vs regenerated + inflated + SDXL; `flux_holdout` = generator never trained on; `inflation_only` = partial edits on real photos; `regenerated_only` = content-matched pairs, so only generation traces can separate them; `sdxl_only` = fully fabricated scenes; `diag_inflation_patch_crop` = one crop centred on the edit vs one centre crop of real photos (diagnostic, no thresholds); `real_damage_only` = how often genuine damage photos are flagged; `real_subgroup_*` = FPR per real-photo source; `compression_*` = test split after JPEG q50 / half-size / screenshot re-encode.
