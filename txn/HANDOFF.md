# Handoff: Person C (transaction layer, privacy, decision policy)

For: the agent deploying C's work on the LinuxONE VM and merging it with the rest of the team's code.
Background: `plan.md` §3 "Person C", §9, §10, §14, §16. Human-facing docs: `txn/README.md`.

**B/C integration update:** C's branch `PERSON_C` at `01e6430` is committed and
merged locally into Person B's branch. The sections below describe C's original
handoff snapshot. Current deployment and interface changes are documented in
[`linuxone/INTEGRATION.md`](../linuxone/INTEGRATION.md): canonical `X-Risk-Secret`,
full SHA evidence IDs, shared threshold aliases, C's built-in privacy bridge,
typed queued Granite requests and the `--with-gateway` launcher. The original
`docker-compose.dev.yml` remains a labelled stub development setup.

## 1. State on 17 Oct 2026

- All of C's code is written and **passes 91 tests locally on Windows** (Python 3.11 + GnuCOBOL 3.2 from MSYS2), including 19 COBOL-vs-Python parity cases and gateway tests that run on the real COBOL binary.
- A live smoke run (stub risk service + gateway as real uvicorn processes, COBOL engine) gave the expected bands at about 42–50 ms per claim on Windows. The fail-safe returned AMBER at about 350 ms.
- **Nothing is committed.** Files are untracked in the working tree: `txn/`, `privacy/`, `llm/prompts/`, `eval/rai.md`. Make a branch before committing (the repo is on `main`).
- **Not yet run:** anything on LinuxONE/s390x, `docker build`, the real risk service (B), real Granite (B), real thresholds (A).

## 2. File map (C owns these; don't move them without updating imports)

| Path | What it is |
|---|---|
| `txn/gateway/app.py` | FastAPI app factory `create_app(settings, transport)`; module-level `app` for uvicorn. All endpoints. |
| `txn/gateway/config.py` | `Settings` (environment variables) and `load_thresholds()`, which re-reads the JSON on every claim |
| `txn/gateway/db.py` | SQLite schema, append-only journal triggers, `commit_decision()` = SYNCPOINT, `reserve_claim_id()` |
| `txn/gateway/policy.py` | `encode()` fixed-width line, `evaluate_cobol()` (subprocess), `evaluate_python()` twin, `evaluate()` fail-safe |
| `txn/gateway/risk_client.py` | `/risk/score` call with an `asyncio.wait_for` hard deadline; pydantic validation of the §14 response |
| `txn/gateway/granite.py` | Post-commit job: PII second pass + adjuster note, output validation, prompt fencing |
| `txn/CLMIPOL.cbl` | The decision rule in COBOL (GnuCOBOL / Enterprise COBOL compatible, free format with `*>` comments) |
| `txn/stub_risk.py` | Fake risk service implementing §14; scores come from the filename; `POST /stub/mode` for fail/slow/garbage |
| `txn/thresholds.example.json` | Placeholder `t-0` (τ_red 0.85, τ_amber 0.60) |
| `txn/Dockerfile`, `txn/Dockerfile.stub`, `txn/docker-compose.dev.yml` | Containers; **build context = repo root** |
| `txn/tests/` | pytest; `conftest.py` puts the repo root on `sys.path`; COBOL tests skip if `cobc` isn't on PATH |
| `privacy/` | Stdlib-only package: `redact()`, `pseudonym()`, `contains_pii()`, `build_egress()`. Meant to be shared with B's risk service. |
| `llm/prompts/pii_second_pass.txt`, `adjuster_note.txt` | Granite prompts; placeholders filled with `str.replace`, not `.format` |
| `eval/rai.md` | Responsible-AI statement; ⟨…⟩ placeholders wait for A's numbers |
| `txn/README.md` | Run instructions, API, fail-safe table, production CICS design (§9.4) |

## 3. Contracts other components must meet

**Risk service (B)**: reached only on the Docker network.
- `POST /evidence`: multipart field **`file`**, header `X-Shared-Secret`. Returns `{"evidence_id": str, "sha256": str}`. The gateway has already checked size (≤ 15 MB) and type (JPEG/PNG magic bytes).
- `POST /risk/score`: header `X-Shared-Secret`, body `{"claim_id", "description", "evidence": [{"evidence_id"}]}`. The description is **already pseudonymised**. The response must match §14, and **`claim_id` must echo the request**, otherwise the gateway treats it as a failure → AMBER. `signals.ai_score` must be in [0, 1]. `signals.duplicate_of` = the earlier claim's ID, or null. `metadata.software_tag` = a string or null.
- The response must arrive within **300 ms** end to end, or the claim goes AMBER `RISK_SERVICE_UNAVAILABLE`.

