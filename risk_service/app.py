"""Internal API. The gateway owns decisions, user authentication and egress."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hmac
import json
import logging
import time

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import Settings, read_thresholds
from .detector import Detector
from .dup import EvidenceIndex
from .precompute import InvalidImage, PREPROCESS_VERSION
from .privacy import PrivacyAdapter
from .storage import EvidenceStore, load_encryption_key

logger = logging.getLogger(__name__)


class EvidenceReference(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=100, pattern=r"^EV-[a-f0-9]{64}$")


class RiskRequest(BaseModel):
    claim_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    description: str = Field(max_length=20000)
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=8)
    privacy_context: dict = Field(default_factory=dict)
    policy_token: str | None = Field(default=None, max_length=100)


def elapsed(start):
    return round((time.perf_counter() - start) * 1000, 3)


def timed(function, *args):
    start = time.perf_counter()
    result = function(*args)
    return result, elapsed(start)


def create_app(settings: Settings | None = None):
    config = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.config = config
        app.state.index = EvidenceIndex(config.data_dir)
        app.state.store = None
        app.state.storage_error = "evidence_key_not_configured"
        if config.evidence_key_b64:
            try:
                app.state.store = EvidenceStore(config.data_dir, app.state.index, load_encryption_key(config.evidence_key_b64))
                app.state.storage_error = None
            except Exception:
                app.state.storage_error = "evidence_key_invalid"
        app.state.detector = Detector(config.model_manifest, config.device, config.threads)
        app.state.privacy = PrivacyAdapter(config.privacy_adapter)
        try:
            app.state.thresholds = read_thresholds(config.thresholds_path)
        except Exception:
            app.state.thresholds = None
        app.state.workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="aegis-risk")
        yield
        app.state.workers.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="AEGIS internal risk service", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default errors can echo PII from invalid request bodies.
        return JSONResponse(status_code=422, content={"detail": "Invalid request fields"})

    def authenticate(x_risk_secret: str | None = Header(default=None)):
        if not config.secret:
            raise HTTPException(503, "Risk-service authentication is not configured")
        if not x_risk_secret or not hmac.compare_digest(x_risk_secret.encode(), config.secret.encode()):
            raise HTTPException(401, "Invalid service credentials")

    @app.get("/health")
    def health():
        state = app.state
        ready = bool(config.secret and state.store and state.detector.ready and state.privacy.ready and state.thresholds)
        return {
            "status": "ready" if ready else "not_ready",
            "ready": ready,
            "upload_ready": bool(config.secret and state.store),
            "model": {"ready": state.detector.ready, "version": state.detector.version,
                      "device": state.detector.device, "artifact_sha256": state.detector.artifact_sha256,
                      "error": state.detector.error},
            "privacy": {"ready": state.privacy.ready, "error": state.privacy.error},
            "thresholds_version": state.thresholds["thresholds_version"] if state.thresholds else None,
            "evidence_storage": {"ready": bool(state.store), "original_encryption": "AES-256-GCM",
                                 "error": state.storage_error},
            "preprocessing_version": PREPROCESS_VERSION,
            "granite": {"configured": False},
        }

    @app.post("/evidence", dependencies=[Depends(authenticate)])
    async def evidence(file: UploadFile = File(...)):
        if app.state.store is None:
            raise HTTPException(503, "Evidence encryption is not configured")
        start = time.perf_counter()
        data = bytearray()
        try:
            while chunk := await file.read(1024 * 1024):
                data.extend(chunk)
                if len(data) > config.max_upload_bytes:
                    raise HTTPException(413, "Image exceeds upload-size limit")
            if not data:
                raise HTTPException(400, "Image is empty")
            result = await run_in_threadpool(app.state.store.put, bytes(data))
        except InvalidImage as exc:
            raise HTTPException(415, str(exc)) from exc
        except HTTPException:
            raise
        except Exception:
            # Do not log upload bytes, filename, EXIF or request body.
            logger.error("Evidence storage failed")
            raise HTTPException(503, "Evidence storage unavailable") from None
        finally:
            await file.close()
        result["timings_ms"] = {"upload_preprocess": elapsed(start)}
        return result

    @app.post("/risk/score", dependencies=[Depends(authenticate)])
    def score(request: RiskRequest):
        state, start = app.state, time.perf_counter()
        if not state.store or not state.detector.ready:
            raise HTTPException(503, "Trained detector unavailable")
        if not state.privacy.ready:
            raise HTTPException(503, "Privacy rules unavailable")
        if not state.thresholds:
            raise HTTPException(503, "Versioned thresholds unavailable")
        ids = list(dict.fromkeys(item.evidence_id for item in request.evidence))
        records = [state.index.get(item) for item in ids]
        if any(record is None for record in records):
            raise HTTPException(404, "Evidence was not found")
        if any(record["preprocessing_version"] != PREPROCESS_VERSION for record in records):
            raise HTTPException(503, "Evidence preprocessing version is incompatible")

        def inference():
            predictions, load_ms, inference_ms = [], 0.0, 0.0
            for record in records:
                crops, milliseconds = timed(state.store.load_crops, record)
                load_ms += milliseconds
                prediction, milliseconds = timed(state.detector.score, crops)
                inference_ms += milliseconds
                predictions.append({"evidence_id": record["evidence_id"], **prediction})
            return predictions, load_ms, inference_ms

        def metadata():
            per_image = [json.loads(record["metadata"]) for record in records]
            return {"exif_present": any(item["exif_present"] for item in per_image),
                    "software_tag": next((item["software_tag"] for item in per_image if item["software_tag"]), None),
                    "c2pa_manifest": any(item["c2pa_manifest"] for item in per_image),
                    "c2pa_check": "marker_presence_only_unverified"}

        workers = state.workers
        futures = {
            "inference": workers.submit(inference),
            "privacy": workers.submit(timed, state.privacy.process, request.description,
                                      {**request.privacy_context, "claim_id": request.claim_id}),
            "metadata": workers.submit(timed, metadata),
        }
        try:
            predictions, load_ms, inference_ms = futures["inference"].result()
            private, privacy_ms = futures["privacy"].result()
            flags, metadata_ms = futures["metadata"].result()
            # Commit the association only after all processing succeeds. This
            # final atomic lookup+registration also closes cross-request races.
            matches, duplicate_ms = timed(state.index.check_and_register, request.claim_id, ids, request.policy_token)
        except Exception:
            for future in futures.values():
                future.cancel()
            logger.error("Risk scoring failed")
            raise HTTPException(503, "Risk processing unavailable") from None
        ai_score = max(item["ai_score"] for item in predictions)
        # The gateway alone assigns GREEN/AMBER/RED. This is eligibility only;
        # no background job is claimed until the Granite service is connected.
        eligible = ai_score >= state.thresholds["tau_amber"] or bool(matches) or bool(flags["software_tag"])
        return {
            "claim_id": request.claim_id,
            "model_version": state.detector.version,
            "thresholds_version": state.thresholds["thresholds_version"],
            "signals": {"ai_score": ai_score, "metadata": flags,
                        "duplicate_of": matches[0]["claim_id"] if matches else None,
                        "duplicate_matches": matches, "evidence_scores": predictions},
            "egress_payload_id": private["egress_payload_id"],
            "redacted_record": private["redacted_record"],
            "async": {"eligible": eligible, "status": "not_configured"},
            "timings_ms": {"privacy": privacy_ms, "load_crops": round(load_ms, 3),
                           "inference": round(inference_ms, 3), "metadata": metadata_ms,
                           "dup": duplicate_ms, "total": elapsed(start)},
        }

    return app


app = create_app()
