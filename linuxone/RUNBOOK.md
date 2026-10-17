# LinuxONE runbook: what is still to run (Person B and C)

Everything below is scripted. Run it on the VM, in this order, from the
repository directory (`~/aegis-person-b` or a fresh clone). Each step says which
file it produces; commit those files, because the VM has no backups. A's files are now in the repository, so every step can run.

Current state: risk, gateway and privacy run on LinuxONE and the degraded
AMBER path is verified (`eval/gateway_smoke.json`). Granite has never run, the
fail-safe and latency evidence have not been saved, and no screenshots exist.

## 0. Refresh the code

```bash
git fetch && git checkout codex/person-b-platform && git pull
bash linuxone/start_services.sh --with-gateway          # rebuilds images, keeps .runtime keys
bash linuxone/service_status.sh                          # expect upload/privacy ready, model unready
```

## 0b. Install A's detector and thresholds (needs A's files)

`best.pt`, `temperature.json`, `export.json` and `model.onnx` are committed under
`models/det-r18-v4/` (A force-added them; `.gitignore` otherwise excludes weights).
The two parity sets are not in Git: copy `parity_val.npz` and `parity_test.npz`
into `models/det-r18-v4/` on the VM with `scp` before the parity step. Then:

```bash
python3 linuxone/install_model.py --run det-r18-v4 --weights models/det-r18-v4/best.pt \
    --temperature models/det-r18-v4/temperature.json --export models/det-r18-v4/export.json \
    --thresholds eval/thresholds_t-0.json
bash linuxone/start_services.sh --with-gateway      # model loads once at startup
bash linuxone/service_status.sh                      # expect ready: true, model.version det-r18-v4
```

Parity check (plan 7.7), then A fits `t-1` on these LinuxONE scores:

```bash
mkdir -p eval/parity
for split in val test; do
  sudo docker exec -i aegis-risk python3 - /models/parity_$split.npz /tmp/$split.npy < linuxone/parity_logits.py
  sudo docker cp aegis-risk:/tmp/$split.npy eval/parity/linuxone_${split}_logits_cpu.npy
done
# A: python train/fit_thresholds_linuxone.py --run det-r18-v4 --version t-1 --build cpu \
#        --val-logits eval/parity/linuxone_val_logits_cpu.npy --test-logits eval/parity/linuxone_test_logits_cpu.npy
# then re-run install_model.py with --thresholds eval/thresholds_t-1.json and restart.
```

Repeat with `AEGIS_MODEL_DEVICE=nnpa` in `.runtime/services.env` for the NNPA build
(`--build nnpa`). Produces `eval/parity_t-1.md` and `eval/thresholds_t-1.json`.

## 1. Fail-safe evidence (C, plan 12.1 step 6)

Runs an isolated real gateway with native COBOL against a controlled risk
fixture: healthy, risk service stopped, risk service slower than 300 ms, recovered.

```bash
sudo docker exec -i aegis-gateway python3 - --output /tmp/failure_smoke.json < linuxone/smoke_failures.py \
  && sudo docker cp aegis-gateway:/tmp/failure_smoke.json eval/failure_smoke.json
```

Produces `eval/failure_smoke.json`. For the live demo take, the equivalent on
the running stack is `sudo docker stop aegis-risk`, submit a claim, expect AMBER
`RISK_SERVICE_UNAVAILABLE`, then `sudo docker start aegis-risk`.

## 2. Granite on LinuxONE (B runs, C reviews)

```bash
python3 linuxone/provision_granite.py --download          # ~784 MiB pinned BE GGUF + licence -> .runtime/models
bash linuxone/start_services.sh --with-gateway --with-granite
bash linuxone/service_status.sh                           # aegis-granite must report ready
python3 linuxone/smoke_granite.py                         # real inference, injection and PII cases
```

Produces `eval/granite_smoke.json` with tokens per second, elapsed time and
container RSS. Also capture `sudo docker logs aegis-granite | head -40`
(llama.cpp log naming the model file) for the "Granite running locally" screenshot.
If a note or PII case fails validation, tune `llm/prompts/*.txt`, not the validators.

## 3. Latency and concurrency (B, plan 11.3)

Before A's model this records the degraded path only and the report says so.

```bash
python3 linuxone/benchmark_gateway.py --synthetic --configuration pre-model-degraded
```

**After A** installs `manifest.json`, the artifact and `thresholds.json` in
`.runtime/models` and the services are restarted with full readiness:

```bash
python3 linuxone/benchmark_gateway.py --image demo_photos/real.jpg --image demo_photos/fake.jpg \
  --configuration torch-cpu --requests 200 --concurrency 1 4 8
# then set AEGIS_MODEL_DEVICE=nnpa in .runtime/services.env, restart, and repeat with --configuration torch-nnpa
```

Produces `eval/gateway_benchmark_<label>.json`, `eval/latency.md` and
`eval/latency.svg`. Run `sudo docker stats --no-stream` while the 8-client run is
in progress for the scalability screenshot.

## 4. Evidence pack (B, plan 12.3)

```bash
bash linuxone/collect_evidence.sh                         # -> evidence/platform-<timestamp>/
```

Captures `uname -m`, `lscpu`, `free`, `df`, `ss -lnt`, `iptables -L`, `docker ps`,
`docker stats`, each service's `/health`, journal and egress metadata, and the
encrypted-evidence summary. Screenshots still have to be taken by hand:

- Community Cloud portal showing the instance
- terminal with `uname -m` and `lscpu`
- `docker ps` and `docker stats` under the 8-client load
- `cobc -x` compile and `echo "09100 08500 06000 0 0 1 0" | txn/CLMIPOL` inside the gateway image
- `GET /journal` and `GET /claims/{id}/views` as adjuster vs authorised in the console
- Granite container log with the model filename
- `sudo iptables -L` showing only SSH
- both lab completion pages

## 5. zDLC retry (B, optional, 30 minutes maximum)

```bash
docker logout icr.io && bash zdlc/compile.sh <model.onnx> <stem>
```

See `zdlc/README.md`. If it still fails, the submission says "PyTorch with the
NNPA extension" everywhere and shows `eval/stock_resnet18.md`.

## 6. Back up and push

```bash
bash linuxone/backup_runtime.sh                           # private tarball with keys, evidence, databases
git add eval evidence zdlc/op_stats && git commit -m "Record LinuxONE evidence" && git push
```

Copy the backup tarball off the VM with `scp`; it contains the evidence
encryption key and must not be committed.
