# Detector results: `det-r18-v3`

Crop aggregation: max. Temperature 1.122. Thresholds `t-0max3`: τ_red = 0.9019 (val real FPR ≤ 1%), τ_amber = 0.8162 (≤ 5%). Fitted on val reals, Modal FP32, n = 638 real val photos.

| Test set | n real | n fake | AUROC | TPR @1% FPR | FPR @τ_red | FPR @τ_amber | TPR @τ_red | ECE |
|---|---|---|---|---|---|---|---|---|
| in_distribution | 641 | 870 | 0.930 | 0.664 | 0.006 | 0.031 | 0.651 | 0.103 |
| flux_holdout | 641 | 327 | 0.940 | 0.437 | 0.006 | 0.031 | 0.410 | 0.209 |
| inflation_only | 641 | 121 | 0.614 | 0.074 | 0.006 | 0.031 | 0.074 | 0.255 |
| regenerated_only | 641 | 641 | 0.979 | 0.739 | 0.006 | 0.031 | 0.722 | 0.168 |
| sdxl_only | 641 | 108 | 0.992 | 0.880 | 0.006 | 0.031 | 0.870 | 0.334 |
| real_damage_only | 605 | 0 | — | — | 0.003 | 0.026 | — | — |
| real_subgroup_cardd | 600 | 0 | — | — | 0.003 | 0.025 | — | — |
| real_subgroup_commons | 41 | 0 | — | — | 0.049 | 0.122 | — | — |
| compression_jpeg_q50 | 641 | 1197 | 0.896 | 0.358 | 0.023 | 0.056 | 0.472 | 0.079 |
| compression_resize_half | 641 | 1197 | 0.798 | 0.171 | 0.080 | 0.231 | 0.456 | 0.145 |
| compression_screenshot | 641 | 1197 | 0.924 | 0.551 | 0.009 | 0.022 | 0.498 | 0.067 |

Rows: `in_distribution` = real vs regenerated + inflated + SDXL; `flux_holdout` = generator never trained on; `inflation_only` = partial edits on real photos; `regenerated_only` = content-matched pairs, so only generation traces can separate them; `sdxl_only` = fully fabricated scenes; `real_damage_only` = how often genuine damage photos are flagged; `real_subgroup_*` = FPR per real-photo source; `compression_*` = test split after JPEG q50 / half-size / screenshot re-encode.
