# Person B risk service

Internal FastAPI service for upload preprocessing, encrypted evidence originals,
trained detector inference, C's privacy adapter, metadata flags and cross-claim
duplicate lookup. C's gateway owns decision bands and the 300 ms fail-to-AMBER
timeout. This service returns no decision band.

The service can accept uploads before A's model arrives. `/risk/score` returns
HTTP 503 until a trained model, C's privacy adapter and versioned thresholds are
configured. It never substitutes a stock classifier or random risk score.

## Run on LinuxONE

Use the repository's `linuxone/start_services.sh` wrapper, or build from the
repository root:

```bash
sudo docker build -f risk_service/Dockerfile -t aegis-risk .
```

The Dockerfile retains the public event image's s390x `torch` and `torch_nnpa`
packages. Runtime UID/GID is `1000:1000`; any bind-mounted data directory must be
writable by that user. It serves internal port 8000; do not publish it to the VM
host. Mount the data directory persistently and keep a protected backup of the
encryption key alongside the evidence backup. Losing the key loses access to
encrypted originals. Do not commit runtime secrets, photos or model weights.

On this s390x image, pip builds `cryptography` from source. The Dockerfile
installs a C compiler, OpenSSL/libffi development headers and pkg-config inside
the image before pip runs. It detects Debian/Ubuntu and RPM package managers;
the container's distribution can differ from the VM host. Rust was successfully
bootstrapped by the Python build backend in the observed VM build. The first
source compilation can take several minutes. Installing headers on the VM host
alone does not fix a missing-header error inside a Docker build.

Configuration:

| Variable | Meaning |
| --- | --- |
| `AEGIS_DATA_DIR` | Persistent index and evidence directory; default `/data` |
| `AEGIS_RISK_SECRET` | Gateway's shared secret, sent as `X-Risk-Secret` |
| `AEGIS_EVIDENCE_KEY_B64` | Base64 encoding of exactly 32 random key bytes |
| `AEGIS_MODEL_MANIFEST` | Path to A's trained model manifest JSON |
| `AEGIS_MODEL_DEVICE` | Explicit `cpu` (default) or `nnpa`; no silent device fallback |
| `AEGIS_TORCH_THREADS` | Torch CPU threads; default `1` |
| `AEGIS_PRIVACY_ADAPTER` | Trusted import path, for example `privacy_adapter:process` |
| `HMAC_KEY` | Stable secret shared with C's gateway, at least 32 bytes for the built-in adapter |
| `AEGIS_THRESHOLDS_PATH` | Versioned threshold JSON shared with C's gateway |

If the wrapper mounts A's code in `/models` and C's adapter in `/integrations`,
set `PYTHONPATH=/app:/models:/integrations`.

## API contract for C and D

`GET /health` reports `ready`, `upload_ready`, detector device/version/hash,
privacy availability and the threshold version. HTTP 200 indicates the process
is alive; inspect `ready` before claiming the full scoring path is available.

`POST /evidence` requires `X-Risk-Secret` and multipart field **`file`**. The
gateway forwards the upload and keeps this credential out of the browser.
Supported images: JPEG, PNG and WebP, up to 15 MiB, 40 million pixels and an
aspect ratio of 20:1. The response includes:

```json
{
  "evidence_id": "EV-<full 64-character SHA-256>",
  "sha256": "<full 64-character SHA-256>",
  "preprocessing_version": "rgb-fit640-noupscale-bilinear-centre-gridcells-v2",
  "phash_version": "phash64-dct32-median64-v1",
  "crop_boxes": [[288,88,512,312],[88,0,312,224],[488,188,712,412]],
  "timings_ms": {"upload_preprocess": 0.0}
}
```

The crop-box example is an oriented 800×400 photo, kept at native size because its
short side is already below 640 (never upscaled). Upload
preprocessing includes file reads, image decode, encryption, crop preparation and
storage, and remains separate from risk-decision latency.

`POST /risk/score` requires the same header and this JSON shape:

```json
{
  "claim_id": "CLM-000123",
  "description": "Description from the claim form",
  "evidence": [{"evidence_id": "EV-<full SHA-256 returned by upload>"}],
  "privacy_context": {"claimant": {}, "policy_number": "provided privately to C's adapter"},
  "policy_token": "optional-opaque-policy-pseudonym-from-C"
}
```

`privacy_context` and `policy_token` are optional. C must agree the form-field
structure needed by their privacy rules. Never put the raw policy number in
`policy_token`. The service never logs the description or saves the request body.

The successful response follows plan §14 and additionally includes
`thresholds_version`, `redacted_record`, per-evidence scores, detailed duplicate
matches and `async: {eligible, status: "gateway_managed", heatmap: "on_request"}`. `duplicate_of` is
the matched **other claim ID** or null. Multiple photos use their maximum
calibrated AI score; metadata flags are combined across photos. The risk service
reports eligibility without directly submitting a Granite job. When enabled,
C's gateway queues and polls Granite after committing its final AMBER/RED band.
C2PA is only an unverified marker-presence check, never signature
validation. Missing EXIF never independently decides anything.

