# Person B: LinuxONE runtime

These files implement the platform side of the plan. Docker, PyTorch CPU and a
small NNPA linear-layer inference have been demonstrated on the team's LinuxONE
VM. The stock ResNet benchmark completed on that VM on 17 October 2026.
The risk image also built successfully after installing the native OpenSSL
build dependencies (UBI 10 packages, OpenSSL 3.5.8). After startup, the user
verified the container was up and Uvicorn reported startup complete. The saved
[`health response`](../eval/risk_service_health.json) confirms encrypted storage
initialization and `upload_ready: true`; an actual upload remains to be verified
on the VM. Full scoring remains unready while A/C artifacts are absent.
No stock classifier is used as a claim-photo detector.

Verified results from the user-supplied VM output are saved in
[`eval/stock_resnet18.json`](../eval/stock_resnet18.json) and explained in
[`eval/stock_resnet18.md`](../eval/stock_resnet18.md). For 100 measured runs of
three crops, CPU p50/p95 were 104.60/109.93 ms and the NNPA device run was
18.63/20.37 ms. This is approximately 5.62× faster at the median for this stock
model; it does not establish final detector accuracy or end-to-end latency.

## Stock-model benchmark

From this repository on the LinuxONE VM:

```bash
bash linuxone/run_benchmark.sh
```

This runs the public event image already downloaded by the team. It downloads
the official torchvision v0.10.0 ResNet definition and ImageNet weights,
preserves its BSD license, and adapts one relative import so a torchvision
binary installation is unnecessary. Weights are checked against the official
filename's SHA-256 prefix. The model is loaded strictly with `weights_only=True`.

The default is 10 warmups and 100 measured runs per backend, using the same
`[3,3,224,224]` synthetic input and weights. CPU-to-NNPA input transfer and
NNPA-to-CPU output transfer are included. Model loading, download time,
preprocessing and warmup are excluded. Debug logging is disabled during timing.

Results are written to `eval/stock_resnet18.json`. CPU results survive an NNPA
extension failure. Use `--runs 200` for a longer sample or `--cpu-only` to test
only the fallback. These are stock ImageNet model timings, **not** fraud
accuracy, end-to-end claim latency, or zDLC compile/op-stats evidence. Repeat
benchmarks after deploying A's final detector.

## Internal risk service

```bash
bash linuxone/start_services.sh
bash linuxone/service_status.sh
```

The launcher builds the risk image from the public event image, creates an
isolated `aegis-internal` Docker network, and starts `aegis-risk:8000`. It
publishes no host ports. C's gateway must join that network and call
`http://aegis-risk:8000`. C should publish the gateway on loopback only and use
the SSH tunnel described in the plan for UI access.

The first launch creates `.runtime/services.env` with stable randomly generated
credentials and a 32-byte evidence encryption key. The key is never printed.
Keep this file and `.runtime/data` together in private backups; losing or
replacing the evidence key makes previously encrypted originals unreadable.
These files and model weights are excluded from Git and Docker build contexts.

Before the trained model, thresholds and privacy adapter are supplied, uploads
can work but `/risk/score` returns HTTP 503. `/health` reports the missing
dependencies. The gateway must translate a failed or timed-out score call into
AMBER. See `risk_service/README.md` for the API and model manifest contract.
The status script waits up to 30 seconds for the HTTP listener to start, then
reports readiness. An HTTP response with `ready: false` is different from a
connection failure; the status command returns nonzero for either case.

To verify uploads using a synthetic image, from the repository on the VM:

```bash
sudo docker exec -i aegis-risk python3 - < linuxone/smoke_upload.py
```

This checks the actual authenticated HTTP upload, SHA-256, AES-GCM round-trip
and cached three-crop array without printing credentials or image bytes. It
leaves one synthetic evidence object in storage, reused on repeat runs. One
upload does not establish the preprocessing p95 or detector accuracy.

## Required handoffs

| Owner | Required files / agreement |
|---|---|
| A | Trained PyTorch model definition + strict state dictionary, or a compatible TorchScript artifact, alongside ONNX. Explicit RGB normalization, fake-class ordering, temperature and crop agreement. Put the manifest and artifact in `.runtime/models`. |
| A + B | Validation/test crops and labels, LinuxONE parity scores and final calibration. Supply versioned `.runtime/models/thresholds.json`; share the same config with C's gateway. |
| C | Privacy adapter at `.runtime/integrations/privacy_adapter.py`, callable `process(description=..., context=...)`, returning `redacted_record` and a real `egress_payload_id`. C owns redaction, pseudonymisation and the allowlisted egress log. |
| C | Gateway forwards multipart `file` to `/evidence` and uses `X-Risk-Secret` for internal calls. It sends claim IDs, evidence IDs and privacy context, and owns idempotency, decision bands, journal and timeout behaviour. |
| C + D | Agree polling/display for optional Granite results. The risk API reports async eligibility but does not enqueue Granite jobs yet; the gateway can enqueue eligible AMBER/RED claims after its decision. |
| C / Lab 2 | A compatible local big-endian Granite GGUF at `.runtime/models/granite.gguf` and reviewed `.runtime/prompts/pii.txt` / `note.txt`. |
| D | Use the measured JSON, real runtime screenshots, fallback disclosure and run instructions in the README/video. |

Preprocessing is versioned as `rgb-short640-bicubic-centre-grid25-75-v1`:
EXIF orientation, RGB, shortest side 640 with bicubic resize, then 224-pixel
crops at 50%, 25% and 75% of each available axis. A must approve or replace this
exact convention before generating final evaluation crops or training exports.

## Optional Granite service

After C's reviewed prompt files and a compatible GGUF are available:

```bash
bash linuxone/start_services.sh --with-granite
bash linuxone/service_status.sh
```

The service is internal at `http://aegis-granite:8001`. `/pii` and `/note` queue
bounded, sequential jobs, and `/jobs/{id}` returns their status/results. Missing
model/prompt files keep the service unready. No external LLM calls are made.
See `llm/README.md` for the detailed contract. It uses the same secret value and
header as the risk service, while its environment key is `RISK_SHARED_SECRET`.

## Current limits

* IBM `icr.io` authentication remains unresolved; this implementation uses the
  plan's PyTorch fallback and does not claim zDLC compilation.
* Model accuracy, parity, thresholds, final latency and concurrency results
  remain unmeasured until the real detector and gateway are integrated.
* Heatmaps and automatic risk-to-Granite queue integration are not implemented.
* The real CICS production design is outside this platform implementation.

## Sources

* Event image and lab: https://github.com/linuxone-community-cloud/jupyter-lab-ml
* Event packages: https://github.com/linuxone-community-cloud/jupyter-lab-ml/blob/main/packages.txt
* IBM PyTorch NNPA usage: https://github.com/IBM/ibmz-accelerated-for-pytorch
* Official model source: https://github.com/pytorch/vision/blob/v0.10.0/torchvision/models/resnet.py
* Model source license: https://github.com/pytorch/vision/blob/v0.10.0/LICENSE
* Official weights: https://download.pytorch.org/models/resnet18-f37072fd.pth
