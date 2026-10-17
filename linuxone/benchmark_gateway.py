#!/usr/bin/env python3
"""Measure the local claims API, keeping degraded plumbing results explicit.

Uploads happen before the decision benchmark. Each concurrency configuration
uses separate warmup and measured claims with unique idempotency keys. No model,
threshold, or runtime configuration is changed by this tool.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from xml.sax.saxutils import escape

SYNTHETIC_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAFAAAAA8CAIAAAB+RarbAAAApElEQVR4nO3bsQ3CUAwA0cvpDwQbwQSsAhMwEiuxACGRkIBwvNYuLNulPe0OZ0okRmIkRmIkRmIkRmIkZjwP364nNmh/vMyFJGa82LBvs7iSEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxEiMxY03SRo+XHspNePq/APw4iZEYiZEYiZEYP13Au90BSW4Kmsyy2kQAAAAASUVORK5CYII="
)
FAILURE_REASONS = {
    "RISK_SERVICE_UNAVAILABLE", "THRESHOLDS_UNAVAILABLE", "POLICY_ENGINE_ERROR",
    "POLICY_INPUT_INVALID", "THRESHOLDS_VERSION_MISMATCH", "RISK_THRESHOLDS_MISMATCH",
}


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--synthetic", action="store_true", help="Explicit plumbing fixture, never final detector latency")
    inputs.add_argument("--image", type=Path, action="append", help="JPEG/PNG; repeat to cycle supplied photos")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--configuration", default="current", help="Recorded label, not automatic backend verification")
    parser.add_argument("--requests", type=positive, default=200, help="Measured requests PER concurrency configuration")
    parser.add_argument("--warmup", type=positive, default=10, help="Additional unmeasured requests per configuration")
    parser.add_argument("--concurrency", type=positive, nargs="+", default=[1, 4, 8])
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent.parent / "eval")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.configuration):
        parser.error("configuration must be 1–64 letters, numbers, underscores or hyphens")
    location = urllib.parse.urlsplit(args.url)
    if (location.scheme != "http" or location.hostname not in {"localhost", "127.0.0.1", "::1"}
            or location.username or location.password or location.query or location.fragment
            or location.path not in {"", "/"}):
        parser.error("url must be a local HTTP gateway or SSH tunnel, without credentials/path/query")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be a positive finite number")
    args.url = args.url.rstrip("/")
    args.concurrency = list(dict.fromkeys(args.concurrency))
    return args


def request_json(url, *, data=None, headers=None, timeout=5):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, body = response.status, response.read()
        elapsed = (time.perf_counter() - started) * 1000
        try:
            return status, json.loads(body), elapsed, None
        except (ValueError, UnicodeError):
            return status, None, elapsed, "invalid_json"
    except urllib.error.HTTPError as error:
        # Do not retain arbitrary server bodies, which can echo request PII.
        return error.code, None, (time.perf_counter() - started) * 1000, "http_error"
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, None, (time.perf_counter() - started) * 1000, type(error).__name__


def statistics(samples):
    if not samples:
        return None
    ordered = sorted(samples)
    def quantile(q):
        position = (len(ordered) - 1) * q
        lower, upper = math.floor(position), math.ceil(position)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {"count": len(samples), "p50": quantile(.50), "p95": quantile(.95),
            "p99": quantile(.99), "min": ordered[0], "max": ordered[-1]}


def classify(status, body, error):
    if error or status != 200 or not isinstance(body, dict):
        return "request_error"
    band = body.get("band")
    reasons = body.get("reasons")
    if band not in {"GREEN", "AMBER", "RED"} or not isinstance(reasons, list):
        return "invalid_decision"
    failed = any(isinstance(reason, str) and (
        reason in FAILURE_REASONS or "UNAVAILABLE" in reason or "MISMATCH" in reason
        or reason.endswith("_ERROR")) for reason in reasons)
    model = body.get("model_version")
    thresholds = body.get("thresholds_version")
    versions_present = (isinstance(model, str) and bool(model.strip())
                        and isinstance(thresholds, str) and bool(thresholds.strip())
                        and thresholds not in {"unavailable", "unknown"})
    if failed or not versions_present:
        return "degraded_amber" if band == "AMBER" else "unverified_decision"
    if model.lower().startswith(("stub", "test", "random", "imagenet", "stock")):
        return "fixture_model_response"
    # This is evidence about the response contract, not model authenticity/accuracy.
    return "scored_response"


def upload_inputs(args):
    files = [(SYNTHETIC_PNG, "synthetic_fixture")] if args.synthetic else [
        (path.read_bytes(), "user_supplied_photo") for path in args.image]
    prepared = []
    for index, (raw, kind) in enumerate(files):
        if raw.startswith(b"\xff\xd8\xff"):
            extension, media_type = "jpg", "image/jpeg"
        elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
            extension, media_type = "png", "image/png"
        else:
            raise ValueError("Benchmark inputs must be JPEG or PNG")
        if len(raw) > 15 * 1024 * 1024:
            raise ValueError("Benchmark input exceeds the gateway's 15 MiB upload limit")
        boundary = "aegis-benchmark-" + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="benchmark-{index + 1}.{extension}"\r\nContent-Type: {media_type}\r\n\r\n').encode()
        body += raw + f"\r\n--{boundary}--\r\n".encode()
        status, result, elapsed, error = request_json(args.url + "/evidence", data=body,
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, timeout=args.timeout)
        digest = hashlib.sha256(raw).hexdigest()
        if (status != 200 or error or not isinstance(result, dict)
                or result.get("sha256") != digest or result.get("evidence_id") != "EV-" + digest):
            raise RuntimeError(f"Input {index + 1} upload failed or returned an incompatible evidence ID (HTTP {status})")
        prepared.append({"evidence_id": result["evidence_id"], "sha256": digest,
                         "size_bytes": len(raw), "kind": kind, "upload_round_trip_ms": elapsed})
    return prepared


def measured_claim(args, evidence, session):
    claim = {"idempotency_key": "load-" + uuid.uuid4().hex,
             "policy_number": "LOAD-" + session,
             "claimant": {"name": "Load Test", "email": "benchmark@example.invalid"},
             "incident": {"date": datetime.now(timezone.utc).date().isoformat(),
                          "description": "Fictional benchmark claim for uploaded vehicle-photo evidence."},
             "evidence": [{"evidence_id": evidence["evidence_id"]}]}
    payload = json.dumps(claim).encode()
    status, body, elapsed, error = request_json(args.url + "/claims", data=payload,
        headers={"Content-Type": "application/json"}, timeout=args.timeout)
    result = {"http_status": status, "elapsed_ms": elapsed, "outcome": classify(status, body, error)}
    if error:
        result["error_type"] = error
    if isinstance(body, dict):
        if body.get("band") in {"GREEN", "AMBER", "RED"}:
            result["band"] = body["band"]
        # Bounded deployment metadata only; never retain descriptions or response bodies.
        for key in ("model_version", "thresholds_version"):
            if isinstance(body.get(key), str):
                result[key] = body[key][:120]
    return result


def run_requests(args, evidence, count, concurrency, session):
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(lambda index: measured_claim(args, evidence[index % len(evidence)], session), range(count)))
    return results, time.perf_counter() - started


def counts(results, key):
    return dict(sorted(Counter(str(item.get(key, "none")) for item in results).items()))


def run_benchmark(args):
    _, health, _, _ = request_json(args.url + "/health", timeout=args.timeout)
    report = {"schema_version": 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "configuration_label": args.configuration, "backend_verification": "label supplied by operator; inspect runtime evidence for actual backend",
              "input_mode": "synthetic_plumbing" if args.synthetic else "user_supplied_photos",
              "measured_requests_per_configuration": args.requests, "warmup_requests_per_configuration": args.warmup,
              "measurement": "closed-loop HTTP round trip including commit; uploads, warmup and client JSON construction excluded; urllib opens a connection per request",
              "percentile_method": "linear interpolation at (N-1)*quantile",
              "gateway_health": {key: health[key] for key in ("status", "txn", "policy_engine", "thresholds_version")
                                 if isinstance(health, dict) and key in health},
              "limitations": ["Repeated evidence is intentionally reused across unique claims, exercising duplicate lookup and its policy path.",
                              "Scored-response classification uses API versions/reasons; it does not independently certify a trained model or photo authenticity.",
                              "Synthetic or degraded timings must not be described as final detector latency."],
              "configurations": []}
    evidence = upload_inputs(args)
    report["inputs"] = evidence
    session = uuid.uuid4().hex[:16]
    for concurrency in args.concurrency:
        warmups, _ = run_requests(args, evidence, args.warmup, concurrency, session)
        results, seconds = run_requests(args, evidence, args.requests, concurrency, session)
        outcomes = counts(results, "outcome")
        report["configurations"].append({
            "clients": concurrency, "requests": len(results), "warmup_outcomes": counts(warmups, "outcome"),
            "outcomes": outcomes, "http_status_counts": counts(results, "http_status"), "band_counts": counts(results, "band"),
            "model_versions": counts(results, "model_version"), "threshold_versions": counts(results, "thresholds_version"),
            "elapsed_seconds": seconds, "attempts_per_second": len(results) / seconds,
            "scored_responses_per_second": outcomes.get("scored_response", 0) / seconds,
            "latency_ms": {"all_attempts": statistics([item["elapsed_ms"] for item in results]),
                           "scored_responses": statistics([item["elapsed_ms"] for item in results if item["outcome"] == "scored_response"]),
                           "degraded_amber": statistics([item["elapsed_ms"] for item in results if item["outcome"] == "degraded_amber"])},
            "samples": results,
        })
    all_scored = all(row["outcomes"].get("scored_response", 0) == row["requests"] for row in report["configurations"])
    report["measurement_status"] = ("api_scoring_observed" if all_scored and not args.synthetic else "pending_final_detector_benchmark")
    report["scope"] = ("API scoring latency on supplied photos; deployment and accuracy require separate verification"
                       if report["measurement_status"] == "api_scoring_observed"
                       else "Plumbing/degraded or synthetic measurements only; final detector benchmark pending")
    return report


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        temporary.write(text)
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_artifacts(report, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"gateway_benchmark_{report['configuration_label']}.json"
    atomic_text(target, json.dumps(report, indent=2, allow_nan=False) + "\n")
    reports = []
    for path in sorted(output_dir.glob("gateway_benchmark_*.json")):
        try:
            candidate = json.loads(path.read_text())
            if candidate.get("schema_version") == 1 and "configurations" in candidate:
                reports.append(candidate)
        except (ValueError, OSError):
            continue
    lines = ["# Gateway latency and concurrency", "", "These are external HTTP round trips. Uploads and warmups are excluded.", "",
             "**Pending/synthetic/degraded rows are plumbing evidence, not final detector latency.**", "",
             "| Configuration | Clients | Scope | Measured | Scored | Degraded AMBER | Errors/unverified | p50 ms | p95 ms | p99 ms | Attempts/s |",
             "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    bars = []
    for item in reports:
        for row in item["configurations"]:
            stats, outcomes = row["latency_ms"]["all_attempts"], row["outcomes"]
            scored, degraded = outcomes.get("scored_response", 0), outcomes.get("degraded_amber", 0)
            scope = "supplied-photo API" if item["measurement_status"] == "api_scoring_observed" else "pending: plumbing"
            lines.append(f"| {item['configuration_label']} | {row['clients']} | {scope} | {row['requests']} | {scored} | {degraded} | "
                         f"{row['requests'] - scored - degraded} | {stats['p50']:.3f} | {stats['p95']:.3f} | {stats['p99']:.3f} | {row['attempts_per_second']:.2f} |")
            bars.append((f"{item['configuration_label']} / {row['clients']} clients", stats,
                         item["measurement_status"] == "api_scoring_observed"))
        lines.extend(["", f"{item['configuration_label']}: {item['scope']}. {item['warmup_requests_per_configuration']} additional warmups per client configuration.", ""])
    lines.extend(["Repeated input evidence is reused across different claims; duplicate lookup is included.",
                  "Backend labels are operator supplied. Confirm actual device/configuration from runtime evidence.",
                  "AMBER caused by unavailable scoring is counted separately from an AMBER scored decision.",
                  "The synthetic fixture and fictional form fields provide no detector-accuracy evidence."])
    atomic_text(output_dir / "latency.md", "\n".join(lines) + "\n")
    height, width = 150 + 76 * len(bars), 1020
    maximum = max((stats["p99"] for _, stats, _ in bars), default=1) or 1
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<g font-family="sans-serif" fill="#18232d"><text x="24" y="32" font-size="21">Gateway HTTP round-trip latency</text>',
           '<text x="24" y="57" font-size="13">Orange: plumbing/synthetic/degraded — final detector benchmark pending. Blue: scored API responses.</text>',
           '<text x="24" y="80" font-size="12">Each row shows p50 / p95 / p99. Backend labels are operator supplied; uploads and warmups excluded.</text>']
    for index, (label, stats, complete) in enumerate(bars):
        top = 105 + index * 76
        svg.append(f'<text x="24" y="{top + 16}" font-size="13">{escape(label)}</text>')
        for offset, metric in enumerate(("p50", "p95", "p99")):
            length, y = 560 * stats[metric] / maximum, top + offset * 19
            color = "#2877a8" if complete else "#b96c16"
            svg.extend([f'<rect x="245" y="{y}" width="{max(length, .5):.2f}" height="13" fill="{color}" opacity="{1 - offset * .17:.2f}"/>',
                        f'<text x="{255 + length:.2f}" y="{y + 11}" font-size="11">{metric}: {stats[metric]:.3f} ms</text>'])
    svg.extend(["</g>", "</svg>"])
    atomic_text(output_dir / "latency.svg", "\n".join(svg) + "\n")
    return target


def main(argv=None):
    args = arguments(argv)
    try:
        report = run_benchmark(args)
        target = write_artifacts(report, args.output_dir)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__, "error": str(error)}))
        return 1
    print(json.dumps({"status": report["measurement_status"], "scope": report["scope"], "report": str(target),
                      "configurations": [{"clients": row["clients"], "outcomes": row["outcomes"],
                                           "latency_ms": row["latency_ms"]["all_attempts"],
                                           "attempts_per_second": row["attempts_per_second"]} for row in report["configurations"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
