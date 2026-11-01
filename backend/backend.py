"""Voice Comparison Studio API — unified single-environment server with all models and metrics."""

from __future__ import annotations
import asyncio
import hashlib
import logging
import os
import shutil
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=False)

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import artifact_storage  # noqa: E402
import direct_inference  # noqa: E402
import lifecycle  # noqa: E402
import schema  # noqa: E402
import security  # noqa: E402
from api_schemas import (  # noqa: E402
    BatchRequest,
    GenerateRequest,
    LayoutRequest,
    Login,
    ModelControls,
    PasswordReset,
    RequestModel,
)
from database import db_connection, initialize_database  # noqa: E402
from export_response import PrivateExportResponse  # noqa: E402
from generation_config import (  # noqa: E402
    GenerationConfig,
    current as generation_config,
    effective_parameters,
)
from isolated_workers import (  # noqa: E402
    InferenceCancelled,
    TerminationFailure,
    WorkerPool,
)
from runtime_lock import DataLock  # noqa: E402
from security_headers import SecurityHeaders  # noqa: E402

logger = logging.getLogger("studio")

if security.production():
    os.umask(0o077)

ROOT = Path(
    os.environ.get("VOICE_STUDIO_DATA", Path(__file__).parent / "data")
).resolve()
if ROOT.is_relative_to((Path(__file__).resolve().parents[1] / "frontend").resolve()):
    raise RuntimeError("VOICE_STUDIO_DATA must be outside the frontend directory")
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / "audio").mkdir(exist_ok=True)
DB = ROOT / "studio.sqlite3"
DATA_LOCK = DataLock(ROOT / "runtime.lock")
if security.production():
    ROOT.chmod(0o700)
    (ROOT / "audio").chmod(0o700)

ORIGINS = [
    x.strip()
    for x in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if x.strip()
]

MODEL_IDS = ("qwen3", "cosyvoice3", "xtts-v2")
LABELS = {"qwen3": "Qwen3-TTS", "cosyvoice3": "CosyVoice 3", "xtts-v2": "XTTS-v2"}
POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="inference")
EXPORT_SLOTS = threading.BoundedSemaphore(2)
EVAL_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evaluation")
MODEL_LOCKS = {model: asyncio.Lock() for model in MODEL_IDS}
MAX_PENDING = int(os.getenv("MAX_PENDING_JOBS", "12"))
TASKS: dict[str, asyncio.Task] = {}
ADMISSION_LOCK = asyncio.Lock()
REPRODUCIBILITY_LOCK = threading.Lock()
WORKERS = WorkerPool()
JOB_CANCELLATIONS = {}

_evaluator = None
_evaluator_lock = threading.Lock()
_evaluation_jobs: set[str] = set()
_eval_jobs_lock = threading.Lock()

REFERENCE_DIR = Path(
    os.getenv("REFERENCE_DIR", Path(__file__).resolve().parent / "reference")
).resolve()
REFERENCE_AUDIO = REFERENCE_DIR / "reference.wav"
REFERENCE_TEXT = REFERENCE_DIR / "reference.txt"


@contextmanager
def db():
    with db_connection(DB) as connection:
        yield connection


# Initialize tables and migration tracking
initialize_database(DB)


def process_isolation():
    return os.getenv("INFERENCE_ISOLATION", "process") == "process"


def model_availability(model):
    status = direct_inference.model_availability(model)
    if process_isolation():
        supervised = WORKERS.status(model)
        state = supervised["state"]
        status.update(supervised)
        status["ready"] = supervised["loaded"]
        status["configured"] = status["configured"]
        if state == "failed":
            status["detail"] = (
                "Model worker failed; consult server logs and retry after correcting configuration"
            )
        elif state == "loading":
            status["detail"] = "Model weights are loading"
    return status


def get_evaluator():
    global _evaluator
    with _evaluator_lock:
        if _evaluator is None:
            from evaluator import Evaluator

            _evaluator = Evaluator()
        return _evaluator


