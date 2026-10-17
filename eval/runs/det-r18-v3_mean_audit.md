# Score audit: `det-r18-v3` (mean)

Spearman correlation between the model's score and features that carry no generation traces, within each class. Near 0 is good; a large value among real photos means some honest photos are flagged for reasons unrelated to AI editing.

| Feature | Real photos | Fakes |
|---|---|---|
| Final JPEG quality | -0.19 | +0.12 |
| Short side (px) | -0.26 | -0.02 |
| Bytes per pixel | -0.35 | +0.34 |
| Brightness | +0.08 | -0.09 |
| Saturation | -0.16 | +0.04 |

RED and AMBER rates among **real** test photos by subgroup (τ_red = 0.714, τ_amber = 0.529).

| Subgroup | n | RED rate | AMBER-or-RED rate |
|---|---|---|---|
| JPEG quality 65–74 | 225 | 0.000 | 0.022 |
| JPEG quality 75–84 | 209 | 0.005 | 0.038 |
| JPEG quality 85–95 | 207 | 0.029 | 0.043 |
| Short side 512–599 | 213 | 0.014 | 0.056 |
| Short side 600–689 | 237 | 0.013 | 0.030 |
| Short side 690–768 | 191 | 0.005 | 0.016 |
| Least compressible third (most detail) | 214 | 0.019 | 0.023 |
| Middle third | 213 | 0.009 | 0.028 |
| Most compressible third (smoothest) | 214 | 0.005 | 0.051 |
| Source: cardd | 600 | 0.010 | 0.032 |
| Source: commons | 41 | 0.024 | 0.073 |
| Damaged | 605 | 0.010 | 0.033 |
| Undamaged | 36 | 0.028 | 0.056 |
