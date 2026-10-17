# Stock ResNet-18: LinuxONE benchmark

Completed 17 October 2026, 15:06:54 UTC (16:06:54 UK). The report was recovered
from the user's pasted VM terminal output; the agent did not execute the VM
run. Every reported percentile has been recomputed from the 100 raw samples
for each backend and matches the saved report.

| Backend | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | ---: | ---: | ---: |
| PyTorch CPU | 104.60 | 109.93 | 128.47 |
| PyTorch `torch_nnpa` | 18.63 | 20.37 | 20.87 |

The median ratio is 5.62× and the p95 ratio is 5.40× for this run. Both outputs
were finite, with shape `[3,1000]`. The maximum absolute CPU-versus-NNPA logit
difference was 0.0787854; no accuracy or parity acceptance threshold was tested.

Configuration: s390x, Python 3.12.15, PyTorch 2.11.0+cpu,
torch_nnpa 1.5.0+gite861108, three CPU threads, one interop thread,
`float32[3,3,224,224]`, seed 0, 10 warmups and 100 measured runs per backend.
Weights came from the official ImageNet ResNet-18 checkpoint; its full
SHA-256 is retained in the JSON provenance. Timings include input transfer,
forward pass and copying outputs back to CPU. They exclude image upload,
preprocessing, model loading and warmup.

The checkpoint emitted PyTorch's warning about the default byte order for
unmarked checkpoints on big-endian machines. Loading and both benchmark runs
completed. This warning was not a runtime failure.

## Plan status and limits

This supplies Person B's stock-model timing evidence using the working
PyTorch fallback. It is **not** the planned ONNX-to-zDLC compilation, `.so`
execution or per-operation NNPA statistics. The `nnpa:0` labels and timing
comparison do not establish that every operation ran on the accelerator.

This is the stock ImageNet classifier, not A's trained claim-photo detector.
Repeat inference parity/calibration and the 200-request/concurrency benchmarks
after A's model and C's gateway are integrated. The plan's CPU detector target
of 60 ms was not met by this stock CPU run; the NNPA stock run is below that
target, but final detector and total claim-path latency remain unmeasured.

G2 is not declared complete for the whole team: generation jobs and the gateway
skeleton have not been verified here. Lab completion and event registration
also remain unverified.

## Next Person B step

Start the internal risk container with `bash linuxone/start_services.sh`, then
inspect `bash linuxone/service_status.sh`. Until A's trained model and C's
privacy adapter plus versioned thresholds are present, the expected state is
`ready: false`, `upload_ready: true`, and `/risk/score` returns 503. This is an
upload/storage deployment check, not full claim scoring. The gateway must
translate unavailable or timed-out scoring into AMBER.

Keep the VM's original `eval/stock_resnet18.json` and capture a real terminal
screenshot for D's evidence package. No screenshot is fabricated by this report.
