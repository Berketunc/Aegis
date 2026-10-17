# Score audit: `det-r18-v4` (mean)

Spearman correlation between the model's score and features that carry no generation traces, within each class. Near 0 is good; a large value among real photos means some honest photos are flagged for reasons unrelated to AI editing.

| Feature | Real photos | Fakes |
|---|---|---|
| Final JPEG quality | -0.09 | +0.11 |
| Short side (px) | -0.08 | +0.01 |
| Bytes per pixel | -0.22 | +0.30 |
| Brightness | -0.01 | -0.16 |
| Saturation | -0.13 | +0.07 |

RED and AMBER rates among **real** test photos by subgroup (τ_red = 0.754, τ_amber = 0.580).

| Subgroup | n | RED rate | AMBER-or-RED rate |
|---|---|---|---|
| JPEG quality 65–74 | 225 | 0.004 | 0.044 |
| JPEG quality 75–84 | 209 | 0.014 | 0.057 |
| JPEG quality 85–95 | 207 | 0.019 | 0.053 |
| Short side 512–599 | 213 | 0.014 | 0.056 |
| Short side 600–689 | 237 | 0.017 | 0.051 |
| Short side 690–768 | 191 | 0.005 | 0.047 |
| Least compressible third (most detail) | 214 | 0.014 | 0.051 |
| Middle third | 213 | 0.009 | 0.033 |
| Most compressible third (smoothest) | 214 | 0.014 | 0.070 |
| Source: cardd | 600 | 0.012 | 0.048 |
| Source: commons | 41 | 0.024 | 0.098 |
| Damaged | 605 | 0.012 | 0.050 |
| Undamaged | 36 | 0.028 | 0.083 |
