# Claims transaction `CLMI`: gateway, decision rule, privacy

This is the part of the system that **owns the decision**. AI services return signals; this layer validates the claim, pseudonymises personal data, calls the risk service under a hard deadline, runs the decision rule written in COBOL, commits everything atomically, and journals it.

> **What is real and what is emulated.** CICS is emulated in this demo by a FastAPI gateway that follows CICS transaction patterns. The decision rule is real COBOL (`CLMIPOL.cbl`), compiled with GnuCOBOL and running on LinuxONE. In production it runs inside a CICS transaction on z/OS (see [Production design](#production-design-cics-on-zos)).

## Flow

```
UI ── POST /evidence ──► gateway ──► risk service /evidence   (upload-time precompute, size + type checks)
UI ── POST /claims ────► gateway  "CLMI"
                           1. validate (photo present, evidence IDs known)
                           2. idempotency key seen before? → return the stored decision (journal: REPLAY)
                           3. privacy rules → pseudonymised record + token vault
                           4. risk service /risk/score, 300 ms hard deadline
                           5. CLMIPOL (COBOL) → band + reasons
                           6. SYNCPOINT: claim + decision + egress + journal in ONE SQLite transaction
                           7. respond: band, reasons, versions, timings
                           8. AMBER/RED only, after commit: Granite PII second pass + adjuster note
```

## Decision policy (`CLMIPOL.cbl`, Python twin in `gateway/policy.py`)

| Band | When | Outcome |
|---|---|---|
| **RED** | calibrated AI score ≥ τ_red, **or** the same photo was already used on a different claim | Refer to an investigator. **Never auto-reject.** |
| **AMBER** | τ_amber ≤ score < τ_red, **or** metadata names an editing/generation tool, **or** the risk service is unavailable, **or** score ≥ τ_red on a **low-resolution photo** (native short side < 480 px; reason `LOW_RESOLUTION_EVIDENCE`, because the detector's false-positive rate is far higher on small photos, see `eval/results.md`) | Human review |
| **GREEN** | otherwise | Fast-track |

Every matching reason is reported, and the band is the most severe one. Missing EXIF and a present C2PA manifest are added as `INFO_*` reasons and never change the band. Thresholds come from a versioned config (`THRESHOLDS_PATH`, written by the data team); the version, model version and timings are stored with every decision.

The COBOL program reads one fixed-width line and writes one line:
```
$ echo "07000 08500 06000 1 1 1 0" | ./CLMIPOL
RED DUPLICATE_PHOTO,AI_SCORE_ABOVE_AMBER,EDITING_SOFTWARE_TAG
$ echo "09000 08500 06000 0 0 1 1" | ./CLMIPOL
AMBER AI_SCORE_ABOVE_RED,LOW_RESOLUTION_EVIDENCE
```
Columns: score, τ_red, τ_amber (all ×10000), duplicate flag, software-tag flag, risk-service-OK flag, low-resolution flag. The test suite runs 25 truth-table cases against both the COBOL binary and the Python twin and requires identical output.

## Fail-safe: nothing fails open

| Failure | Result |
|---|---|
| Risk service down, 5xx, slower than 300 ms, invalid JSON, or answering for a different claim | AMBER `RISK_SERVICE_UNAVAILABLE` |
| COBOL binary missing, crashing, timing out or printing garbage | AMBER `POLICY_ENGINE_ERROR` |
| Thresholds config unreadable or out of order | AMBER `THRESHOLDS_UNAVAILABLE` |
| Malformed input to CLMIPOL | AMBER `POLICY_INPUT_INVALID` (from the COBOL itself) |
| Same claim submitted twice (or concurrently) | Same claim ID and decision; no re-scoring; journal `REPLAY` |
| No photo / unknown evidence ID | 400, nothing committed |
| Upload over 15 MB / not JPEG or PNG | 413 / 415 before it reaches the risk service |

## Privacy (`/privacy`, shared with the risk service)

- **Rules on the decision path (milliseconds):** regex + gazetteer for names (from the form, including first/last name alone), email, UK phone, postcode, vehicle registration, policy number, National Insurance number, Luhn-checked card numbers and dates of birth.
- **Pseudonymise, don't delete:** `KIND_` + first 8 hex of `HMAC-SHA256(key, kind|normalised value)`, e.g. `PERSON_7f3a91c2`. Deterministic, so investigators can link records without seeing identities. The token→value vault is a separate table; in production the key lives in IBM Z crypto hardware.
- **Rules are a first pass, not a guarantee of complete PII removal.** The token vault contains identity values and the private claim record can contain missed identifiers. Views report `privacy_review.status` and `description_released`. Pending/unavailable Granite review withholds description and damage-category free text from adjusters and current egress payloads. Successful PII review releases reviewed text even if note generation subsequently fails. GREEN and Granite-disabled deployments explicitly report `rules_only`.
- `X-Role: authorised` explicitly reveals the private record and journals `VIEW_ORIGINAL`. The role header and console role selector are demo controls, not production authentication. Keep the gateway on SSH-accessible loopback. The SQLite vault and crop cache are sensitive local files; only evidence originals have application-level AES-GCM encryption.
- **Egress allowlist:** the egress payload is built by *copying* named fields (redacted description, damage category, band, score, non-identifying flags) and re-scanned for PII. Everything else is dropped by construction. Each payload is stored and journaled.
- The detector still reads the **original** image: redaction would destroy the pixel traces it relies on. Privacy controls govern who sees personal data, not what the detector sees.

## Granite (on-prem LLM, after the decision)

Granite runs only for AMBER/RED claims, after the decision is committed, so **it cannot change the band**. Prompts are in [`llm/prompts/`](../llm/prompts/).
1. **PII second pass:** finds personal data the rules missed. Only values that literally occur in the text are accepted, and they are pseudonymised into the vault.
2. **Adjuster note:** 3 bullets from the redacted record and the signals. It must state that the detector score is one signal, not proof, and must not speculate about the person.

Guardrails: the claim text is fenced as untrusted data, and attempts to close the fence are neutralised. Output is schema- and length-checked and re-scanned for PII; anything that fails becomes "note unavailable". One job runs at a time.

**Contract with Granite:** authenticated `X-Risk-Secret`, typed JSON bodies to `POST /pii` (`claim_id`, `description`, `context`) and `POST /note` (`claim_id`, `redacted_record`, `signals`). Accepted work returns HTTP 202 with `job_id`; poll `GET /jobs/{job_id}` on the configured local service. PII review precedes note submission, under a bounded overall deadline. See [`llm/README.md`](../llm/README.md) for exact schemas. Raw prompts are not an API contract.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/evidence` | multipart `file` → `{"evidence_id", "sha256"}` |
| POST | `/claims` | plan.md §14 body plus optional `idempotency_key` (UUID made by the UI when the form opens; the `Idempotency-Key` header also works) → decision JSON |
| GET | `/claims/{id}` | decision + adjuster note status |
| GET | `/claims/{id}/views` | pseudonymised record, egress payload, original (authorised only) |
| POST | `/claims/{id}/heatmap` | optional AMBER/RED patch-map job; no model returns 503 |
| GET | `/claims/{id}/heatmap/{job_id}` | poll the claim’s advisory 4×4 patch-score job |
| GET | `/journal?claim_id=` | append-only journal (evidence screenshot) |
| GET | `/health` | thresholds version, active policy engine |

Decision response:
```json
{"claim_id": "CLM-000123", "band": "RED", "reasons": ["AI_SCORE_ABOVE_RED"], "model_version": "det-r18-v2",
 "thresholds_version": "t-1", "policy_engine": "cobol", "idempotency_key": "…", "replayed": false,
 "timings_ms": {"gateway": 6.0, "risk": 41.3, "policy": 3.1, "total": 52.7, "risk_detail": {"…": 0}}}
```

## Run

```bash
# Tests (Python 3.11). The COBOL parity tests run when `cobc` is on PATH, otherwise they are skipped.
pip install -r txn/requirements.txt
pytest txn/tests -q

# Local, with the stub risk service (filenames drive scores: *fake* → RED, *inflat*/*edit* → AMBER)
uvicorn txn.stub_risk:app --port 9000 &
uvicorn txn.gateway.app:app --port 8080
curl -X POST localhost:9000/stub/mode -H 'content-type: application/json' -d '{"mode":"fail"}'   # fail-safe demo

# On the LinuxONE VM
apt-cache policy gnucobol gnucobol3 && sudo apt install -y gnucobol3   # or gnucobol
cobc -x -o txn/CLMIPOL txn/CLMIPOL.cbl
docker compose -f txn/docker-compose.dev.yml up --build                # gateway on 127.0.0.1:8080 only
```

Main settings: `RISK_URL`, `RISK_SECRET` (canonical `X-Risk-Secret`; legacy `X-Shared-Secret` also sent for the labelled development stub), `RISK_TIMEOUT_MS` (300), `THRESHOLDS_PATH`, `POLICY_ENGINE` (`auto`/`cobol`/`python`), `CLMIPOL_BIN`, `HMAC_KEY`, `GRANITE_URL`, `DB_PATH`, `UI_DIR`. See `gateway/config.py`.

The example response above illustrates a fresh POST. `timings_ms.total` includes the database commit. Stored decisions and journal rows use `precommit_total`; GET/replay cannot recover the original post-commit time. Replays add their own `replay_timings_ms`. Measure full HTTP latency with `linuxone/benchmark_gateway.py`.

For LinuxONE use `bash linuxone/start_services.sh --with-gateway` and [`linuxone/INTEGRATION.md`](../linuxone/INTEGRATION.md). The development Compose stack uses synthetic scores and example thresholds, not trained-detector evidence.

## Production design: CICS on z/OS

Not built: this describes what the emulation stands in for.

```
CICS transaction CLMI (z/OS)                                LinuxONE (same IBM Z, HiperSockets)
  EXEC CICS WEB RECEIVE           claim JSON from the channel
  EXEC CICS TRANSFORM / JSON PARSE  into a COBOL copybook
  EXEC CICS WRITEQ TS             claim held in a temp-storage queue keyed by claim ID (idempotency)
  EXEC CICS WEB CONVERSE  ───────────────────────────────►  risk service /risk/score
                          ◄───────────────────────────────  signals (memory-to-memory, no external network)
  CALL 'CLMIPOL'                  the same decision rule as this repo
  EXEC CICS SYNCPOINT             claim + decision committed to Db2/VSAM with the CICS journal
  EXEC CICS WEB SEND              band + reasons to the channel
```

| Emulated here | Production |
|---|---|
| FastAPI route `POST /claims` | CICS transaction `CLMI`, `WEB RECEIVE` / `WEB SEND` |
| SQLite `BEGIN IMMEDIATE … COMMIT` | `EXEC CICS SYNCPOINT` over Db2/VSAM |
| `journal` table with append-only triggers | CICS system/user journal |
| `idem_key UNIQUE` | TS queue / Db2 unique key on claim ID |
| httpx call with a 300 ms deadline | `WEB CONVERSE` over HiperSockets with an RTIMOUT |
| GnuCOBOL `CLMIPOL` via subprocess | `CALL 'CLMIPOL'` compiled with Enterprise COBOL |
| HMAC key in an environment variable | Key in IBM Z crypto hardware (ICSF/CCA) |