Privacy rules, metadata extraction and inference run in parallel. After those
succeed, an atomic SQLite duplicate check and association commit prevents two
simultaneous submissions from missing each other. Failed inference/privacy
processing leaves no claim association. A duplicate is a perceptual-hash Hamming
distance **≤8** against evidence registered to a different claim. Reusing a
claim ID excludes its own evidence; gateway idempotency remains C's responsibility.
The MVP index scans existing claim evidence; measure its scaling before claiming
large production capacity.

Stage timings are wall-clock milliseconds; parallel timings do not sum to the
total. Inference timing includes model-lock waiting, preprocessing normalization,
device transfer and output conversion. Stored crop loading has its own timing.

Failures: 401 invalid secret, 404 missing evidence, 413 upload too large, 415
unsupported/invalid image, 422 malformed fields and 503 unavailable dependencies
or processing failures. C must convert risk-service failures/timeouts to AMBER.

## Preprocessing, identical to A's training recipe

`PREPROCESS_VERSION = rgb-fit640-noupscale-bilinear-centre-gridcells-v2` is a
line-for-line port of `three_crops()` / `fit_short_side()` / `grid_crops()` in
`train/train_modal.py` (model `det-r18-v4`), verified pixel-identical against
that reference on seven image sizes:

1. Apply EXIF orientation, convert to RGB.
2. **Shrink only, never enlarge** (bilinear): short side > 640 → short side 640;
   224 ≤ short side ≤ 640 → native pixels; short side < 224 → lifted to 224.
   Upscaling small photos made honest photos look synthetic to earlier models.
3. Three 224×224 crops: centre `((w-224)//2, (h-224)//2)`, upper-left grid cell
   `(w//4-112, h//4-112)`, lower-right grid cell `(3w//4-112, 3h//4-112)`, each
   clamped into the image.
4. Store a non-pickle NumPy array `uint8[3,3,224,224]` in RGB CHW order. The
   native short side (before any resize) is kept in the metadata flags as
   `native_short_side`; the gateway uses it for the low-resolution rule.
5. At inference divide by 255 and apply the manifest's RGB mean/std. Average
   the three logits first, then apply temperature scaling and sigmoid.

The heatmap uses the same fit and a 4×4 grid of evenly spaced windows
(`grid_crops`, row-major). Any change here must be mirrored in training and
bump the version string, or stored crops are refused at scoring time.

Original files are AES-256-GCM encrypted with a fresh 12-byte nonce, with the
SHA-256 bound as authenticated associated data. Crop arrays and the SQLite index
are ordinary local files in restricted directories; they remain sensitive and
must stay on the VM. Evidence filenames use full SHA-256 values; there is no
unauthenticated original-download endpoint.

## Trained model manifest from A

`linuxone/install_model.py` writes this manifest from A's `best.pt`,
`temperature.json` and `export.json` (train/HANDOFF.md). The event's s390x image
has torch but no torchvision, so the weights load into the pure-torch twin
`risk_service/resnet18.py` with strict key matching:

```json
{
  "model_version": "det-r18-v4",
  "preprocessing_version": "rgb-fit640-noupscale-bilinear-centre-gridcells-v2",
  "format": "state_dict",
  "artifact": "detector.pth",
  "sha256": "<digest of detector.pth, checked at load>",
  "factory": "risk_service.resnet18:build_detector",
  "factory_kwargs": {"num_outputs": 1},
  "output_kind": "binary_logit",
  "mean": [0.485, 0.456, 0.406],
  "std": [0.229, 0.224, 0.225],
  "temperature": 0.676,
  "calibration_bias": 0.0
}
```

`binary_logit` requires `[3]` or `[3,1]`, where positive means AI-generated/edited.
`two_class_logits` requires `[3,2]` in **[real, fake]** order and uses fake minus
real as the binary logit. The model must return a tensor, not a dict or tuple.
The probability is `sigmoid(mean_crop_logit / temperature + calibration_bias)`.

For eager weights, use `format: "state_dict"`, `artifact: "detector.pth"`,
`factory: "detector_factory:build_model"`, and optional `factory_kwargs: {}`.
Mount that trusted Python factory where it is importable. Weights load with
`weights_only=True` and strict key matching. Loading and a production-shape
warm-up run happen once at process startup; a failure leaves scoring unavailable.

Threshold JSON shape:

```json
{"version": "t-0", "model_version": "det-r18-v4", "tau_amber": 0.5801, "tau_red": 0.7539, "...": "extra keys are ignored"}
```

A's `eval/thresholds_t-0.json` (Modal FP32) is installed as is; `t-1` replaces it
after the LinuxONE parity check (`linuxone/parity_logits.py`,
`train/fit_thresholds_linuxone.py`). The same file/version must be used by C's
gateway. Model/config changes require a service restart.

## C's privacy adapter

The integrated adapter is now available as
`AEGIS_PRIVACY_ADAPTER=risk_service.c_privacy_adapter:process`. It imports C's
shared `privacy.redact` and `privacy.build_egress`, checks its stable `HMAC_KEY`
and log availability during startup, and writes real append-only rows to
`AEGIS_DATA_DIR/privacy_egress.sqlite3`. `EG-RISK-...` IDs identify persisted
allowlisted payload preparation, **not an outbound transmission**. The gateway
retains its own final decision egress log. Missing HMAC configuration leaves
privacy unavailable.

