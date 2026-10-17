# Presenter's notes: every number, what it means, and where it comes from

Use this when a judge points at a number. Each entry says what was measured, on what, and the file that records it. Numbers marked **measured on the VM** came from the LinuxONE Community Cloud machine on 17 October 2026; numbers marked **Modal** came from A's evaluation on the held-out test split.

## The machine

| Value | Meaning | Source |
|---|---|---|
| `s390x`, machine type **9175** | IBM z17, the current generation, with the Telum II processor and its on-chip AI accelerator (NNPA) | `uname -m`, `lscpu` on the VM |
| **3 vCPUs**, 7.8 GiB RAM | Size of the Community Cloud VM. Everything (gateway, risk service, Granite) shares these three cores | `lscpu`, `docker stats` |
| Big Endian | Why the Granite GGUF had to be a big-endian conversion and why weights are checked by SHA-256 | `lscpu`, `llm/model-source.json` |
| Firewall: ACCEPT lo, ACCEPT tcp 22, ACCEPT icmp, ACCEPT established, REJECT all | Only SSH is reachable from outside. The console is reached through an SSH tunnel | `sudo iptables -L INPUT -n` |
| `127.0.0.1:8080->8080/tcp` on the gateway only | The gateway listens on the VM's loopback. The risk and Granite containers publish no ports; the `3000/5000/8888/38888` entries are declarations inherited from the event's base image, not open ports | `docker ps` |
| Networks `aegis-internal` (internal) and `aegis-edge` | Risk and Granite live on an internal Docker network with no route out; the gateway sits on both so it can publish loopback 8080 and still reach the others by name | `docker network ls`, `linuxone/start_services.sh` |

## The decision rule

| Value | Meaning | Source |
|---|---|---|
| **τ red = 0.7539**, **τ amber = 0.5801** (version `t-0`) | Score thresholds. τ red is the score that only 1% of genuine validation photos exceed; τ amber the score only 5% exceed. They are set by false-positive caps on honest photos, not by how many fakes they catch | `eval/thresholds_t-0.json`, fitted by A on 638 real validation photos, Modal FP32 |
| `t-0` versus `t-1` | `t-0` is fitted on Modal scores. `t-1` is the same fit on scores produced by the deployed model on LinuxONE, because the accelerator computes in 16-bit and shifts scores slightly. `t-1` needs the parity run (`linuxone/parity_logits.py`) | `train/HANDOFF.md`, `linuxone/RUNBOOK.md` step 0b |
| Score 0.0328 on CPU versus **0.0312** on NNPA, same image | The 16-bit accelerator shift in practice: about 0.002 on a low score. This is why thresholds are refitted on LinuxONE scores | `linuxone/smoke_upload.py` runs before and after switching the device |
| **300 ms** deadline | The gateway's hard limit on the risk-service call. Past it the claim becomes AMBER with `RISK_SERVICE_UNAVAILABLE`, never GREEN | `txn/gateway/config.py` `RISK_TIMEOUT_MS`, `eval/failure_smoke.json` |
| **480 px** | Low-resolution limit on the photo's native short side. Below it the AI score alone can give AMBER but not RED, because the detector's false-positive rate is far higher on small photos | `txn/gateway/config.py` `LOW_RES_SHORT_SIDE`, `txn/CLMIPOL.cbl` column 25 |
| `09000 07539 05801 0 0 1 0` → `RED AI_SCORE_ABOVE_RED` | The COBOL program's fixed-width input: score 0.9000, τ red 0.7539, τ amber 0.5801, duplicate 0, software tag 0, risk OK 1, low-res 0. Scores and thresholds travel as integers ×10000, so COBOL and Python compare exactly the same values | `txn/CLMIPOL.cbl` header comment, `txn/gateway/policy.py` `encode()` |
| **25** truth-table cases | Inputs run against both the COBOL binary and its Python twin; outputs must be identical. 23 of them ran natively on LinuxONE in the gateway image | `txn/tests/test_policy.py`, `eval/native_gateway_tests.json` |
| **67** native tests, **180** local tests | Gateway and policy suites passed inside the s390x gateway image; the full suite on a laptop with GnuCOBOL installed | `eval/native_gateway_tests.json`, `pytest -q` |

