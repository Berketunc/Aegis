# Detector handoff: A → B (and C)

What B needs to serve the detector on LinuxONE and what C's decision rule reads.

## Files (from `modal run train/export_onnx.py --run <run>`, local copies in `models/<run>/`, gitignored)

**Integrated:** B's risk service implements exactly the preprocessing below (`risk_service/precompute.py`, verified pixel-identical against `three_crops`). B loads `best.pt` (strict state dict) through a torchvision-free ResNet-18 twin, because the s390x image has no torchvision: `python3 linuxone/install_model.py --run det-r18-v4 --weights best.pt --temperature temperature.json --export export.json --thresholds eval/thresholds_t-0.json`. The low-resolution rule below is implemented in `CLMIPOL.cbl` (`LOW_RESOLUTION_EVIDENCE`, limit 480 px).

| File | What it is |
|---|---|
| `model.onnx` | ResNet-18, opset 17, static input `crops` `[3,3,224,224]` float32, output `logits` `[3,1]` |
| `temperature.json` | Calibration temperature `T` fitted on val |
| `parity_val.npz`, `parity_test.npz` | Parity sets: `crops` `[N,3,224,224,3]` uint8 (HWC, RGB), `logits_torch` `[N,3]` Modal FP32 reference, `labels` (1 = fake), `ids`, `mean`, `std`, `temperature` |
| `export.json` | Summary, incl. max logit diff PyTorch vs ONNX Runtime |

Compile the ONNX with zDLC exactly as for the stock ResNet-18 (§8.3): the shape and op set are identical.

## Preprocessing (must match training exactly)

Done once at upload time (`POST /evidence`, §8.4):

1. Decode, apply EXIF orientation, convert to RGB.
2. **Shrink only, never enlarge** (model `det-r18-v4` and later; `export.json` → `preprocess.no_upscale = true`):
   - short side > 640 → resize (bilinear) so the short side is 640;
   - 224 ≤ short side ≤ 640 → keep the native pixels, no resize;
   - short side < 224 → enlarge to 224 (the crop size), bilinear.

   Upscaling small real photos made them look synthetic to earlier models, so v4 is trained without it. (v1–v3 always resized the short side to 640; their `export.json` says `no_upscale: false`.)
3. Cut three 224×224 crops from the resized image (`w`, `h` = resized size, `C` = 224):
   - centre: `x = (w - C) // 2`, `y = (h - C) // 2`
   - upper-left grid cell: `x = w // 4 - C // 2`, `y = h // 4 - C // 2`
   - lower-right grid cell: `x = 3 * w // 4 - C // 2`, `y = 3 * h // 4 - C // 2`
   - clamp each `x` to `[0, w - C]` and `y` to `[0, h - C]`.
4. Scale to `[0,1]`, then normalise per channel with ImageNet `mean = [0.485, 0.456, 0.406]`, `std = [0.229, 0.224, 0.225]`, layout CHW.

Reference implementation: `three_crops()` in `train/train_modal.py`. Never shrink the whole photo to 224; that destroys the traces the model reads.

## Score

```
logits = model(crops)              # [3,1]
score  = sigmoid(mean(logits) / T) # calibrated probability the photo is AI-generated or AI-edited
```

Thresholds come from `eval/thresholds_<version>.json` (`tau_red`, `tau_amber`). The risk service returns `ai_score = score`; C's rule compares it to the thresholds (§9.3).

Current: model `det-r18-v4`, preliminary thresholds `t-0` (fitted on Modal FP32 scores): `T = 0.676`, `tau_red = 0.754`, `tau_amber = 0.580`. These become `t-1` after the LinuxONE parity check below.

## Recommendation for C's decision rule: low-quality evidence

The detector is least reliable on small or heavily recompressed photos (results in `eval/results.md`): on test photos halved in size, 17% of real ones would go RED. Suggested rule, so honest claimants with low-quality photos aren't sent to an investigator on the AI score alone:

- The risk service also returns `native_short_side` (short side of the original upload, before any resize).
- If `native_short_side < 480`, the AI score can raise **AMBER** but not **RED** on its own; add reason `LOW_RESOLUTION_EVIDENCE`. The duplicate-photo rule can still give RED.

## Parity check (§7.7), B runs on LinuxONE, A fits thresholds

1. For each of `parity_val.npz` and `parity_test.npz`: take `crops`, apply step 4 above (uint8 → float, normalise, HWC → CHW), run the compiled `.so` one sample (3 crops) at a time, and save the raw logits as a float32 `.npy` of shape `[N,3]` in the same order.
2. Send A the two `.npy` files and say which build produced them (NNPA or CPU).
3. A runs:
   ```
   python train/fit_thresholds_linuxone.py --run <run> --version t-1 \
       --val-logits linuxone_val_logits.npy --test-logits linuxone_test_logits.npy --build nnpa
   ```
   which writes `eval/thresholds_t-1.json` (fitted on LinuxONE scores) and `eval/parity_t-1.md` (drift report).

## Model version strings

`model_version` in `/risk/score` responses = the run name, e.g. `det-r18-v1`. `thresholds_version` = `t-1`, `t-2`, …
