# Detector results: `clip-b16-probe-v1`

Temperature 0.977. Thresholds `probe`: τ_red = 0.9161 (val real FPR ≤ 1%), τ_amber = 0.7534 (≤ 5%). Fitted on val reals, Modal FP16 CLIP + FP64 LR, n = 638 real val photos.

| Test set | n real | n fake | AUROC | TPR @1% FPR | FPR @τ_red | FPR @τ_amber | TPR @τ_red | ECE |
|---|---|---|---|---|---|---|---|---|
| in_distribution | 641 | 870 | 0.913 | 0.503 | 0.006 | 0.033 | 0.383 | 0.046 |
| flux_holdout | 641 | 327 | 0.919 | 0.450 | 0.006 | 0.033 | 0.367 | 0.086 |
| inflation_only | 641 | 121 | 0.599 | 0.017 | 0.006 | 0.033 | 0.008 | 0.129 |
| regenerated_only | 641 | 641 | 0.961 | 0.566 | 0.006 | 0.033 | 0.415 | 0.085 |
| sdxl_only | 641 | 108 | 0.985 | 0.676 | 0.006 | 0.033 | 0.611 | 0.201 |
| real_damage_only | 605 | 0 | — | — | 0.003 | 0.031 | — | — |
| real_subgroup_cardd | 600 | 0 | — | — | 0.003 | 0.030 | — | — |
| real_subgroup_commons | 41 | 0 | — | — | 0.049 | 0.073 | — | — |
| compression_jpeg_q50 | 641 | 1197 | 0.763 | 0.082 | 0.025 | 0.066 | 0.103 | 0.266 |
| compression_resize_half | 641 | 1197 | 0.646 | 0.043 | 0.153 | 0.566 | 0.344 | 0.154 |
| compression_screenshot | 641 | 1197 | 0.874 | 0.251 | 0.033 | 0.175 | 0.458 | 0.071 |

Rows: `in_distribution` = real vs regenerated + inflated + SDXL; `flux_holdout` = generator never trained on; `inflation_only` = partial edits on real photos; `regenerated_only` = content-matched pairs, so only generation traces can separate them; `sdxl_only` = fully fabricated scenes; `real_damage_only` = how often genuine damage photos are flagged; `real_subgroup_*` = FPR per real-photo source; `compression_*` = test split after JPEG q50 / half-size / screenshot re-encode.
