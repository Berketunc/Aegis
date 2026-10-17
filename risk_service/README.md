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
  "preprocessing_version": "rgb-short640-bicubic-centre-grid25-75-v1",
  "phash_version": "phash64-dct32-median64-v1",
  "crop_boxes": [[528,208,752,432],[264,104,488,328],[792,312,1016,536]],
  "timings_ms": {"upload_preprocess": 0.0}
}
```

The crop-box example is an oriented 800×400 photo resized to 1280×640. Upload
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
matches and `async: {eligible, status: "not_configured"}`. `duplicate_of` is
the matched **other claim ID** or null. Multiple photos use their maximum
calibrated AI score; metadata flags are combined across photos. There is no
Granite job queue yet; eligibility is reported honestly without claiming a job
was queued. C2PA is only an unverified marker-presence check, never signature
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

## Exact preprocessing handoff to A

Confirm this before A exports a model; the plan leaves the two grid positions
unspecified. The implemented version is:

1. Apply EXIF orientation, convert to RGB.
2. Resize the shortest side to 640 using Pillow bicubic, rounding each dimension.
3. Produce three 224×224 crops in order: centre, then positions at 25% and 75%
   of each available top-left coordinate range. For fraction `f`,
   `left = round((width - 224) * f)`, `top = round((height - 224) * f)`.
4. Store a non-pickle NumPy array `uint8[3,3,224,224]` in RGB CHW order.
5. At inference divide by 255 and apply the manifest's RGB mean/std. Average
   the three logits first, then apply temperature scaling and sigmoid.

Original files are AES-256-GCM encrypted with a fresh 12-byte nonce, with the
SHA-256 bound as authenticated associated data. Crop arrays and the SQLite index
are ordinary local files in restricted directories; they remain sensitive and
must stay on the VM. Evidence filenames use full SHA-256 values; there is no
unauthenticated original-download endpoint.

## Trained model manifest from A

Example shape only: the temperature, model name and artifact must come from A's
actual validation/export, and thresholds must be calibrated on LinuxONE scores.
These example values are not a trained detector or an approved threshold set.

```json
{
  "model_version": "det-r18-v2",
  "preprocessing_version": "rgb-short640-bicubic-centre-grid25-75-v1",
  "format": "torchscript",
  "artifact": "detector.ts",
  "sha256": "<optional actual artifact digest; remove field if not supplied>",
  "output_kind": "binary_logit",
  "mean": [0.485, 0.456, 0.406],
  "std": [0.229, 0.224, 0.225],
  "temperature": 1.0,
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
{"thresholds_version": "t-1", "tau_amber": 0.5, "tau_red": 0.9}
```

Replace both illustrative numeric values with A's fitted LinuxONE thresholds.
The same file/version must be used by C's gateway. Model/config changes require
a service restart.

## C's privacy adapter

Provide a callable such as `privacy_adapter:process`:

```python
def process(*, description: str, context: dict) -> dict:
    # Implement C's real regex/gazetteer/HMAC rules and egress allowlist/log here.
    # Keep the HMAC key and token map on the VM. Do not return raw identifiers.
    return {
        "redacted_record": redacted_record,
        "egress_payload_id": persisted_egress_payload_id,
    }
```

The names in this example are outputs C must implement; it is not a runnable
adapter. The service refuses scoring if no adapter is configured, import fails,
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
