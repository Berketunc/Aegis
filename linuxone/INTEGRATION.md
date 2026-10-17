# Person B + C integration

C's source branch `PERSON_C` at `01e6430bae65583edd166031ba7aae2b1a98a34e`
has been merged into the local Person B branch. The integration uses C's real
gateway, shared privacy rules and COBOL source. A's trained detector and
calibrated thresholds have not been supplied. No stock classifier or example
thresholds are used as a replacement.

## Deploy the updated package

Copy `.deliverables/aegis-person-b.tar.gz` to the VM as before, then in the
existing SSH terminal:

```bash
cd ~/aegis-person-b
tar -xzf ~/aegis-person-b.tar.gz -C ~/aegis-person-b
bash linuxone/start_services.sh --with-gateway
sudo docker exec -i aegis-gateway python3 - --expect-degraded < linuxone/smoke_gateway.py
```

The package excludes `.runtime`, so extracting it preserves the existing
evidence key and uploaded data. Runtime migration preserves the saved evidence
and authentication keys and adds a stable HMAC key for C's pseudonyms. A missing
HMAC key with existing gateway data is an error requiring recovery of the
original key. Neither gateway nor Granite receives the evidence encryption key.

The launcher builds all requested images before replacing its owned containers.
The gateway image compiles `CLMIPOL.cbl` natively with GnuCOBOL during the build.
The gateway alone publishes `127.0.0.1:8080`; the risk and Granite services remain
on the internal Docker network. Gateway data persists in `.runtime/gateway`.

The smoke script checks actual COBOL GREEN/RED/fail-safe examples, then uploads
a synthetic PNG through the gateway to the real risk service. It submits a
fictional claim, expects AMBER from the missing model/thresholds, checks that
replay uses the same claim, reads the journal and verifies the pseudonymised
view. It leaves one synthetic evidence object and one fictional claim per run.
It waits for startup, so an immediate connection refusal is retried.

The user subsequently ran this deployment on LinuxONE. The risk image
`81aca95619cb` and gateway image `8499ca369faa` built successfully. Debian's
s390x GnuCOBOL 3.2 compiled `CLMIPOL.cbl`; its build-time GREEN example and
the smoke script's GREEN/RED/AMBER examples passed. The
[`gateway smoke result`](../eval/gateway_smoke.json) confirms real upload,
replay, journal and pseudonymised-view checks for `CLM-000001`.
The claim returned AMBER with `THRESHOLDS_UNAVAILABLE` in one measured round
trip of 10.446 ms. No trained detector ran, and no percentile was established.

Local tests exercise both real ASGI applications, durable egress preparation,
timeouts, threshold mismatches, replay and concurrent duplicate submissions.
Local COBOL tests still skip because the Mac has no `cobc`. The user then ran
the selected gateway/policy suites inside the native VM image: **67 passed,
zero skipped, one deprecation warning, 2.57 seconds**. This includes all 23
COBOL cases previously skipped locally. The saved
[`native test result`](../eval/native_gateway_tests.json) records that scope;
these tests use controlled risk fixtures and do not validate A's model.

Integration validation on 17 October: **114 tests passed, 23 COBOL tests
skipped**. Shell syntax, Python compilation and patch whitespace checks passed.

The native policy/gateway verification used this command, from the VM repository
directory. It need not be repeated until policy/gateway code changes:

```bash
sudo docker run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$PWD/txn/tests,target=/app/txn/tests,readonly" \
  --entrypoint python3 aegis-gateway:person-b \
  -m pytest /app/txn/tests/test_policy.py /app/txn/tests/test_gateway.py -q -p no:cacheprovider
```

These isolated tests use controlled risk fixtures and real GnuCOBOL. They do not
change the running gateway's database or pretend to validate A's detector.

For status, run `bash linuxone/service_status.sh` separately. Until A's files
arrive, the expected state is upload ready and privacy ready, with detector and
overall scoring unready. The status command returns nonzero for that deliberate
unready state. The gateway may report thresholds unavailable and still commit
AMBER. That does not complete G4's successful detector path.

## Interface decisions

* Internal authentication uses `X-Risk-Secret`. The gateway also sends C's
  original `X-Shared-Secret` header for compatibility with its development stub.
* Evidence IDs are `EV-` plus the full SHA-256 (67 characters); the gateway now
  accepts them and limits a claim to the risk service's eight photos.
* Both services accept `version` or `thresholds_version` in the same mounted
  threshold file. Conflicting aliases or differing response versions fail safe.
* The gateway applies C's privacy rules to structured identity and description
  before scoring. B reuses those same rules, receives minimal context and logs
  allowlisted payload preparation. These PREPARED records are local audit rows,
  not evidence that a network egress occurred. C separately commits its final
  decision/egress record in the same SQLite transaction as the journal.
* A per-key submission lock prevents concurrent duplicate requests from scoring
  under different claim IDs. The deployment uses one gateway worker.
* Fresh POST responses include commit time in `timings_ms.total`. Stored
  decisions/journal use the explicitly named `precommit_total`; replays add
  their own `replay_timings_ms`. Final latency must be measured externally too.

## Required model handoff from A

Put the trained artifact and `manifest.json` in `.runtime/models`. Supply a
compatible TorchScript artifact or trusted factory plus strict PyTorch state
dictionary alongside the planned ONNX export. The manifest contract and exact
crop/normalisation/calibration convention are in `risk_service/README.md`.
Do not substitute ImageNet weights for the damage-photo detector.

With A, verify LinuxONE CPU/NNPA scores on the real validation/test crops, then
write versioned `.runtime/models/thresholds.json` shared by both services. C's
COBOL/Python policy rounds scores and thresholds to four decimal places;
threshold selection and the reported FPR must include that actual rule.

After installing the model, rebuild/restart, require full risk readiness and run
the successful claim path. Then measure the final 200-request CPU/NNPA and
1/4/8-client configurations. Current stock timings and the single 139.339 ms
upload are early platform evidence, not final detector or claim latency.

## Optional local Granite

After the compatible GGUF is at `.runtime/models/granite.gguf`:

```bash
bash linuxone/start_services.sh --with-gateway --with-granite
```

C's prompts now match B's strict typed JSON job contract. The gateway submits
authenticated jobs, polls a fixed local path and runs PII review before asking
for a note. Failed PII review stops the note. Any newly found identifier updates
the pseudonymised claim and current egress view, with a new journal event.
Committed decision bands never change. B's earlier PREPARED audit rows remain
historical records. The first synchronous rules pass can miss identifiers;
the optional second pass runs after the decision and is not a guarantee that
no identifier was ever stored. No external AI calls are configured.

Actual Granite inference, tokens/second and memory remain unmeasured until
the model is available. Heatmaps remain unimplemented.