**Granite service (B)**: `POST {GRANITE_URL}/pii` and `POST {GRANITE_URL}/note`, body `{"prompt": str, "max_tokens": int}`, response `{"text": str}`. The gateway calls it only for AMBER/RED claims, after commit, one job at a time, with a 60 s timeout (`GRANITE_TIMEOUT_S`). If `GRANITE_URL` is empty, Granite is skipped.

**Thresholds (A)**: JSON `{"version": "t-1", "tau_red": float, "tau_amber": float}` at `THRESHOLDS_PATH`, with 0 ≤ τ_amber ≤ τ_red ≤ 1. If it's unreadable → AMBER `THRESHOLDS_UNAVAILABLE`.

**UI (D)**:
- `POST /evidence` (multipart `file`).
- `POST /claims`: §14 body plus **`idempotency_key`** (UUID made when the form opens; the `Idempotency-Key` header also works).
- `GET /claims/{id}` returns the decision plus `adjuster_note.status`: `pending` | `ready` | `unavailable` | `not_requested`.
- `GET /claims/{id}/views` with header `X-Role: adjuster|authorised`.
- `GET /journal?claim_id=`.
- The gateway serves `UI_DIR` (default `<repo>/ui`) at `/` if the directory exists.

## 4. Deploy on the LinuxONE VM (Ubuntu 22.04, s390x, user `linux1`)

```bash
# 0. Get the code onto the VM (after it's committed and pushed from the main codespace)
git clone <repo> && cd <repo>

# 1. Native check first (fast feedback before Docker)
uname -m                                   # s390x
apt-cache policy gnucobol gnucobol3
sudo apt install -y gnucobol3 || sudo apt install -y gnucobol
cobc -x -o txn/CLMIPOL txn/CLMIPOL.cbl
echo "09100 08500 06000 0 0 1" | txn/CLMIPOL     # expect: RED AI_SCORE_ABOVE_RED
echo "garbage" | txn/CLMIPOL                     # expect: AMBER POLICY_INPUT_INVALID

python3 -m venv .venv && . .venv/bin/activate     # Ubuntu 22.04 ships Python 3.10; the code needs ≥ 3.10
pip install -r txn/requirements.txt
pytest txn/tests -q                               # expect 91 passed, 0 skipped (cobc is on PATH)

# 2. Container
docker compose -f txn/docker-compose.dev.yml up --build -d
docker compose -f txn/docker-compose.dev.yml ps
curl -s localhost:8080/health                     # {"policy_engine":"cobol", ...}

# 3. From the laptop: ssh -i key.pem -L 8080:localhost:8080 linux1@<IP>  →  http://localhost:8080
```

Set real secrets with environment variables (don't commit them): `RISK_SECRET` (same value in B's service), `HMAC_KEY` (long random string; changing it changes every pseudonym), `GRANITE_URL`.

## 5. Merging with the main codespace

1. **Check for collisions** with what other agents created: `risk_service/privacy.py`, a root `docker-compose.yml`, a root `.gitignore`, `ui/`. C only owns the paths in §2.
2. **Privacy is shared:** B's risk service should `from privacy import redact, pseudonym` rather than keep its own copy. B's Dockerfile then needs `COPY privacy privacy`, with build context = repo root.
3. **One compose file for the demo** (B owns it). Add the gateway service like this and remove `risk-stub`:
   ```yaml
   gateway:
     build: { context: ., dockerfile: txn/Dockerfile }
     environment:
       RISK_URL: http://risk-service:<port>
       RISK_SECRET: ${RISK_SECRET}
       HMAC_KEY: ${HMAC_KEY}
       THRESHOLDS_PATH: /config/thresholds.json
       GRANITE_URL: http://granite:<port>     # empty string disables Granite
     ports: ["127.0.0.1:8080:8080"]          # loopback only; the risk service and Granite publish no ports
     volumes: [clmi-data:/data, ./ui:/app/ui:ro, ./config:/config:ro]
   ```
