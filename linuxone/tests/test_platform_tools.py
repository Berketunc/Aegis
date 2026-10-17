"""Platform-tool checks using synthetic data, without Docker or model installs."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import threading

import pytest

LINUXONE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("aegis_gateway_benchmark_tools", LINUXONE / "benchmark_gateway.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def embedded_python(filename):
    text = (LINUXONE / filename).read_text()
    source = text.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace = {"__name__": "aegis_embedded_test"}
    exec(compile(source, filename, "exec"), namespace)
    return namespace


def test_benchmark_keeps_failed_amber_distinct_from_scored_amber():
    unavailable = {"band": "AMBER", "reasons": ["THRESHOLDS_UNAVAILABLE"],
                   "model_version": None, "thresholds_version": "unavailable"}
    scored = {"band": "AMBER", "reasons": ["AI_SCORE_ABOVE_AMBER"],
              "model_version": "det-r18-v2", "thresholds_version": "t-1"}
    assert benchmark.classify(200, unavailable, None) == "degraded_amber"
    assert benchmark.classify(200, scored, None) == "scored_response"
    assert benchmark.classify(200, {**scored, "model_version": "stub-0"}, None) == "fixture_model_response"
    assert benchmark.classify(503, None, "http_error") == "request_error"


def test_synthetic_load_counts_warmup_separately_and_writes_pending_reports(tmp_path, monkeypatch):
    keys = []
    uploads = []
    guard = threading.Lock()
    digest = hashlib.sha256(benchmark.SYNTHETIC_PNG).hexdigest()

    def fake_request(url, *, data=None, headers=None, timeout=5):
        if url.endswith("/health"):
            return 200, {"status": "ok", "txn": "CLMI", "private_extra": "not copied"}, 1.0, None
        if url.endswith("/evidence"):
            assert 'multipart/form-data; boundary=' in headers["Content-Type"]
            assert b'name="file"' in data and benchmark.SYNTHETIC_PNG in data
            uploads.append(data)
            return 200, {"evidence_id": "EV-" + digest, "sha256": digest}, 123.0, None
        claim = json.loads(data)
        with guard:
            keys.append(claim["idempotency_key"])
        return 200, {"claim_id": "CLM-fixture", "band": "AMBER", "reasons": ["RISK_SERVICE_UNAVAILABLE"],
                     "model_version": None, "thresholds_version": "t-1"}, 7.0, None

    monkeypatch.setattr(benchmark, "request_json", fake_request)
    args = benchmark.arguments(["--synthetic", "--configuration", "plumbing", "--requests", "4",
                                "--warmup", "1", "--concurrency", "1", "2", "--output-dir", str(tmp_path)])
    report = benchmark.run_benchmark(args)
    benchmark.write_artifacts(report, tmp_path)
    assert len(uploads) == 1
    assert len(keys) == len(set(keys)) == 10  # 2 * (1 warmup + 4 measured)
    assert report["measurement_status"] == "pending_final_detector_benchmark"
    assert report["inputs"][0]["upload_round_trip_ms"] == 123.0
    assert "private_extra" not in report["gateway_health"]
    for row in report["configurations"]:
        assert row["requests"] == 4
        assert row["warmup_outcomes"] == {"degraded_amber": 1}
        assert row["outcomes"] == {"degraded_amber": 4}
        assert row["latency_ms"]["all_attempts"]["p95"] == 7.0
        assert row["latency_ms"]["scored_responses"] is None
        assert row["scored_responses_per_second"] == 0
    assert (tmp_path / "gateway_benchmark_plumbing.json").is_file()
    assert "pending: plumbing" in (tmp_path / "latency.md").read_text()
    assert "final detector benchmark pending" in (tmp_path / "latency.svg").read_text()


def test_benchmark_rejects_nonlocal_url_and_unsafe_report_name():
    with pytest.raises(SystemExit):
        benchmark.arguments(["--synthetic", "--url", "https://example.com"])
    with pytest.raises(SystemExit):
        benchmark.arguments(["--synthetic", "--configuration", "../../private"])


def test_gateway_evidence_excludes_raw_payload_and_vault_values(tmp_path, monkeypatch):
    namespace = embedded_python("collect_evidence.sh")
    database = tmp_path / "gateway.db"
    db = sqlite3.connect(database)
    db.executescript("""
        CREATE TABLE claims (claim_id TEXT);
        CREATE TABLE decisions (band TEXT);
        CREATE TABLE journal (seq INTEGER, ts TEXT, txn TEXT, claim_id TEXT, event TEXT, payload_json TEXT);
        CREATE TABLE egress (egress_id TEXT, claim_id TEXT, payload_json TEXT, ts TEXT);
        CREATE TABLE token_vault (token TEXT, value TEXT);
        INSERT INTO claims VALUES ('CLM-1');
        INSERT INTO decisions VALUES ('AMBER');
        INSERT INTO journal VALUES (1,'now','CLMI','CLM-1','DECISION','raw-person@example.invalid');
        INSERT INTO token_vault VALUES ('PERSON_12345678','raw-person@example.invalid');
    """)
    db.execute("INSERT INTO egress VALUES (?,?,?,?)", ("EG-1", "CLM-1", json.dumps({"description_redacted": "raw-person@example.invalid"}), "now"))
    db.commit()
    db.close()
    monkeypatch.setenv("DB_PATH", str(database))
    output = io.StringIO()
    with redirect_stdout(output):
        exec(namespace["GATEWAY_SUMMARY_CODE"], {})
    result = json.loads(output.getvalue())
    assert result["counts"]["token_vault"] == 1
    assert result["egress_metadata"][0]["field_names_only"] == ["description_redacted"]
    assert "raw-person@example.invalid" not in output.getvalue()
    assert "payload_json" not in output.getvalue()


def make_runtime(project):
    runtime = project / ".runtime"
    runtime.mkdir()
    secret = b"AEGIS_EVIDENCE_KEY_B64=private-fixture-key\nHMAC_KEY=private-fixture-hmac\n"
    (runtime / "services.env").write_bytes(secret)
    (runtime / "data").mkdir()
    (runtime / "data" / "original.aesgcm").write_bytes(b"AEGIS1\x00opaque-encrypted-fixture")
    (project / "eval").mkdir()
    (project / "eval" / "gateway_smoke.json").write_text('{"status":"passed"}')
    return runtime, secret


def run_backup(project, output):
    return subprocess.run(["bash", str(LINUXONE / "backup_runtime.sh"), str(output)],
                          env={**os.environ, "AEGIS_PROJECT_DIR": str(project)},
                          capture_output=True, text=True, timeout=30)


def test_backup_preserves_keys_and_committed_wal_rows_without_hot_copy(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, secret = make_runtime(project)
    database = runtime / "data" / "index.sqlite3"
    live = sqlite3.connect(database)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("CREATE TABLE proof (value TEXT)")
    live.execute("INSERT INTO proof VALUES ('committed-in-wal')")
    live.commit()
    assert Path(str(database) + "-wal").is_file()
    (runtime / "backups").mkdir()
    (runtime / "backups" / "old.tar.gz").write_bytes(b"must not recurse")
    output = tmp_path / "private" / "archive.tar.gz"
    result = run_backup(project, output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "private-fixture-key" not in result.stdout + result.stderr
    assert output.stat().st_mode & 0o777 == 0o600
    with tarfile.open(output) as archive:
        names = archive.getnames()
        assert ".runtime/services.env" in names
        assert archive.extractfile(".runtime/services.env").read() == secret
        assert not any(name.endswith(("-wal", "-shm", "-journal")) or "/backups/" in name for name in names)
        data = archive.extractfile(".runtime/data/index.sqlite3").read()
        manifest = json.load(archive.extractfile("BACKUP_MANIFEST.json"))
        original = archive.extractfile(".runtime/data/original.aesgcm").read()
    restored = tmp_path / "restored.sqlite3"
    restored.write_bytes(data)
    check = sqlite3.connect(restored)
    assert check.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert check.execute("SELECT value FROM proof").fetchall() == [("committed-in-wal",)]
    check.close()
    live.close()
    entry = next(item for item in manifest["files"] if item["path"] == ".runtime/data/original.aesgcm")
    assert entry["sha256"] == hashlib.sha256(original).hexdigest()
    assert manifest["cross_database_atomic_snapshot"] is False


def test_backup_refuses_recursive_output_overwrite_and_symlinks(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, _ = make_runtime(project)
    assert run_backup(project, runtime / "backup.tar.gz").returncode != 0
    existing = tmp_path / "existing.tar.gz"
    existing.write_bytes(b"preserve")
    assert run_backup(project, existing).returncode != 0
    assert existing.read_bytes() == b"preserve"
    outside = tmp_path / "unrelated-private-file"
    outside.write_text("do not follow")
    (runtime / "unexpected-link").symlink_to(outside)
    target = tmp_path / "refused.tar.gz"
    assert run_backup(project, target).returncode != 0
    assert not target.exists()


def test_shell_wrappers_have_valid_syntax():
    for name in ("collect_evidence.sh", "backup_runtime.sh"):
        completed = subprocess.run(["bash", "-n", str(LINUXONE / name)], capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
