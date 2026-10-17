# Detector results: `det-r18-v4`

Crop aggregation: mean. Temperature 0.676. Thresholds `t-0v4`: τ_red = 0.7539 (val real FPR ≤ 1%), τ_amber = 0.5801 (≤ 5%). Fitted on val reals, Modal FP32, n = 638 real val photos.

| Test set | n real | n fake | AUROC | TPR @1% FPR | FPR @τ_red | FPR @τ_amber | TPR @τ_red | ECE |
|---|---|---|---|---|---|---|---|---|
| in_distribution | 641 | 870 | 0.936 | 0.674 | 0.012 | 0.051 | 0.698 | 0.062 |
| flux_holdout | 641 | 327 | 0.944 | 0.502 | 0.012 | 0.051 | 0.520 | 0.061 |
| inflation_only | 641 | 121 | 0.622 | 0.041 | 0.012 | 0.051 | 0.041 | 0.094 |
| regenerated_only | 641 | 641 | 0.985 | 0.746 | 0.012 | 0.051 | 0.778 | 0.087 |
| sdxl_only | 641 | 108 | 0.997 | 0.954 | 0.012 | 0.051 | 0.954 | 0.146 |
| real_damage_only | 605 | 0 | — | — | 0.012 | 0.050 | — | — |
| real_subgroup_cardd | 600 | 0 | — | — | 0.012 | 0.048 | — | — |
| real_subgroup_commons | 41 | 0 | — | — | 0.024 | 0.098 | — | — |
| diag_inflation_patch_crop | 641 | 121 | 0.839 | 0.198 | — | — | — | — |
| compression_jpeg_q50 | 641 | 1197 | 0.908 | 0.402 | 0.023 | 0.078 | 0.490 | 0.124 |
| compression_resize_half | 641 | 1197 | 0.807 | 0.183 | 0.168 | 0.310 | 0.616 | 0.048 |
| compression_screenshot | 641 | 1197 | 0.935 | 0.593 | 0.008 | 0.031 | 0.561 | 0.137 |

Rows: `in_distribution` = real vs regenerated + inflated + SDXL; `flux_holdout` = generator never trained on; `inflation_only` = partial edits on real photos; `regenerated_only` = content-matched pairs, so only generation traces can separate them; `sdxl_only` = fully fabricated scenes; `diag_inflation_patch_crop` = one crop centred on the edit vs one centre crop of real photos (diagnostic, no thresholds); `real_damage_only` = how often genuine damage photos are flagged; `real_subgroup_*` = FPR per real-photo source; `compression_*` = test split after JPEG q50 / half-size / screenshot re-encode.
