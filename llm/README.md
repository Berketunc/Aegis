# Local Granite runtime (Person B)

This internal FastAPI service runs a **local big-endian Granite GGUF** using the event
image's existing s390x `llama-cpp-python`. It does not download a model or call an external API.
Missing GGUF, missing prompt files, or missing secret leave `/health` **503 unready** and
jobs are refused. A passing unit test is not evidence that Granite has run on LinuxONE.

Prerequisites:

- B supplies the Lab 2 GGUF already on the VM, its exact filename, and its licence.
- C supplies/reviews `pii.txt` and `note.txt` using `prompts/*.example.txt` as starting points.
- The caller and service share `RISK_SHARED_SECRET` (at least 32 random characters).
- C calls these endpoints after the decision, only for AMBER/RED; C's rules handle the
  immediate privacy pass. D polls via C's gateway; this service has no public port.

Build from the repository root on LinuxONE:

```bash
sudo docker build -f llm/Dockerfile -t aegis-granite .
```

Copy `.env.example` to a private env file, set its real values, and create the shared
internal Docker network if the platform setup has not already done so. Example run
after `/home/linux1/aegis/models` and `/home/linux1/aegis/prompts` contain those assets:

```bash
sudo docker run -d --name aegis-granite --network aegis-internal \
  --env-file /home/linux1/aegis/.env \
  --mount type=bind,src=/home/linux1/aegis/models,dst=/models,readonly \
  --mount type=bind,src=/home/linux1/aegis/prompts,dst=/prompts,readonly \
  aegis-granite
```

The root `linuxone/start_services.sh --with-granite` provides the integrated equivalent.
No `-p` mapping is needed. Use exactly **one uvicorn worker**, so the GGUF loads once
and the one-job-at-a-time limit holds. Defaults use 2 CPU threads, context 1024, and
256 output tokens; all are configurable in `.env.example`. If a description cannot
fit, its job fails with `INPUT_EXCEEDS_CONTEXT`; it is never silently truncated.

## API for C and D

Authenticate job endpoints with `X-Risk-Secret: <shared secret>`. `/health` has no secret
and returns readiness, configuration errors, queue depth and in-flight count.

`POST /pii` returns HTTP 202 with `job_id` and `poll_url`:

```json
{"claim_id":"CLM-000123","band":"AMBER","description":"Contact Alex Doe about the bumper."}
```

The completed result contains `findings`, each with `kind` and an exact `text` substring,
and `review_status: "model_second_pass"`. **C must pseudonymise those findings before
storage/display.** The service cannot certify that an empty list contains no missed PII.
Do not use an unavailable/failed check as successful clearance.

`POST /note` accepts only a previously redacted record and bounded signals:

```json
{
  "claim_id":"CLM-000123", "band":"RED",
  "redacted_description":"PERSON_7f3a reports a dented rear bumper.",
  "damage_category":"rear bumper",
  "signals":{"ai_score":0.91,"duplicate_found":false,"flags":["exif_absent"]}
}
```

The result has exactly three `bullets`, `advisory: true`, and `changes_band: false`.
The third bullet must state that the detector score is one signal, not proof. Prompts
treat the description as untrusted data and prohibit speculation about a person.
The runtime never emits or changes a claim band. These guardrails still need C's
prompt-injection tests against the supplied real model.

`GET /jobs/{job_id}` reports `queued`, `running`, `succeeded`, or `failed`.
Model/schema errors return HTTP **502**, and context overflow returns **422**;
the body includes `status: "failed"` and a bounded error code, with no invented result.
Only the initial POST is an acknowledgement; it is not proof the PII check succeeded.

There is one inference worker plus a queue of 4 waiting jobs (configurable); a full
queue returns 429. Completed job results expire after 15 minutes and a limit of 128
records bounds memory. Jobs are ephemeral and lost at restart. The original request
body is cleared after processing. `DELETE /jobs/{job_id}` deletes completed results
early; C should use this after persisting the reviewed result on the VM. Neither
request bodies nor generated text are logged. PII findings are sensitive local data.

Each successful real job reports completion tokens, elapsed generation time,
completion tokens per wall-clock second (including prompt processing), and current
process RSS on Linux. Use these plus `docker stats` for B's performance evidence.
No throughput or RAM values have been measured against a real GGUF in this checkout.

## Local contract checks without a GGUF

```bash
python -m pytest llm/tests -q
```

Tests inject a runtime double; they cover absent-model readiness, auth, bounded queue
and result storage, serial generation, malformed output, exact PII spans, and advisory
note contracts. They do not load llama.cpp or download model weights.

Sources: [event image and Lab 2](https://github.com/linuxone-community-cloud/jupyter-lab-ml),
[llama-cpp-python API](https://llama-cpp-python.readthedocs.io/en/latest/api-reference/),
[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/).
