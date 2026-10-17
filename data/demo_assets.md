# Demo assets (A → D), plan.md §12.1

Picked from the **test** split by the final model's actual scores (`det-r18-v4`, thresholds `t-0`: τ_amber 0.580, τ_red 0.754), so the live demo behaves as the results tables say. Images are local only (`demo_assets/`, gitignored). Re-fetch with:

```
modal volume get claimshield-data /data/dataset/test/<real|fake>/<id>.jpg demo_assets/<id>.jpg
```

| Demo step | File | What it is | Score | Band |
|---|---|---|---|---|
| 1. Genuine claim | `cardd_001627.jpg` | Real scrape on a blue bumper (CarDD) | 0.003 | GREEN |
| 2. Fully generated damage | `sdxl_000343.jpg` | SDXL: crumpled green bumper | 1.000 | RED |
| 2b. Unseen generator | `flux_000387.jpg` | FLUX.1-schnell: shattered windscreen at night (never in training) | 1.000 | RED |
| 3. Inflated damage, RED | `cardd_001882.jpg` → `inflate_cardd_001882.jpg` | Real dented door (0.318, GREEN) → AI punches a hole in it | 0.829 | RED |
| 3b. Inflated damage, AMBER | `cardd_003271.jpg` → `inflate_cardd_003271.jpg` | Real scraped door (0.225, GREEN) → AI adds a second scrape | 0.635 | AMBER |
| Heatmap check | `inflate_cardd_001882_mask.png`, `inflate_cardd_003271_mask.png` | Where the edit actually is | — | — |

Notes for the script:

- Say plainly that small edits are the hard case: only 2 of 115 test edits on GREEN photos reach RED, 7 reach AMBER (`eval/results.md`). Step 3 shows the good case; don't imply it always works.
- Step 4 (plate-swapped duplicate) is caught by the perceptual-hash duplicate check, not the detector; none of these photos shows a plate. Use any photo submitted on two claims, edited slightly, for that step.
- Use fictional personas only. CarDD photos are under a non-commercial research licence: fine for the demo and video, not for redistribution.
- Scores above are Modal FP32. On LinuxONE (NNPA, 16-bit) they shift slightly; re-check the bands with the compiled model before recording.
