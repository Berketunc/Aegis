# Detector results: `det-r18-v3`

Crop aggregation: grid16max. Temperature 2.239. Thresholds `t-0grid`: τ_red = 0.8424 (val real FPR ≤ 1%), τ_amber = 0.7675 (≤ 5%). Fitted on val reals, Modal FP32, n = 638 real val photos.

| Test set | n real | n fake | AUROC | TPR @1% FPR | FPR @τ_red | FPR @τ_amber | TPR @τ_red | ECE |
|---|---|---|---|---|---|---|---|---|
| in_distribution | 641 | 870 | 0.940 | 0.644 | 0.022 | 0.058 | 0.690 | 0.208 |
| flux_holdout | 641 | 327 | 0.938 | 0.333 | 0.022 | 0.058 | 0.394 | 0.320 |
| inflation_only | 641 | 121 | 0.662 | 0.041 | 0.022 | 0.058 | 0.058 | 0.421 |
| regenerated_only | 641 | 641 | 0.984 | 0.718 | 0.022 | 0.058 | 0.774 | 0.271 |
| sdxl_only | 641 | 108 | 0.991 | 0.880 | 0.022 | 0.058 | 0.898 | 0.480 |
| real_damage_only | 605 | 0 | — | — | 0.023 | 0.055 | — | — |
| real_subgroup_cardd | 600 | 0 | — | — | 0.023 | 0.053 | — | — |
| real_subgroup_commons | 41 | 0 | — | — | 0.000 | 0.122 | — | — |
| diag_inflation_patch_crop | 641 | 121 | 0.858 | 0.256 | — | — | — | — |
| compression_jpeg_q50 | 641 | 1197 | 0.904 | 0.425 | 0.019 | 0.080 | 0.477 | 0.170 |
| compression_resize_half | 641 | 1197 | 0.826 | 0.280 | 0.023 | 0.161 | 0.374 | 0.154 |
| compression_screenshot | 641 | 1197 | 0.935 | 0.576 | 0.005 | 0.033 | 0.517 | 0.169 |

Rows: `in_distribution` = real vs regenerated + inflated + SDXL; `flux_holdout` = generator never trained on; `inflation_only` = partial edits on real photos; `regenerated_only` = content-matched pairs, so only generation traces can separate them; `sdxl_only` = fully fabricated scenes; `diag_inflation_patch_crop` = one crop centred on the edit vs one centre crop of real photos (diagnostic, no thresholds); `real_damage_only` = how often genuine damage photos are flagged; `real_subgroup_*` = FPR per real-photo source; `compression_*` = test split after JPEG q50 / half-size / screenshot re-encode.