4. **Thresholds file:** agree a path with A (e.g. `config/thresholds.json`) and mount it as above.
5. **Run `pytest txn/tests -q`** after the merge. The tests use the stub over in-process transports, so they don't need B's service running.

## 6. What's missing or still needed

**Must do before G4 / the demo**
- [ ] Commit C's files on a branch and merge into the main codespace (§5).
- [ ] Run §4 on the VM: native tests with `cobc`, then `docker build`. **Untested on s390x.**
- [ ] Integration test against **B's real risk service**: check the field names, the `claim_id` echo, `X-Shared-Secret`, multipart field `file`, and that the 300 ms deadline holds with the real detector. If B's response shape differs from §14, adapt `RiskResponse` in `txn/gateway/risk_client.py`; don't loosen the fail-safe.
- [ ] Replace `t-0` with A's `t-1` (§3).
- [ ] Demonstrate the fail-safe live: `docker stop <risk-service>` → a claim → AMBER `RISK_SERVICE_UNAVAILABLE`.

**Should do**
- [ ] Integration test with **real Granite**: check the response arrives as `{"text"}`, that the PII pass returns parseable JSON (`parse_pii` tolerates text around the JSON object), and that notes pass `validate_note` (≤ 3 lines, all starting with `- `, ≤ 600 chars, no PII). If Granite keeps failing validation, tune the prompts in `llm/prompts/`, not the validators.
- [ ] Measure gateway latency on the VM for `eval/latency.md` / §11.2: the rows "Gateway" and "COBOL rule + journal + commit". Every response contains `timings_ms` (gateway, risk, policy, total). The COBOL subprocess should cost a few ms on Linux; on Windows it was most of the ~40 ms.
- [ ] Evidence screenshots (§12.3): `cobc` compile + `echo … | ./CLMIPOL` on the VM; `GET /journal`; `GET /claims/{id}/views` as adjuster vs authorised; the egress payload; `docker ps`.
- [ ] Fill the ⟨…⟩ numbers in `eval/rai.md` from A's `eval/results.md`.
- [ ] Ask D to link `txn/README.md` from the root README (production CICS design + real-vs-emulated).

**Not built (by design or deferred)**
- Heatmap: the gateway has **no heatmap route**. If the UI needs it through the gateway, add a pass-through to B's endpoint.
- Claude fallback for Granite (plan §10.2): not implemented. If needed, add it in `granite._complete` and send only the pseudonymised prompt, labelled as a stand-in.
- The production CICS COBOL program (COULD tier) isn't written; only the design is in `txn/README.md`.
- Pending Granite jobs live in memory (FastAPI `BackgroundTasks`) and are lost if the container restarts. Acceptable for the demo.

## 7. Gotchas

- **Run uvicorn with one worker.** There's one SQLite connection per process, and the Granite one-job semaphore is per process. WAL handles multiple processes, but the demo doesn't need them.
- **`pydantic-core` on s390x:** if pip has no s390x wheel for the pinned version, it will try to build from source and needs Rust. Fix: `pip install` a version that has an s390x wheel, or install `rustc cargo`, or build on top of the Lab 2 / Jupyter image that already has these packages.
- **GnuCOBOL in the container:** the Dockerfile tries `gnucobol3`, then `gnucobol`, on Debian bookworm (`python:3.11-slim`). If neither installs on s390x, set `POLICY_ENGINE=python` and say so in the video. Don't copy a host-compiled binary in, because it links against the host's `libcob`.
- **Windows only:** `cobc` from MSYS2 needs `COB_CONFIG_DIR=C:/msys64/ucrt64/share/gnucobol/config`. Not needed on Linux.
- `POLICY_ENGINE=auto` silently uses the Python twin if the binary is missing. The Docker image sets `cobol` explicitly, so a missing binary shows up as AMBER `POLICY_ENGINE_ERROR`. Check `/health` → `policy_engine`.
- **The `X-Role` header is demo-grade access control,** acceptable only because the gateway is reachable solely through the SSH tunnel. `/journal` has no auth for the same reason. Say so if asked; don't expose port 8080 publicly.
- **The stub keeps global state** (evidence, first use of each photo). Restart it to reset duplicate detection between demo takes.
- `txn/.gitignore` excludes `clmi.db*` and the compiled `CLMIPOL`. Don't commit databases or binaries.