## Latency, measured on the VM

| Value | Meaning | Source |
|---|---|---|
| CPU **104.6 / 109.9 ms** (p50 / p95), NNPA **18.6 / 20.4 ms** | Stock ResNet-18 on three 224 px crops, 100 timed runs each, three CPU threads. The NNPA run is 5.6× faster at the median. This shows what the accelerator does to the same network; it is not the trained detector | `eval/stock_resnet18.md` |
| Trained detector, CPU, three threads: **117 to 126 ms** warm, later **170 to 320 ms** on a busier evening | Inference for one claim's three crops. The second range is why the CPU path kept hitting the 300 ms deadline | `linuxone/smoke_upload.py` scoring block |
| Trained detector, **NNPA: 27 to 44 ms** | Same model moved to the accelerator with `AEGIS_MODEL_DEVICE=nnpa`. Whole risk-service call 30 to 47 ms | `linuxone/smoke_upload.py` scoring block, `eval/risk_service_health.json` shows `device: nnpa` |
| **121 ms** end to end (CPU), **77 to 102 ms** (NNPA) | One claim through the gateway: upload lookup, privacy rules, detector, duplicate check, COBOL rule, journal and commit. Measured by the gateway smoke and the demo-claim recorder | `eval/gateway_smoke_scored.json`, `eval/demo_claims.json` |
| Upload preprocessing **45 to 139 ms** | Decode, orientation, resize, three crops, SHA-256, perceptual hash, metadata flags, AES-GCM encryption. Done once at upload, off the decision path | `eval/upload_smoke.json`, smoke_upload runs |
| 1 client: **p50 48 ms, p95 75 ms, p99 113 ms, 19 claims/s** | 200 scored claims through the gateway over HTTP on NNPA, one at a time | `eval/gateway_benchmark_torch-nnpa.json` (first run) |
| 4 clients: **p50 144 ms, p95 212 ms, p99 301 ms, 26 claims/s** | Same with four concurrent clients. One detector lock, so requests queue; p95 is just over the plan's 200 ms target | same |
| 8 clients: 191 of 200 timed out at ~311 ms | **Not a decision-path number.** During that run Granite was at 195% CPU writing notes for every RED claim the benchmark produced, on the same three cores. The sweep is rerun with Granite stopped; use the rerun's row | `eval/latency.md` after the rerun |
| Fail-safe: healthy GREEN 10 ms, refused AMBER 8 ms, slow AMBER **310 ms**, recovered GREEN 7 ms | Real gateway with native COBOL against a controlled risk fixture that is stopped, then made slow, then restored. The slow case shows the 300 ms deadline cutting in | `eval/failure_smoke.json` |

## The detector and its data (Modal, held-out test split)