def _startup_preload():
    if process_isolation():
        for model in (*MODEL_IDS, "evaluation"):
            try:
                WORKERS.get(model).call(
                    None, timeout=float(os.getenv("MODEL_LOAD_TIMEOUT", "600"))
                )
            except Exception:
                logger.warning("Preload unavailable model=%s", model)
        return
    direct_inference.preload_all_models()
    try:
        print(
            "[evaluator] Loading Whisper, SpeechBrain ECAPA, and ScoreQ...", flush=True
        )
        get_evaluator()
        print("[evaluator] All metric evaluators ready.", flush=True)
    except Exception as exc:
        print(f"[evaluator] Preload warning: {exc}", flush=True)


def stored_audio(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(ROOT / "audio") or not resolved.is_file():
        raise HTTPException(404, "Audio unavailable")
    return resolved


def require_reference(c, reference_id):
    shared = c.execute(
        "SELECT value FROM settings WHERE key=?",
        ("shared-reference:" + str(reference_id),),
    ).fetchone()
    if not shared:
        security.require_owner(c, "reference", reference_id)


def mark_shared_reference(c, reference_id):
    c.execute(
        "INSERT OR IGNORE INTO settings VALUES (?,?)",
        ("shared-reference:" + reference_id, "true"),
    )


def install_backend_reference():
    if not REFERENCE_AUDIO.is_file():
        return False
    try:
        with wave.open(str(REFERENCE_AUDIO), "rb") as reader:
            duration = reader.getnframes() / reader.getframerate()
            if duration < 0.25 or duration > 120:
                raise ValueError("Backend reference WAV must be 0.25-120 seconds long")
    except (wave.Error, ZeroDivisionError) as exc:
        raise RuntimeError(
            "backend/reference/reference.wav must be a valid PCM WAV recording"
        ) from exc
    data = REFERENCE_AUDIO.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    transcript = (
        REFERENCE_TEXT.read_text(encoding="utf-8").strip()
        if REFERENCE_TEXT.is_file()
        else ""
    )
    with db() as c:
        existing = c.execute(
            "SELECT r.id,r.path FROM references_audio r JOIN settings s ON s.key='shared-reference:'||r.id JOIN reference_versions v ON v.reference_id=r.id WHERE r.sha256=? AND v.transcript=? ORDER BY r.created DESC LIMIT 1",
            (digest, transcript),
        ).fetchone()
        if existing and Path(existing["path"]).is_file():
            ref_id = existing["id"]
        else:
            ref_id = str(uuid.uuid4())
            target = ROOT / "audio" / f"{ref_id}.wav"
            shutil.copy2(REFERENCE_AUDIO, target)
            c.execute(
                "INSERT INTO references_audio VALUES (?,?,?,?,?)",
                (ref_id, digest, "reference.wav", str(target), time.time()),
            )
        c.execute(
            "INSERT OR IGNORE INTO reference_versions VALUES (?,?,?)",
            (
                ref_id,
                transcript,
                "English" if "_en" in str(REFERENCE_AUDIO) else "German",
            ),
        )
        mark_shared_reference(c, ref_id)
        c.execute(
            "INSERT INTO reference_profiles(id,reference_id,transcript,updated) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET reference_id=excluded.reference_id,transcript=excluded.transcript,updated=excluded.updated",
            (ref_id, transcript, time.time()),
        )
    return True


def load_backend_reference():
    with db() as c:
        for select, update in (
            (
                "SELECT id,path FROM references_audio WHERE path IS NOT NULL",
                "UPDATE references_audio SET path=? WHERE id=?",
            ),
            (
                "SELECT id,output AS path FROM jobs WHERE output IS NOT NULL",
                "UPDATE jobs SET output=? WHERE id=?",
            ),
        ):
            for row in c.execute(select).fetchall():
                relocated = ROOT / "audio" / Path(row["path"]).name
                if not Path(row["path"]).is_file() and relocated.is_file():
                    c.execute(
                        update,
                        (str(relocated), row["id"]),
                    )
    install_backend_reference()
    with db() as c:
        interrupted = [
            row[0]
            for row in c.execute(
                "SELECT id FROM jobs WHERE status IN ('pending','running')"
            )
        ]
        for identifier in interrupted:
            try:
                uuid.UUID(identifier)
            except (ValueError, TypeError):
                continue
            (ROOT / "audio" / f"{identifier}.wav").unlink(missing_ok=True)
        c.execute("UPDATE batch_runs SET status='interrupted' WHERE status='planning'")
        c.execute(
            "UPDATE jobs SET status='failed',error='Inference interrupted by backend restart',updated=? WHERE status IN ('pending','running')",
            (time.time(),),
        )
        artifact_storage.backfill(c, ROOT)
        c.execute("DELETE FROM storage_reservations")
        c.execute(
            "UPDATE evaluation_runs SET status='interrupted',error='Evaluation interrupted by backend restart',updated=? WHERE status IN ('pending','running')",
            (time.time(),),
        )


# Import routing modules and service delegates
import services.synthesis as synthesis_service  # noqa: E402
import routes.models as models_routes  # noqa: E402
import routes.generations as generations_routes  # noqa: E402
import routes.evaluations as evaluations_routes  # noqa: E402
import routes.ratings as ratings_routes  # noqa: E402
import routes.admin as admin_routes  # noqa: E402
import routes.auth as auth_routes  # noqa: E402

# Export service symbols for direct access and test monkeypatching
validate_generated_audio = synthesis_service.validate_generated_audio
execute_worker = synthesis_service.execute_worker
update_job = synthesis_service.update_job
evaluate_job = synthesis_service.evaluate_job
schedule_evaluation = synthesis_service.schedule_evaluation
auto_evaluate = synthesis_service.auto_evaluate
run_job = synthesis_service.run_job
delete_finished_job = synthesis_service.delete_finished_job
apply_retention = synthesis_service.apply_retention
retention_loop = synthesis_service.retention_loop
resolve_generation_reference = synthesis_service.resolve_generation_reference
_generate_impl = synthesis_service._generate_impl
plan_generation_jobs = synthesis_service.plan_generation_jobs
submit_job = synthesis_service.submit_job
_batch_experiment_impl = synthesis_service._batch_experiment_impl
_batch_generations = synthesis_service._batch_generations
csv_safe = synthesis_service.csv_safe

get_installed_reference = models_routes.get_installed_reference
store_reference = models_routes.store_reference
get_reference_profile = models_routes.get_reference_profile
batch = generations_routes.batch
models = models_routes.models
health = admin_routes.health
require_administrator = admin_routes.require_administrator

__all__ = [
    "app",
    "db",
    "ROOT",
    "DB",
    "DATA_LOCK",
    "ORIGINS",
    "MODEL_IDS",
    "LABELS",
    "POOL",
    "EXPORT_SLOTS",
    "EVAL_POOL",
    "MODEL_LOCKS",
    "MAX_PENDING",
    "TASKS",
    "ADMISSION_LOCK",
    "REPRODUCIBILITY_LOCK",
    "WORKERS",
    "JOB_CANCELLATIONS",
    "REFERENCE_AUDIO",
    "REFERENCE_DIR",
    "REFERENCE_TEXT",
    "artifact_storage",
    "direct_inference",
    "lifecycle",
    "schema",
    "security",
    "BatchRequest",
    "GenerateRequest",
    "LayoutRequest",
    "Login",
    "ModelControls",
    "PasswordReset",
    "RequestModel",
    "PrivateExportResponse",
    "GenerationConfig",
    "generation_config",
    "effective_parameters",
    "InferenceCancelled",
    "TerminationFailure",
    "WorkerPool",
    "DataLock",
    "SecurityHeaders",
    "validate_generated_audio",
    "execute_worker",
    "update_job",
    "evaluate_job",
    "schedule_evaluation",
    "auto_evaluate",
    "run_job",
    "delete_finished_job",
    "apply_retention",
    "retention_loop",
    "resolve_generation_reference",
    "_generate_impl",
    "plan_generation_jobs",
    "submit_job",
    "_batch_experiment_impl",
    "_batch_generations",
    "csv_safe",
    "get_installed_reference",
    "store_reference",
    "get_reference_profile",
    "batch",
    "models",
    "health",
    "require_administrator",
    "stored_audio",
    "require_reference",
    "mark_shared_reference",
    "install_backend_reference",
    "load_backend_reference",
    "process_isolation",
    "model_availability",
    "get_evaluator",
    "_startup_preload",
    "_evaluator",
    "_evaluator_lock",
    "_evaluation_jobs",
    "_eval_jobs_lock",
    "BodyLimitMiddleware",
]


@asynccontextmanager
async def lifespan(app):
    global WORKERS
    if WORKERS.abort.is_set():
        WORKERS = WorkerPool()
    security.validate_configuration()
    with db() as c:
        if security.production() and schema.missing_constraints(c):
            raise RuntimeError(
                "Legacy database requires offline schema.py --apply migration after a verified backup"
            )
    primary = DATA_LOCK.acquire()
    retention = None
    try:
        if primary:
            load_backend_reference()
        if primary and os.getenv("PRELOAD_MODELS", "1") == "1":
            threading.Thread(target=_startup_preload, daemon=True).start()
        if primary and os.getenv("RETENTION_DAYS", "0") != "0":
            retention = asyncio.create_task(retention_loop())
        yield
    finally:
        if retention is not None:
            retention.cancel()
            await asyncio.gather(retention, return_exceptions=True)
        if DATA_LOCK.begin_release():
            try:
                for task in list(TASKS.values()):
                    task.cancel()
                if TASKS:
                    await asyncio.gather(*list(TASKS.values()), return_exceptions=True)
                await asyncio.to_thread(WORKERS.close)
            finally:
                DATA_LOCK.finish_release()


app = FastAPI(
    title="Voice Comparison Studio",
    version="1.1.0",
    lifespan=lifespan,
    docs_url=None if security.production() else "/docs",
    redoc_url=None if security.production() else "/redoc",
    openapi_url=None if security.production() else "/openapi.json",
)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    return JSONResponse(
        {
            "detail": "Invalid request",
            "errors": [
                {
                    "field": ".".join(str(part) for part in item["loc"]),
                    "message": item["msg"],
                }
                for item in error.errors()
            ],
        },
        status_code=422,
    )


# Include Domain APIRouters
app.include_router(auth_routes.router)
app.include_router(models_routes.router)
app.include_router(generations_routes.router)
app.include_router(evaluations_routes.router)
app.include_router(ratings_routes.router)
app.include_router(admin_routes.router)


@app.middleware("http")
async def protect_api(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    user = None
    context = None
    try:
        if (
            security.production()
            and request.url.scheme != "https"
            and (
                request.client is None
                or request.client.host not in ("127.0.0.1", "::1")
            )
        ):
            return JSONResponse({"detail": "HTTPS required"}, status_code=400)
        if request.method != "OPTIONS" and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            own_origin = str(request.base_url).rstrip("/")
            mutable = request.method not in ("GET", "HEAD")
            if mutable and origin and origin not in ORIGINS and origin != own_origin:
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)

            def identify():
                with db() as c:
                    return security.identify(request, c)

            user, cookie_auth = await asyncio.to_thread(identify)
            public = request.url.path in (
                "/api/health",
                "/api/deployment",
                "/api/password-reset",
            ) or (
                request.url.path == "/api/session" and request.method in ("GET", "POST")
            )
            admin = request.url.path.startswith("/api/admin/")
            if admin and request.url.path not in (
                "/api/admin/reload-reference",
                "/api/admin/security-events",
            ):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            if not public and not admin and user is None:
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            if mutable and cookie_auth and request.headers.get("X-Studio-CSRF") != "1":
                return JSONResponse({"detail": "CSRF header required"}, status_code=403)
            if mutable and not public:
                source = request.client.host if request.client else "unknown"
                limit = (
                    6
                    if request.url.path
                    in ("/api/generations", "/api/batches", "/api/references/upload")
                    else 60
                )
                bucket = (
                    request.url.path
                    if request.url.path
                    in ("/api/generations", "/api/batches", "/api/references/upload")
                    else "mutations"
                )
                if not await asyncio.to_thread(
                    security.limiter.allow, (user or source, bucket), limit
                ):
                    return JSONResponse(
                        {"detail": "Rate limit exceeded"},
                        status_code=429,
                        headers={"Retry-After": "60"},
                    )
            if (
                request.method == "GET"
                and request.url.path.startswith("/api/exports/")
                and not await asyncio.to_thread(
                    security.limiter.allow, (user, "exports"), 10
                )
            ):
                return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        context = security.principal.set(user)
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request error id=%s", request_id)
        response = JSONResponse(
            {"detail": "Internal server error", "request_id": request_id},
            status_code=500,
        )
    finally:
        if context is not None:
            security.principal.reset(context)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if security.production():
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


class BodyLimitMiddleware:
    """Bound ASGI body bytes before multipart parsers or JSON allocate them."""

    def __init__(self, app):
        self.app = app
        self.upload_slots = asyncio.Semaphore(4)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/api/references/upload" and not scope.get(
            "studio_upload_admitted"
        ):
            if self.upload_slots.locked():
                return await JSONResponse(
                    {"detail": "Upload capacity reached"}, status_code=429
                )(scope, receive, send)
            async with self.upload_slots:
                return await self.__call__(
                    {**scope, "studio_upload_admitted": True}, receive, send
                )
        headers = dict(scope.get("headers", []))
        if scope["path"].startswith("/api/") and scope["method"] != "OPTIONS":
            public = scope["path"] in (
                "/api/health",
                "/api/deployment",
                "/api/password-reset",
            ) or (
                scope["path"] == "/api/session" and scope["method"] in ("GET", "POST")
            )
            if scope["path"].startswith("/api/admin/"):
                if scope["path"] not in (
                    "/api/admin/reload-reference",
                    "/api/admin/security-events",
                ):
                    return await JSONResponse({"detail": "Not found"}, status_code=404)(
                        scope, receive, send
                    )
                try:
                    require_administrator(headers.get(b"authorization", b"").decode())
                except HTTPException as error:
                    return await JSONResponse(
                        {"detail": error.detail}, status_code=error.status_code
                    )(scope, receive, send)
            elif not public:

                def identify():
                    with db() as c:
                        return security.identify(Request(scope), c)[0]

                if await asyncio.to_thread(identify) is None:
                    return await JSONResponse(
                        {"detail": "Unauthorized"}, status_code=401
                    )(scope, receive, send)
        maximum = (
            17 * 1024 * 1024
            if scope["path"] == "/api/references/upload"
            else 256 * 1024
        )
        try:
            if int(headers.get(b"content-length", b"0")) > maximum:
                return await JSONResponse(
                    {"detail": "Request too large"}, status_code=413
                )(scope, receive, send)
        except ValueError:
            return await JSONResponse(
                {"detail": "Invalid content length"}, status_code=400
            )(scope, receive, send)
        messages, size = [], 0
        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            while True:
                try:
                    message = await asyncio.wait_for(receive(), timeout=15)
                except TimeoutError:
                    return await JSONResponse(
                        {"detail": "Request body timed out"}, status_code=408
                    )(scope, receive, send)
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > maximum:
                    return await JSONResponse(
                        {"detail": "Request too large"}, status_code=413
                    )(scope, receive, send)
                messages.append(message)
                if not message.get("more_body", False):
                    break

        async def bounded_receive():
            return messages.pop(0) if messages else await receive()

        await self.app(scope, bounded_receive, send)


app.add_middleware(BodyLimitMiddleware)
app.add_middleware(SecurityHeaders)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Studio-CSRF"],
)

DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
else:

    @app.get("/", include_in_schema=False)
    def home():
        return {
            "service": "Voice Comparison Studio",
            "frontend": "http://localhost:5173",
            "message": "For one-port production hosting, run npm ci && npm run build in frontend, then restart the API.",
            "health": "/api/health",
            "docs": "/docs",
        }