The gateway sends an already-pseudonymised description, opaque policy token and
minimal `{claim_id, damage_category}` privacy context; raw claimant form fields
stay with C's gateway/vault. The built-in adapter can also accept an optional
`gazetteer` context from a trusted internal caller, without persisting its values.
Both services use the same HMAC key and rules.

The gateway sends canonical `X-Risk-Secret`, plus `X-Shared-Secret` solely for
C's legacy labelled stub compatibility. B authenticates `X-Risk-Secret` only.
Both threshold-file readers accept `version` and `thresholds_version`; a file
containing conflicting values is rejected. A gateway/risk version mismatch
returns AMBER instead of applying different thresholds silently.

The gateway persists an explicit `privacy_review` status. `pending` and
`unavailable` quarantine descriptions and free-form damage categories: adjuster
views return null, and export-candidate payloads omit those fields. Only a
successful second-pass review releases the text, independently of whether the
advisory note passed its separate validation. Authorised original access is
explicit and journaled. `rules_only` means the optional second pass was disabled
or not requested for a GREEN claim; it is never labelled model-reviewed. Pending
jobs lost on restart become unavailable and remain quarantined. The private
first-pass claim record and B's PREPARED audit candidates can still contain
identifiers the rules missed; neither is an external transmission.

Concurrent submissions with one idempotency key are serialised before claim-ID
allocation and risk scoring (one gateway worker). The first commits; the second
replays its result. Locks are discarded once holders and waiters finish.

Gateway timing semantics: persisted decisions, journal rows, GET responses and
replays contain `timings_ms.precommit_total`, which explicitly excludes the
SQLite commit. A fresh POST response additionally contains measured `commit`
and `total`, including the commit. A replay reports its current request latency
separately under `replay_timings_ms.total`; it does not invent the original
post-commit timing or rewrite the append-only journal. Use a client-side
benchmark for full HTTP latency.

For a custom adapter instead of the integrated implementation, provide a
callable such as `privacy_adapter:process`:

```python
def process(*, description: str, context: dict) -> dict:
    # Implement C's real regex/gazetteer/HMAC rules and egress allowlist/log here.
    # Keep the HMAC key and token map on the VM. Do not return raw identifiers.
    return {
        "redacted_record": redacted_record,
        "egress_payload_id": persisted_egress_payload_id,
    }
```

The custom-adapter example is illustrative; the integrated adapter described
above is implemented. The service refuses scoring if no adapter is configured, import fails,
or the return value is incomplete. B does not fabricate an egress-log entry.

## Local tests

No Torch installation is required for the evidence/orchestration unit tests:

```bash
python3 -m pip install -r risk_service/requirements.txt pytest httpx
python3 -m pytest risk_service/tests -q
```

Tests exercise EXIF orientation and crop geometry, encryption/decryption,
duplicate boundaries and persistence, retries, auth, invalid uploads, fail-closed
scoring and failed-processing registration. The orchestration test uses explicit
test-only detector/privacy stubs. It does not prove a trained model's accuracy,
LinuxONE latency or end-to-end NNPA operation; run those against A's actual model
on the VM.

## Optional asynchronous heatmap

Gateway API:

* `POST /claims/{claim_id}/heatmap`, optional `{"evidence_id":"EV-..."}`; defaults
  to the first photo. AMBER/RED only, after the claim was scored.
* HTTP 202 returns `job_id`, status and a local `poll_url`.
* `GET /claims/{claim_id}/heatmap/{job_id}` returns queued/running/succeeded/failed.

B's internal counterparts are authenticated `POST /evidence/{id}/heatmap` with
`{"claim_id":"CLM-..."}` and `GET /heatmaps/{job_id}`. Evidence must have been
associated with that claim by successful scoring. No trained model means HTTP
503; no synthetic heatmap substitutes for unavailable inference.

The worker decrypts the original in memory, applies the same orientation/RGB/
short640 resize, and scores sixteen 224×224 windows in one 4×4 row-major batch.
Positions cover the available top-left ranges evenly; windows can overlap.
Completed `result` contains `grid_shape: [4,4]`, `patch_scores` (four rows of four
floats), all sixteen `crop_boxes` in row-major order, `resized_size` (width,
height), `model_version`, `timings_ms.total` and
`score_scope: "advisory_patch_signal_not_validated_edit_localisation"`.

Patch scores use the trained detector's logit transform, but per-patch
localisation has not been independently calibrated or validated as an edit mask.
They never change a claim's band. Batch-16 compatibility and the five-second
target still require validation against A's actual artifact. `/health` exposes
`heatmap.batch16_validated`, initially false, and sets it only after actual
successful grid inference.

The single background worker and at most 32 cached jobs bound memory use.
Completed jobs may be evicted; GET then returns 404. Jobs are in memory and are
lost on restart. Heatmaps share the detector lock/CPU with normal scoring, so
measure their resource contention before including them in a latency claim.