| Value | Meaning | Source |
|---|---|---|
| **10,363** images; 7,018 train / 1,507 val / 1,838 test | Dataset size and split. Split by source photo: a real photo and all fakes made from it stay in the same split | `data/dataset_card.md` |
| **4,261 real** (CarDD 4,000 + Commons), **6,102 fake** | Real photos predate 2022 so none can be AI-made. Fakes were all generated by the team, so labels are free and exact | `data/dataset_card.md` |
| Damage share **94.7% real vs 94.2% fake**, gap **0.5 points** | If fakes showed damage more often than real photos, the model would learn "damage = fake" and flag honest claimants. The gap is kept near zero on purpose | `data/dataset_card.md` |
| Shortcut AUROC **0.50 to 0.55** | A logistic regression on file size, dimensions or colour alone cannot tell the classes apart (0.5 is a coin flip) | `data/dataset_card.md` |
| Temperature **T = 0.676** | Calibration constant: `score = sigmoid(mean(3 crop logits) / T)`. Fitted on validation so scores behave like probabilities | `models/det-r18-v4/temperature.json` |
| In-distribution: AUROC **0.936**, TPR **0.674** at 1% FPR | Overall separation, and the share of fakes caught when at most 1% of real photos are flagged | `eval/results.md` |
| FLUX.1-schnell: AUROC **0.944**, TPR **0.502** at 1% FPR | A generator never used in training. Half of its fakes are caught at the 1% cap. Reported because it is the honest generalisation number | `eval/results.md` |
| Real vs its own regenerated copy: AUROC **0.985** | Same content on both sides, so the model can only be reading generation traces, not scene content | `eval/results.md` |
| Damage inflation: AUROC **0.622**, TPR 0.041 | Small inpainted edits are the weak spot. The model sees them when a crop lands on the edit (AUROC 0.839), so the limit is crop coverage | `eval/results.md` |
| Real damage photos: **1.2%** reach RED, **5.0%** reach AMBER | The number that decides whether the product is usable: how often genuine damage photos are flagged | `eval/results.md` |
| Half-size photos: **16.8%** of real ones exceed τ red (first model: 89%) | The failure mode that led to the no-upscale preprocessing and the 480 px rule | `eval/results.md`, `eval/charts/honest_flagged.png` |
| RED rate among real photos **0.4% to 2.8%** across subgroups | Fairness check: JPEG quality, size, compressibility, source and damaged/undamaged groups all stay within this band. Commons (n = 41) and undamaged (n = 36) groups are small | `eval/runs/det-r18-v4_mean_audit.md` |
| Thresholds `t-demo` (0.30 / 0.55), if anyone asks | A hand-picked, more sensitive setting kept as a labelled file. On the parity samples it sends 19 to 20% of genuine photos to review and 2 to 9% to an investigator. Not used | `eval/thresholds_t-demo.json` |

## The two demo photos

| Value | Meaning | Source |
|---|---|---|
| `cardd_001882.jpg`: 703×527, 19,515 bytes, score **0.319**, GREEN | A real CarDD photo of a dented door, from the test split. Scored identically on the Mac and on the VM | `data/dataset_manifest.csv`, `eval/demo_claims.json` |
| `inflate_cardd_001882.jpg`: 883×662, 74,830 bytes, score ≈ **0.83**, RED | The same photo after AI inpainting added a hole in the door. `AI_SCORE_ABOVE_RED` | `data/demo_assets.md`, `eval/demo_claims.json` |
| `INFO_EXIF_MISSING` on both | Informational only: dataset images have EXIF stripped. Never changes the band | reason table in `txn/README.md` |
| `DUPLICATE_PHOTO` on a re-submission | Perceptual-hash match (Hamming distance ≤ 8 of 64 bits) against a photo on a different claim. Catches the "same photo, different plate" pattern. Always RED | `risk_service/dup.py`, `eval/gateway_smoke_scored.json` |

## Granite

| Value | Meaning | Source |
|---|---|---|
| Granite 3.0 1B-A400M Instruct, **Q4_K_M**, **821,845,024 bytes**, SHA-256 `937a8c48…` | The local LLM: a big-endian GGUF conversion listed in the s390x verified-models collection, pinned by hash, Apache-2.0 | `llm/model-source.json` |
| threads 2, context 1024, llama-cpp-python 0.3.36 | Runtime configuration inside the Granite container | `aegis-granite` health |
| queue depth, in flight, **tokens per second** and RSS | Reported per job by the service and summarised by the smoke. Fill from `eval/granite_smoke.json` once the run completes | `linuxone/smoke_granite.py` |
| 195% CPU during the benchmark | Two threads writing adjuster notes for RED claims. It is async and cannot change a band, but on three shared cores it competes with the detector, which is why the sweep is run with it stopped | `docker stats` |

## What to say if asked "is anything simulated?"

CICS is emulated by a FastAPI gateway that follows the CICS patterns (idempotency key, one-transaction syncpoint, append-only journal). The COBOL rule is real and runs on LinuxONE. The detector runs on LinuxONE with PyTorch for s390x on the NNPA accelerator; compiling it with zDLC is scripted in `zdlc/` but was not achieved in the event window because the registry pull failed. Granite is real and local. Personas are fictional. Thresholds are `t-0` from Modal scores until the parity run produces `t-1`.
