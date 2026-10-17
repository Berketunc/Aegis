# LinuxONE runbook: what is still to run (Person B and C)

Everything below is scripted. Run it on the VM, in this order, from the
repository directory (`~/aegis-person-b` or a fresh clone). Each step says which
file it produces; commit those files, because the VM has no backups. Nothing
here needs A's model, except where marked **after A**.

Current state: risk, gateway and privacy run on LinuxONE and the degraded
AMBER path is verified (`eval/gateway_smoke.json`). Granite has never run, the
fail-safe and latency evidence have not been saved, and no screenshots exist.

## 0. Refresh the code

```bash
git fetch && git checkout codex/person-b-platform && git pull
bash linuxone/start_services.sh --with-gateway          # rebuilds images, keeps .runtime keys
bash linuxone/service_status.sh                          # expect upload/privacy ready, model unready
```

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
- `cobc -x` compile and `echo "09100 08500 06000 0 0 1" | txn/CLMIPOL` inside the gateway image
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
