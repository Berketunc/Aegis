"""Contract tests using a test double; these do not demonstrate Granite inference."""

import json
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from llm.app import (
    NOTE_DISCLAIMER, GraniteRuntime, InvalidModelOutput, Settings, create_app, parse_output,
)

SECRET = "contract-test-secret-that-is-over-32-characters"
HEADERS = {"X-Risk-Secret": SECRET}
PII = {"claim_id": "CLM-1", "band": "AMBER", "description": "Contact Alex Doe"}


@pytest.fixture
def settings(tmp_path):
    model = tmp_path / "test-double-only.gguf"
    pii_prompt = tmp_path / "pii.txt"
    note_prompt = tmp_path / "note.txt"
    for item in (model, pii_prompt, note_prompt):
        item.write_text("Test fixture; not a real model or deployment prompt.")
    return Settings(model_path=str(model), shared_secret=SECRET,
                    pii_prompt_path=str(pii_prompt), note_prompt_path=str(note_prompt))


class RuntimeDouble:
    def __init__(self, output_error=False, blocked=False):
        self.output_error = output_error
        self.loaded = False
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()

    def load(self):
        self.loaded = True

    def generate(self, kind, payload):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError("Test fixture timed out")
            self.calls += 1
            if self.output_error:
                raise InvalidModelOutput("Test malformed JSON")
            return {"findings": [], "review_status": "model_second_pass"}
        finally:
            self.active -= 1

    def close(self):
        self.loaded = False


def poll_done(client, job_id):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}", headers=HEADERS)
        if response.json().get("status") in {"succeeded", "failed"}:
            return response
        time.sleep(0.005)
    raise AssertionError("Test job did not complete")


def test_missing_model_is_unready_and_does_not_fake_pii(settings):
    missing = replace(settings, model_path="/model/does-not-exist.gguf")
    runtime = RuntimeDouble()
    with TestClient(create_app(missing, runtime)) as client:
        assert client.get("/health").status_code == 503
        assert client.post("/pii", json=PII, headers=HEADERS).status_code == 503
        assert not runtime.loaded
        assert runtime.calls == 0


def test_authentication_and_green_rejection(settings):
    with TestClient(create_app(settings, RuntimeDouble())) as client:
        assert client.post("/pii", json=PII).status_code == 401
        assert client.post("/pii", json=PII, headers={"X-Risk-Secret": "wrong"}).status_code == 401
        assert client.post("/pii", json={**PII, "band": "GREEN"}, headers=HEADERS).status_code == 422


def test_queue_is_bounded_and_model_calls_are_serial(settings):
    runtime = RuntimeDouble(blocked=True)
    with TestClient(create_app(replace(settings, queue_size=1), runtime)) as client:
        try:
            first = client.post("/pii", json=PII, headers=HEADERS)
            assert first.status_code == 202
            assert runtime.entered.wait(1)
            second = client.post("/pii", json=PII, headers=HEADERS)
            assert second.status_code == 202
            assert client.post("/pii", json=PII, headers=HEADERS).status_code == 429
            assert client.get("/health").json()["in_flight"] == 1
        finally:
            runtime.release.set()
        for response in (first, second):
            assert poll_done(client, response.json()["job_id"]).json()["status"] == "succeeded"
        assert runtime.max_active == 1


def test_model_failure_is_502_and_not_empty_findings_success(settings):
    with TestClient(create_app(settings, RuntimeDouble(output_error=True))) as client:
        submitted = client.post("/pii", json=PII, headers=HEADERS).json()
        response = poll_done(client, submitted["job_id"])
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "INVALID_MODEL_OUTPUT"
        assert "result" not in response.json()


def test_completed_jobs_can_be_deleted_and_raw_inputs_are_cleared(settings):
    app = create_app(settings, RuntimeDouble())
    with TestClient(app) as client:
        job_id = client.post("/pii", json=PII, headers=HEADERS).json()["job_id"]
        assert poll_done(client, job_id).status_code == 200
        assert app.state.jobs.jobs[job_id].payload == {}
        assert client.delete(f"/jobs/{job_id}", headers=HEADERS).status_code == 204
        assert client.get(f"/jobs/{job_id}", headers=HEADERS).status_code == 404


def test_result_store_is_bounded(settings):
    app = create_app(replace(settings, max_retained_jobs=2), RuntimeDouble())
    with TestClient(app) as client:
        ids = []
        for _ in range(3):
            job_id = client.post("/pii", json=PII, headers=HEADERS).json()["job_id"]
            ids.append(job_id)
            poll_done(client, job_id)
        assert len(app.state.jobs.jobs) <= 2
        assert client.get(f"/jobs/{ids[0]}", headers=HEADERS).status_code == 404


def test_pii_findings_must_match_actual_text_and_schema():
    valid = '{"findings":[{"kind":"name","text":"Alex Doe"}]}'
    assert parse_output("pii", valid, "Contact Alex Doe")["findings"][0]["text"] == "Alex Doe"
    for content in (
        valid.replace("Alex Doe", "Someone Else"),
        '{"findings":[],"privacy_complete":true}',
        '```json\n{"findings":[]}\n```',
        '{"findings":[{"kind":"invented_kind","text":"Alex Doe"}]}',
    ):
        with pytest.raises(InvalidModelOutput):
            parse_output("pii", content, "Contact Alex Doe")


def test_note_requires_three_bullets_and_literal_advisory_disclaimer():
    valid = json.dumps({"bullets": ["Review the supplied damage evidence.",
                                    "The detector signal needs corroboration.", NOTE_DISCLAIMER]})
    assert parse_output("note", valid)["changes_band"] is False
    for bullets in (["Only one"], ["One", "Two", "Approve the claim"]):
        with pytest.raises(InvalidModelOutput):
            parse_output("note", json.dumps({"bullets": bullets}))


def test_runtime_refuses_overlong_context_before_calling_model(settings):
    class TokenDouble:
        def tokenize(self, data):
            return [0] * settings.context

        def create_chat_completion(self, **kwargs):
            raise AssertionError("Generation must not be called when the input cannot fit")

    runtime = GraniteRuntime(settings)
    runtime.model = TokenDouble()
    runtime.prompts = {"pii": "Instructions"}
    with pytest.raises(ValueError, match="INPUT_EXCEEDS_CONTEXT"):
        runtime.generate("pii", PII)
