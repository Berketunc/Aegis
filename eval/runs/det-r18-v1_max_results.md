# Detector results: `det-r18-v1`

Crop aggregation: max. Temperature 1.230. Thresholds `t-0max`: τ_red = 0.9445 (val real FPR ≤ 1%), τ_amber = 0.8168 (≤ 5%). Fitted on val reals, Modal FP32, n = 638 real val photos.

| Test set | n real | n fake | AUROC | TPR @1% FPR | FPR @τ_red | FPR @τ_amber | TPR @τ_red | ECE |
|---|---|---|---|---|---|---|---|---|
| in_distribution | 641 | 870 | 0.933 | 0.695 | 0.008 | 0.031 | 0.589 | 0.109 |
| flux_holdout | 641 | 327 | 0.946 | 0.560 | 0.008 | 0.031 | 0.391 | 0.217 |
| inflation_only | 641 | 121 | 0.598 | 0.066 | 0.008 | 0.031 | 0.017 | 0.255 |
| regenerated_only | 641 | 641 | 0.986 | 0.783 | 0.008 | 0.031 | 0.660 | 0.177 |
| sdxl_only | 641 | 108 | 0.990 | 0.880 | 0.008 | 0.031 | 0.806 | 0.337 |
| real_damage_only | 605 | 0 | — | — | 0.008 | 0.031 | — | — |
| real_subgroup_cardd | 600 | 0 | — | — | 0.007 | 0.030 | — | — |
| real_subgroup_commons | 41 | 0 | — | — | 0.024 | 0.049 | — | — |
| compression_jpeg_q50 | 641 | 1197 | 0.874 | 0.236 | 0.027 | 0.140 | 0.434 | 0.086 |
| compression_resize_half | 641 | 1197 | 0.718 | 0.093 | 0.598 | 0.899 | 0.835 | 0.304 |
| compression_screenshot | 641 | 1197 | 0.923 | 0.456 | 0.009 | 0.050 | 0.451 | 0.081 |

Rows: `in_distribution` = real vs regenerated + inflated + SDXL; `flux_holdout` = generator never trained on; `inflation_only` = partial edits on real photos; `regenerated_only` = content-matched pairs, so only generation traces can separate them; `sdxl_only` = fully fabricated scenes; `real_damage_only` = how often genuine damage photos are flagged; `real_subgroup_*` = FPR per real-photo source; `compression_*` = test split after JPEG q50 / half-size / screenshot re-encode.
