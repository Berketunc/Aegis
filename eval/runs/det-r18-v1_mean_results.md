# Detector results: `det-r18-v1`

Temperature 0.794. Thresholds `t-0`: τ_red = 0.7565 (val real FPR ≤ 1%), τ_amber = 0.5660 (≤ 5%). Fitted on val reals, Modal FP32, n = 638 real val photos.

| Test set | n real | n fake | AUROC | TPR @1% FPR | FPR @τ_red | FPR @τ_amber | TPR @τ_red | ECE |
|---|---|---|---|---|---|---|---|---|
| in_distribution | 641 | 870 | 0.940 | 0.786 | 0.006 | 0.042 | 0.766 | 0.050 |
| flux_holdout | 641 | 327 | 0.960 | 0.676 | 0.006 | 0.042 | 0.636 | 0.078 |
| inflation_only | 641 | 121 | 0.602 | 0.017 | 0.006 | 0.042 | 0.000 | 0.084 |
| regenerated_only | 641 | 641 | 0.995 | 0.908 | 0.006 | 0.042 | 0.883 | 0.092 |
| sdxl_only | 641 | 108 | 0.997 | 0.926 | 0.006 | 0.042 | 0.926 | 0.151 |
| real_damage_only | 605 | 0 | — | — | 0.007 | 0.041 | — | — |
| real_subgroup_cardd | 600 | 0 | — | — | 0.005 | 0.040 | — | — |
| real_subgroup_commons | 41 | 0 | — | — | 0.024 | 0.073 | — | — |
| compression_jpeg_q50 | 641 | 1197 | 0.902 | 0.332 | 0.056 | 0.123 | 0.634 | 0.055 |
| compression_resize_half | 641 | 1197 | 0.732 | 0.093 | 0.892 | 0.972 | 0.977 | 0.295 |
| compression_screenshot | 641 | 1197 | 0.935 | 0.679 | 0.011 | 0.062 | 0.684 | 0.066 |

Rows: `in_distribution` = real vs regenerated + inflated + SDXL; `flux_holdout` = generator never trained on; `inflation_only` = partial edits on real photos; `regenerated_only` = content-matched pairs, so only generation traces can separate them; `sdxl_only` = fully fabricated scenes; `real_damage_only` = how often genuine damage photos are flagged; `real_subgroup_*` = FPR per real-photo source; `compression_*` = test split after JPEG q50 / half-size / screenshot re-encode.
