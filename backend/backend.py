"""Voice Comparison Studio API — unified single-environment server with all models and metrics."""

from __future__ import annotations
import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import math
import os
import shutil
import sqlite3
import threading
import tempfile
import time
import uuid
import wave
import zipfile
from typing import Literal
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict

load_dotenv(Path(__file__).parent / ".env", override=False)

import direct_inference
import security
import schema
import artifact_storage
from generation_config import GenerationConfig, current as generation_config, effective_parameters
from isolated_workers import WorkerPool, InferenceCancelled
from export_response import PrivateExportResponse
from security_headers import SecurityHeaders
from runtime_lock import DataLock

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


def process_isolation():
    return os.getenv("INFERENCE_ISOLATION", "process") == "process"

_evaluator = None
_evaluator_lock = threading.Lock()
_evaluation_jobs: set[str] = set()
_eval_jobs_lock = threading.Lock()


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
                WORKERS.get(model).call(None, timeout=float(os.getenv("MODEL_LOAD_TIMEOUT", "600")))
            except Exception:
                logger.warning("Preload unavailable model=%s", model)
        return
    direct_inference.preload_all_models()
    try:
        print("[evaluator] Loading Whisper, SpeechBrain ECAPA, and ScoreQ...", flush=True)
        get_evaluator()
        print("[evaluator] All metric evaluators ready.", flush=True)
    except Exception as exc:
        print(f"[evaluator] Preload warning: {exc}", flush=True)


@asynccontextmanager
async def lifespan(app):
    global WORKERS
    if WORKERS.abort.is_set():
        WORKERS = WorkerPool()
    security.validate_configuration()
    with db() as c:
        if security.production() and schema.missing_constraints(c):
            raise RuntimeError("Legacy database requires offline schema.py --apply migration after a verified backup")
    primary = DATA_LOCK.acquire()
    try:
        if primary:
            load_backend_reference()
        if primary and os.getenv("PRELOAD_MODELS", "1") == "1":
            threading.Thread(target=_startup_preload, daemon=True).start()
        yield
    finally:
        if DATA_LOCK.begin_release():
            try:
                for task in list(TASKS.values()):
                    task.cancel()
                if TASKS:
                    await asyncio.gather(*list(TASKS.values()), return_exceptions=True)
                await asyncio.to_thread(WORKERS.close)
            finally:
                DATA_LOCK.finish_release()


app = FastAPI(title="Voice Comparison Studio", version="1.1.0", lifespan=lifespan,
              docs_url=None if security.production() else "/docs",
              redoc_url=None if security.production() else "/redoc",
              openapi_url=None if security.production() else "/openapi.json")



@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Pydantic input values can contain passwords/tokens; never echo them.
    return JSONResponse({"detail": "Invalid request", "errors": [
        {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
        for item in error.errors()
    ]}, status_code=422)


@contextmanager
def db():
    connection = sqlite3.connect(DB, timeout=30)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


with db() as c:
    schema.initialize(c)


with db() as c:
    security.initialize(c)
    if not schema.missing_constraints(c):
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES (2,?)", (time.time(),))
    artifact_storage.initialize(c)
    c.execute("CREATE TABLE IF NOT EXISTS reference_versions (reference_id TEXT PRIMARY KEY REFERENCES references_audio(id), transcript TEXT NOT NULL, language TEXT NOT NULL)")
    c.execute("INSERT OR IGNORE INTO reference_versions SELECT reference_id,transcript,language FROM user_reference_profiles")
    c.execute("CREATE TABLE IF NOT EXISTS job_provenance (job_id TEXT PRIMARY KEY REFERENCES jobs(id), configuration TEXT NOT NULL)")
security.limiter.bind(DB)
if security.production():
    DB.chmod(0o600)


REFERENCE_DIR = Path(
    os.getenv("REFERENCE_DIR", Path(__file__).resolve().parent / "reference")
).resolve()
REFERENCE_AUDIO = REFERENCE_DIR / "reference.wav"
REFERENCE_TEXT = REFERENCE_DIR / "reference.txt"


def stored_audio(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(ROOT / "audio") or not resolved.is_file():
        raise HTTPException(404, "Audio unavailable")
    return resolved


def require_reference(c, reference_id):
    shared = c.execute("SELECT value FROM settings WHERE key=?", ("shared-reference:" + str(reference_id),)).fetchone()
    if not shared:
        security.require_owner(c, "reference", reference_id)


def mark_shared_reference(c, reference_id):
    c.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", ("shared-reference:" + reference_id, "true"))


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
        c.execute("INSERT OR IGNORE INTO reference_versions VALUES (?,?,?)", (ref_id, transcript, "English" if "_en" in str(REFERENCE_AUDIO) else "German"))
        mark_shared_reference(c, ref_id)
        c.execute(
            "INSERT INTO reference_profiles(id,reference_id,transcript,updated) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET reference_id=excluded.reference_id,transcript=excluded.transcript,updated=excluded.updated",
            (ref_id, transcript, time.time()),
        )
    return True


def load_backend_reference():
    with db() as c:
        for select, update in (
            ("SELECT id,path FROM references_audio WHERE path IS NOT NULL", "UPDATE references_audio SET path=? WHERE id=?"),
            ("SELECT id,output AS path FROM jobs WHERE output IS NOT NULL", "UPDATE jobs SET output=? WHERE id=?"),
        ):
            for row in c.execute(
                select
            ).fetchall():
                relocated = ROOT / "audio" / Path(row["path"]).name
                if not Path(row["path"]).is_file() and relocated.is_file():
                    c.execute(
                        update,
                        (str(relocated), row["id"]),
                    )
    install_backend_reference()
    with db() as c:
        interrupted = [row[0] for row in c.execute("SELECT id FROM jobs WHERE status IN ('pending','running')")]
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


def require_administrator(authorization):
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected or not hmac.compare_digest((authorization or "").encode(), ("Bearer "+expected).encode()):
        raise HTTPException(403, "Administrator credentials required")
    if os.getenv("AUTH_MODE") == "accounts":
        with db() as c:
            account = c.execute("SELECT role FROM accounts WHERE id=?", (security.principal.get(),)).fetchone()
        if not account or account["role"] != "admin":
            raise HTTPException(403, "Administrator account required")


@app.post("/api/admin/reload-reference")
def reload_backend_reference(authorization: str | None = Header(None)):
    require_administrator(authorization)
    return {"loaded": install_backend_reference()}


@app.get("/api/admin/security-events")
def security_events(authorization: str | None = Header(None)):
    require_administrator(authorization)
    with db() as c:
        rows=c.execute("SELECT created,kind,request_id FROM security_events ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(row) for row in rows]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ModelControls(RequestModel):
    temperature: float | None = Field(default=None, ge=0.1, le=1.5, allow_inf_nan=False)
    speech_rate: float | None = Field(default=None, ge=0.8, le=1.1, allow_inf_nan=False)


class GenerateRequest(RequestModel):
    use_cache: bool = True
    text: str = Field(min_length=1, max_length=2000)
    reference_id: str | None = None
    models: list[str] = Field(min_length=1, max_length=3)
    reference_text: str = Field(default="", max_length=4000)
    language: str = Field(default="German", pattern="^(German|English|de|en)$")
    temperature: float | None = Field(default=None, ge=0.1, le=1.5, allow_inf_nan=False)
    speech_rate: float | None = Field(default=None, ge=0.8, le=1.1, allow_inf_nan=False)
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    model_settings: dict[Literal["qwen3", "cosyvoice3", "xtts-v2"], ModelControls] = Field(default_factory=dict)


class BatchRequest(RequestModel):
    reference_id: str
    models: list[str] = Field(min_length=1, max_length=3)
    texts: list[str] = Field(min_length=1, max_length=100)
    repetitions: int = Field(default=1, ge=1, le=20)
    reference_text: str = Field(default="", max_length=4000)


class RatingRequest(RequestModel):
    job_id: str
    rater: str = Field(default="Evaluator", min_length=1, max_length=100)
    category: str = Field(pattern="^(naturalness|similarity|intelligibility)$")
    rating: int = Field(ge=1, le=5)


class LayoutRequest(RequestModel):
    active: list[str] = Field(max_length=3)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "inference": "direct-single-environment",
        "execution": "parallel",
        "reference_configured": REFERENCE_AUDIO.is_file(),
    }


@app.get("/api/models")
def models():
    result = []
    for model in MODEL_IDS:
        state = dict(direct_inference.model_availability(model))
        if security.production() and state.get("detail"):
            state["detail"] = "Model unavailable; contact the administrator"
        result.append({"id": model, "name": LABELS[model], **state,
                       "controls": {"temperature": model != "cosyvoice3", "speech_rate": True}})
    return result


def get_installed_reference(filename: str, text_filename: str):
    audio_path = REFERENCE_DIR / filename
    text_path = REFERENCE_DIR / text_filename
    if not audio_path.is_file():
        return None
    try:
        with wave.open(str(audio_path), "rb") as reader:
            dur = round(reader.getnframes() / reader.getframerate(), 2)
            sr = reader.getframerate()
            ch = reader.getnchannels()
    except Exception:
        return None
    data = audio_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    transcript = text_path.read_text(encoding="utf-8").strip() if text_path.is_file() else ""
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
            shutil.copy2(audio_path, target)
            c.execute(
                "INSERT INTO references_audio VALUES (?,?,?,?,?)",
                (ref_id, digest, filename, str(target), time.time()),
            )
        c.execute("INSERT OR IGNORE INTO reference_versions VALUES (?,?,?)", (ref_id, transcript, "English" if "_en" in filename else "German"))
        mark_shared_reference(c, ref_id)
    return {
        "id": ref_id,
        "filename": filename,
        "transcript": transcript,
        "duration_s": dur,
        "sample_rate": sr,
        "channels": ch,
    }


@app.get("/api/reference-profile")
def get_reference_profile(language: str = "German"):
    language = "English" if language.lower() in ("english", "en") else "German"
    with db() as c:
        profile = c.execute("SELECT reference_id,transcript FROM user_reference_profiles WHERE owner_id=? AND language=?",
                            (security.owner(), language)).fetchone()
        if profile:
            ref = c.execute("SELECT id,filename FROM references_audio WHERE id=?", (profile["reference_id"],)).fetchone()
            return {"reference": dict(ref) | {"transcript": profile["transcript"]}, "language": language}
    if language.lower() in ("english", "en"):
        ref = get_installed_reference("reference_en.wav", "reference_en.txt")
        return {
            "reference": ref,
            "language": "English",
            "expected_audio": "backend/reference/reference_en.wav",
            "expected_text": "backend/reference/reference_en.txt",
        }
    else:
        ref = get_installed_reference("reference.wav", "reference.txt")
        if not ref:
            with db() as c:
                row = c.execute(
                    "SELECT reference_id,transcript FROM reference_profiles WHERE id=1"
                ).fetchone()
                if row:
                    try:
                        require_reference(c, row["reference_id"])
                    except HTTPException:
                        row = None
                if row:
                    db_ref = c.execute(
                        "SELECT id,filename,path FROM references_audio WHERE id=?",
                        (row["reference_id"],),
                    ).fetchone()
                    if db_ref and Path(db_ref["path"]).exists():
                        ref = {
                            "id": db_ref["id"],
                            "filename": db_ref["filename"],
                            "transcript": row["transcript"] or "",
                        }
        return {
            "reference": ref,
            "language": "German",
            "expected_audio": "backend/reference/reference.wav",
            "expected_text": "backend/reference/reference.txt",
        }


@app.post("/api/references/upload")
async def upload_reference(
    file: UploadFile = File(...),
    transcript: str = Form(""),
    language: str = Form("German"),
):
    aliases = {"German": "German", "de": "German", "English": "English", "en": "English"}
    if language not in aliases:
        raise HTTPException(422, "Unsupported reference language")
    language = aliases[language]
    if not file.filename or not file.filename.lower().endswith(".wav"):
        raise HTTPException(400, "Reference must be a .wav file")
    if len(transcript) > 4000 or "\x00" in transcript:
        raise HTTPException(422, "Invalid reference transcript")
    content = await file.read(16 * 1024 * 1024 + 1)
    if len(content) > 16 * 1024 * 1024:
        raise HTTPException(413, "Reference upload exceeds 16 MiB")
    try:
        with wave.open(io.BytesIO(content), "rb") as reader:
            frames, rate = reader.getnframes(), reader.getframerate()
            channels, width = reader.getnchannels(), reader.getsampwidth()
            if not 8000 <= rate <= 96000 or channels not in (1, 2) or width not in (1, 2, 3, 4):
                raise ValueError("Unsupported audio format")
            if not 0.25 <= frames / rate <= 120 or reader.getcomptype() != "NONE":
                raise ValueError("Unsupported audio duration or encoding")
            if len(reader.readframes(frames)) != frames * channels * width:
                raise ValueError("Truncated audio")
    except (wave.Error, EOFError, ValueError, ZeroDivisionError):
        raise HTTPException(400, "Invalid PCM WAV reference")
    reference_id = str(uuid.uuid4())
    target = ROOT / "audio" / f"{reference_id}.wav"
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        artifact_storage.capacity(c, ROOT, security.owner(), len(content))
        count = c.execute("SELECT count(*) FROM resource_owners WHERE kind='reference' AND owner_id=?", (security.owner(),)).fetchone()[0]
        if count >= 50:
            raise HTTPException(429, "Reference storage quota reached")
        target.write_bytes(content)
        try:
            c.execute("INSERT INTO references_audio VALUES (?,?,?,?,?)",
                      (reference_id, hashlib.sha256(content).hexdigest(), "reference.wav", str(target), time.time()))
            security.claim(c, "reference", reference_id)
            artifact_storage.record(c, target, security.owner(), "reference")
            c.execute("INSERT INTO reference_versions VALUES (?,?,?)", (reference_id, transcript.strip(), language))
            c.execute("INSERT INTO user_reference_profiles VALUES (?,?,?,?) ON CONFLICT(owner_id,language) DO UPDATE SET reference_id=excluded.reference_id,transcript=excluded.transcript",
                      (security.owner(), language, reference_id, transcript.strip()))
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return get_reference_profile(language)


@app.get("/api/references/{reference_id}/audio")
def reference_audio(reference_id: str):
    with db() as c:
        require_reference(c, reference_id)
        row = c.execute(
            "SELECT path FROM references_audio WHERE id=?", (reference_id,)
        ).fetchone()
    if not row or not Path(row["path"]).is_file():
        raise HTTPException(
            404, "Reference file unavailable. Check server reference configuration."
        )
    return FileResponse(stored_audio(row["path"]), media_type="audio/wav")


def update_job(job_id, status, output=None, error=None, runtime_seconds=None):
    now = time.time()
    with db() as c:
        if status not in ("pending", "running"):
            if status == "complete" and output:
                own = c.execute("SELECT owner_id FROM resource_owners WHERE kind='job' AND resource_id=?", (job_id,)).fetchone()
                if own:
                    artifact_storage.record(c, output, own["owner_id"], "output")
            c.execute("DELETE FROM storage_reservations WHERE id=?", (job_id,))
        if status == "running":
            c.execute(
                "UPDATE jobs SET status=?,started_at=?,updated=? WHERE id=?",
                (status, now, now, job_id),
            )
        else:
            c.execute(
                "UPDATE jobs SET status=?,output=?,error=?,updated=?,runtime_seconds=? WHERE id=?",
                (status, output, error, now, runtime_seconds, job_id),
            )


def execute_worker(
    job_id, model_id, text, reference_path, reference_text, language="German", config=GenerationConfig()
):
    output = ROOT / "audio" / f"{job_id}.wav"
    try:
        t0 = time.perf_counter()
        if process_isolation():
            output_path, elapsed, first_audio = WORKERS.get(model_id).call(
                (text, reference_path, reference_text, str(output), language, config),
                timeout=float(os.getenv("GENERATION_TIMEOUT_SECONDS", "600")),
                cancelled=JOB_CANCELLATIONS.get(job_id),
            )
            if first_audio is not None:
                from models import record_timing
                record_timing(output, "first_audio_ms", first_audio)
            validate_generated_audio(output)
            return output_path, elapsed
        from models import _request_language
        token = _request_language.set(language)
        config_token = generation_config.set(config)
        from contextlib import nullcontext
        guard = REPRODUCIBILITY_LOCK if os.getenv("REPRODUCIBLE") == "1" else nullcontext()
        try:
            with guard:
                if config.seed is not None:
                    import random
                    import numpy as np
                    import torch
                    random.seed(config.seed)
                    np.random.seed(config.seed)
                    torch.manual_seed(config.seed)
                    if torch.cuda.is_available():
                        torch.cuda.manual_seed_all(config.seed)
                    torch.use_deterministic_algorithms(True)
                direct_inference.generate_audio(model_id, text, Path(reference_path), reference_text, output)
        finally:
            _request_language.reset(token)
            generation_config.reset(config_token)
        t_elapsed = time.perf_counter() - t0
        validate_generated_audio(output)
        return str(output), t_elapsed
    except Exception:
        output.unlink(missing_ok=True)
        raise


def evaluate_job(job_id: str):
    try:
        with db() as c:
            c.execute(
                "UPDATE evaluation_runs SET status='running',updated=? WHERE job_id=?",
                (time.time(), job_id),
            )
            row = c.execute(
                "SELECT jobs.output,jobs.runtime_seconds,jobs.created,jobs.updated,batches.text,references_audio.path reference FROM jobs JOIN batches ON batches.id=jobs.batch_id JOIN references_audio ON batches.reference_id=references_audio.id WHERE jobs.id=? AND jobs.status=?",
                (job_id, "complete"),
            ).fetchone()
        if not row or not row["output"]:
            raise RuntimeError("Recording missing or job not complete")
        evaluator = None if process_isolation() else get_evaluator()
        with _evaluator_lock:
            elapsed = (
                row["runtime_seconds"]
                if row["runtime_seconds"] is not None
                else max(0, row["updated"] - row["created"])
            )
            if process_isolation():
                metrics, errors = WORKERS.get("evaluation").call(
                    (row["output"], row["reference"], row["text"], elapsed),
                    timeout=float(os.getenv("EVALUATION_TIMEOUT_SECONDS", "600")),
                )
            else:
                metrics = evaluator.evaluate(row["output"], row["reference"], row["text"], elapsed)
                errors = dict(evaluator.errors)
                evaluator.errors.clear()
        with db() as c:
            c.execute("DELETE FROM metrics WHERE job_id=?", (job_id,))
            for name, value, method in metrics:
                if not math.isfinite(float(value)):
                    continue
                c.execute(
                    "INSERT OR REPLACE INTO metrics VALUES (?,?,?,?)",
                    (job_id, name, float(value), method),
                )
            c.execute("DELETE FROM evaluation_errors WHERE job_id=?", (job_id,))
            for name, reason in errors.items():
                c.execute(
                    "INSERT OR REPLACE INTO evaluation_errors VALUES (?,?,?)",
                    (job_id, name, "Evaluator unavailable; consult server logs"),
                )
            c.execute(
                "UPDATE evaluation_runs SET status=?,error=?,updated=? WHERE job_id=?",
                ("partial" if errors else "completed", None, time.time(), job_id),
            )
    except Exception as exc:
        logger.exception("Evaluation failed job=%s", job_id)
        with db() as c:
            c.execute(
                "INSERT INTO evaluation_runs VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,error=excluded.error,updated=excluded.updated",
                (job_id, "failed", "Evaluation failed; contact the administrator with the job ID", time.time()),
            )
    finally:
        with _eval_jobs_lock:
            _evaluation_jobs.discard(job_id)


def schedule_evaluation(job_id: str, force: bool = False) -> str:
    with _eval_jobs_lock:
        with db() as c:
            row = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Unknown job")
            if row["status"] != "complete":
                raise HTTPException(409, "Generation not complete")
            existing = c.execute(
                "SELECT status FROM evaluation_runs WHERE job_id=?", (job_id,)
            ).fetchone()
            if job_id in _evaluation_jobs:
                return "running"
            if (
                not force
                and existing
                and existing["status"] in ("completed", "partial")
            ):
                return existing["status"]
            if len(_evaluation_jobs) >= MAX_PENDING:
                raise HTTPException(429, "Evaluation queue is full")
            c.execute(
                "INSERT INTO evaluation_runs VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,error=NULL,updated=excluded.updated",
                (job_id, "pending", None, time.time()),
            )
        _evaluation_jobs.add(job_id)
    EVAL_POOL.submit(evaluate_job, job_id)
    return "scheduled"


def auto_evaluate(job_id):
    try:
        schedule_evaluation(job_id)
    except Exception as exc:
        with db() as c:
            c.execute(
                "INSERT OR REPLACE INTO evaluation_runs VALUES (?,?,?,?)",
                (job_id, "failed", "Evaluation failed; contact the administrator with the job ID", time.time()),
            )


def validate_generated_audio(output):
    if output.stat().st_size > int(os.getenv("MAX_OUTPUT_BYTES", "67108864")):
        raise RuntimeError("Generated audio exceeds storage limit")
    with wave.open(str(output), "rb") as reader:
        count, rate = reader.getnframes(), reader.getframerate()
        if not 8000 <= rate <= 96000 or reader.getnchannels() not in (1, 2):
            raise RuntimeError("Invalid generated audio format")
        if not count or count/rate > float(os.getenv("MAX_OUTPUT_SECONDS", "600")):
            raise RuntimeError("Generated audio exceeds duration limit")
        if len(reader.readframes(count)) != count*reader.getnchannels()*reader.getsampwidth():
            raise RuntimeError("Generated audio is truncated")


async def run_job(job_id, model, text, path, reference_text, language="German", config=GenerationConfig()):
    future = None
    try:
        async with MODEL_LOCKS[model]:
            update_job(job_id, "running")
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(POOL, execute_worker, job_id, model, text, path, reference_text, language, config)
            output, runtime = await asyncio.shield(future)
            update_job(job_id, "complete", output, runtime_seconds=runtime)
            if os.environ.get("AUTO_EVALUATE", "1") == "1":
                auto_evaluate(job_id)
    except asyncio.CancelledError:
        JOB_CANCELLATIONS[job_id].set()
        if future is not None:
            try:
                await asyncio.shield(future)
            except Exception:
                pass
        (ROOT / "audio" / f"{job_id}.wav").unlink(missing_ok=True)
        update_job(job_id, "cancelled", error="Generation cancelled")
        raise
    except InferenceCancelled:
        update_job(job_id, "cancelled", error="Generation cancelled")
    except TimeoutError:
        update_job(job_id, "failed", error="Generation exceeded its configured time limit")
    except Exception:
        logger.exception("Inference failed job=%s", job_id)
        update_job(job_id, "failed", error="Inference failed; contact the administrator with the job ID")
    finally:
        JOB_CANCELLATIONS.pop(job_id, None)


@app.delete("/api/jobs/{job_id}")
async def cancel_job(job_id: str):
    with db() as c:
        security.require_owner(c, "job", job_id)
        row = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row["status"] not in ("pending", "running"):
        raise HTTPException(409, "Only active jobs can be cancelled")
    task = TASKS.get(job_id)
    if task is None:
        raise HTTPException(409, "Job is no longer active in this process")
    if not task.cancel():
        raise HTTPException(409, "Job already finished")
    await asyncio.gather(task, return_exceptions=True)
    return {"id": job_id, "status": "cancelled"}


@app.post("/api/generations", status_code=202)
async def generate(request: GenerateRequest):
    async with ADMISSION_LOCK:
        return await _generate_impl(request)


async def _generate_impl(request: GenerateRequest, deferred=None):
    if request.temperature is not None and "cosyvoice3" in request.models:
        raise HTTPException(422, "CosyVoice temperature is unsupported; leave temperature unset for comparison")
    if request.seed is not None and os.getenv("REPRODUCIBLE") != "1":
        raise HTTPException(422, "Seeded requests require REPRODUCIBLE=1 (serialized inference)")
    if any(model not in request.models for model in request.model_settings):
        raise HTTPException(422, "Settings were supplied for an unselected model")
    if request.model_settings.get("cosyvoice3", ModelControls()).temperature is not None:
        raise HTTPException(422, "CosyVoice temperature is unsupported")
    configurations = {}
    for model in request.models:
        custom = request.model_settings.get(model, ModelControls())
        config = GenerationConfig(custom.temperature if custom.temperature is not None else request.temperature,
                                  custom.speech_rate if custom.speech_rate is not None else request.speech_rate, request.seed)
        configurations[model] = config
    if not request.text.strip():
        raise HTTPException(422, "Enter a nonempty speech script")
    if len(TASKS) + len(request.models) > MAX_PENDING:
        raise HTTPException(
            429, "Generation queue is full; retry after current jobs finish"
        )
    if len(set(request.models)) != len(request.models) or any(
        m not in MODEL_IDS for m in request.models
    ):
        raise HTTPException(422, "Unsupported or duplicate models")
    req_lang = request.language or os.getenv("GENERATION_LANGUAGE", "German")
    selected_reference = request.reference_id
    effective_transcript = request.reference_text
    if not selected_reference:
        profile = get_reference_profile(req_lang).get("reference")
        if profile:
            selected_reference = profile["id"]
            effective_transcript = profile.get("transcript", "")
    with db() as c:
        if not selected_reference:
            if req_lang.lower() in ("english", "en"):
                en_ref = get_installed_reference("reference_en.wav", "reference_en.txt")
                if en_ref:
                    selected_reference = en_ref["id"]
                    effective_transcript = en_ref["transcript"]
                else:
                    raise HTTPException(
                        422,
                        "English reference audio not found. Please place reference_en.wav and reference_en.txt in backend/reference/",
                    )
            else:
                profile = c.execute(
                    "SELECT reference_id,transcript FROM reference_profiles WHERE id=1"
                ).fetchone()
                selected_reference = profile["reference_id"] if profile else None
                effective_transcript = profile["transcript"] if profile else ""
        require_reference(c, selected_reference)
        ref = c.execute(
            "SELECT * FROM references_audio WHERE id=?", (selected_reference,)
        ).fetchone()
    if not effective_transcript:
        with db() as c:
            version = c.execute("SELECT transcript,language FROM reference_versions WHERE reference_id=?", (selected_reference,)).fetchone()
        if version:
            effective_transcript = version["transcript"]
    if "cosyvoice3" in request.models and not effective_transcript.strip():
        raise HTTPException(422, "CosyVoice requires a transcript for the selected reference")
    if not ref or not Path(ref["path"]).is_file():
        expected_wav = "reference_en.wav" if req_lang.lower() in ("english", "en") else "reference.wav"
        raise HTTPException(422, f"Configure backend/reference/{expected_wav} first")

    available = await asyncio.gather(
        *(
            asyncio.to_thread(direct_inference.model_availability, m)
            for m in request.models
        )
    )
    if len(TASKS) + len(request.models) > MAX_PENDING:
        raise HTTPException(429, "Generation queue is full")
    unavailable = [
        LABELS[m] + ": " + str(status.get("detail"))
        for m, status in zip(request.models, available)
        if not status["configured"]
    ]
    if unavailable:
        raise HTTPException(503, "Selected model unavailable; contact the administrator" if security.production() else "; ".join(unavailable))
    now = time.time()
    batch_id = str(uuid.uuid4())
    planned = []
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        outstanding = c.execute("SELECT count(*) FROM jobs WHERE status IN ('pending','running')").fetchone()[0]
        personal = c.execute("SELECT count(*) FROM jobs j JOIN resource_owners o ON o.resource_id=j.id AND o.kind='job' WHERE o.owner_id=? AND j.status IN ('pending','running')", (security.owner(),)).fetchone()[0]
        if outstanding + len(request.models) > MAX_PENDING or personal + len(request.models) > int(os.getenv("MAX_USER_JOBS", "6")):
            raise HTTPException(429, "Generation capacity reached")
        total = c.execute("SELECT count(*) FROM resource_owners WHERE kind='job' AND owner_id=?", (security.owner(),)).fetchone()[0]
        if total + len(request.models) > int(os.getenv("MAX_STORED_JOBS", "1000")):
            raise HTTPException(429, "Stored job quota reached; contact the administrator")
        c.execute(
            "INSERT INTO batches VALUES (?,?,?,?)",
            (batch_id, request.text.strip(), selected_reference, now),
        )
        security.claim(c, "generation", batch_id)
        for model in request.models:
            config = configurations[model]
            effective = effective_parameters(model, config)
            key = hashlib.sha256(
                json.dumps(
                    [
                        model,
                        security.owner(),
                        effective,
                        os.getenv("GIT_REVISION", "unknown"),
                        ref["sha256"],
                        request.text.strip(),
                        effective_transcript,
                        req_lang,
                        "native-speed-batching-v3",
                        os.getenv("MODEL_CACHE_VERSION", "6"),
                        {key: value for key, value in os.environ.items()
                         if key.startswith(("QWEN_", "XTTS_", "COSY_"))
                         or key in ("MAX_INTERNAL_PAUSE_MS", "MODEL_DEVICE", "SPEECH_RATE",
                                        "COMMA_PAUSE_MS", "SENTENCE_PAUSE_MS",
                                        "PARAGRAPH_PAUSE_MS", "CONTINUATION_PAUSE_MS")},
                        os.getenv("QWEN_MODEL", ""),
                        os.getenv("COSY_MODEL_DIR", ""),
                        os.getenv("XTTS_MODEL", ""),
                    ],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            prior = c.execute(
                "SELECT output,runtime_seconds FROM jobs WHERE cache_key=? AND status='complete' AND output IS NOT NULL ORDER BY created DESC LIMIT 1",
                (key,),
            ).fetchone()
            if not request.use_cache:
                prior = None
            job_id = str(uuid.uuid4())
            out = prior["output"] if prior and Path(prior["output"]).exists() else None
            c.execute(
                "INSERT INTO jobs (id,batch_id,model_id,status,output,error,created,updated,cache_key,runtime_seconds) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    batch_id,
                    model,
                    "complete" if out else "pending",
                    out,
                    None,
                    now,
                    now,
                    key,
                    prior["runtime_seconds"] if out else None,
                ),
            )
            security.claim(c, "job", job_id)
            if not out:
                artifact_storage.reserve(c, ROOT, security.owner(), job_id, int(os.getenv("MAX_OUTPUT_BYTES", "67108864")))
            provenance = {"model_id": model, "model_revision": os.getenv({"qwen3":"QWEN_REVISION", "cosyvoice3":"COSY_REVISION", "xtts-v2":"XTTS_REVISION"}[model], "unverified"),
                          "reference_sha256": ref["sha256"], "transcript_sha256": hashlib.sha256(effective_transcript.encode()).hexdigest(),
                          "language": req_lang, "temperature": config.temperature, "speech_rate": config.speech_rate,
                          "effective_parameters": effective,
                          "seed": request.seed, "software_revision": os.getenv("GIT_REVISION", "unknown"),
                          "device": os.getenv("MODEL_DEVICE", "auto"),
                          "environment_controls": {k:v for k,v in os.environ.items() if k in (
                              "QWEN_TEMPERATURE", "XTTS_TEMPERATURE", "QWEN_SPEECH_RATE", "XTTS_SPEECH_RATE", "COSY_SPEECH_RATE",
                              "SPEECH_RATE", "COMMA_PAUSE_MS", "SENTENCE_PAUSE_MS", "PARAGRAPH_PAUSE_MS", "CONTINUATION_PAUSE_MS", "MAX_INTERNAL_PAUSE_MS")}}
            c.execute("INSERT INTO job_provenance VALUES (?,?)", (job_id, json.dumps(provenance)))
            planned.append((job_id, model, out))
    for job_id, model, out in planned:
        if out and os.environ.get("AUTO_EVALUATE", "1") == "1":
            auto_evaluate(job_id)
        if not out:
            args = (job_id, model, request.text.strip(), ref["path"], effective_transcript, req_lang, configurations[model])
            if deferred is not None:
                deferred.append(args)
            else:
                submit_job(args)
    return {
        "id": batch_id,
        "jobs": [
            {
                "id": j,
                "model_id": m,
                "status": "complete" if out else "pending",
                "cached": bool(out),
            }
            for j, m, out in planned
        ],
    }


def submit_job(args):
    job_id = args[0]
    JOB_CANCELLATIONS[job_id] = threading.Event()
    task = asyncio.create_task(run_job(*args))
    TASKS[job_id] = task
    def finished(done):
        TASKS.pop(job_id, None)
        JOB_CANCELLATIONS.pop(job_id, None)
        if done.cancelled():
            with db() as c:
                c.execute("UPDATE jobs SET status='cancelled',error='Generation cancelled',updated=? WHERE id=? AND status IN ('pending','running')", (time.time(), job_id))
                c.execute("DELETE FROM storage_reservations WHERE id=?", (job_id,))
    task.add_done_callback(finished)


@app.get("/api/generations/{batch_id}")
def batch(batch_id: str):
    with db() as c:
        security.require_owner(c, "generation", batch_id)
        b = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not b:
            raise HTTPException(404, "Batch not found")
        jobs = c.execute(
            "SELECT id,model_id,status,error,output,created,updated,started_at,runtime_seconds FROM jobs WHERE batch_id=? ORDER BY rowid",
            (batch_id,),
        ).fetchall()
    return {
        "id": batch_id,
        "text": b["text"],
        "reference_id": b["reference_id"],
        "jobs": [
            dict(j)
            | {
                "audio_url": f"/api/recordings/{j['id']}/audio"
                if j["output"]
                else None,
                "output": None,
            }
            for j in jobs
        ],
    }


@app.get("/api/recordings/{job_id}/audio")
def recording(job_id: str):
    with db() as c:
        security.require_owner(c, "job", job_id)
        row = c.execute(
            "SELECT output FROM jobs WHERE id=? AND status='complete'", (job_id,)
        ).fetchone()
    if not row or not row["output"] or not Path(row["output"]).exists():
        raise HTTPException(404, "Recording not available")
    return FileResponse(stored_audio(row["output"]), media_type="audio/wav", filename=f"{job_id}.wav")


@app.get("/api/experiments")
def experiments(limit: int = 30):
    with db() as c:
        rows = c.execute(
            "SELECT b.* FROM batches b JOIN resource_owners o ON o.resource_id=b.id AND o.kind='generation' WHERE o.owner_id=? ORDER BY b.created DESC LIMIT ?",
            (security.owner(), min(max(limit, 1), 100)),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/evaluations/{job_id}")
def evaluations(job_id: str):
    with db() as c:
        # Read metrics and run state from one SQLite snapshot, avoiding a
        # completed status paired with the preceding empty metric set.
        c.execute("BEGIN")
        security.require_owner(c, "job", job_id)
        exists = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "Unknown job")
        results = c.execute(
            "SELECT name,value,evaluator FROM metrics WHERE job_id=?", (job_id,)
        ).fetchall()
        run = c.execute(
            "SELECT status,error FROM evaluation_runs WHERE job_id=?", (job_id,)
        ).fetchone()
        errors = c.execute(
            "SELECT name,reason FROM evaluation_errors WHERE job_id=?", (job_id,)
        ).fetchall()
    state = run["status"] if run else ("completed" if results else "not_evaluated")
    return {
        "job_id": job_id,
        "metrics": [dict(r) for r in results],
        "status": state,
        "error": run["error"] if run else None,
        "errors": [dict(e) for e in errors],
    }


@app.post("/api/evaluations/{job_id}", status_code=202)
async def start_evaluation(job_id: str):
    with db() as c:
        security.require_owner(c, "job", job_id)
    status = schedule_evaluation(job_id)
    return {"status": status, "job_id": job_id}


@app.get("/api/evaluation-errors/{job_id}")
def evaluation_errors(job_id: str):
    with db() as c:
        security.require_owner(c, "job", job_id)
        rows = c.execute(
            "SELECT name,reason FROM evaluation_errors WHERE job_id=?", (job_id,)
        ).fetchall()
    return [dict(x) for x in rows]


@app.get("/api/workspace")
def workspace():
    with db() as c:
        row = c.execute("SELECT value FROM user_preferences WHERE owner_id=?", (security.owner(),)).fetchone()
    return json.loads(row["value"]) if row else {"active": list(MODEL_IDS)}


@app.patch("/api/workspace")
def save_workspace(body: LayoutRequest):
    if len(body.active) != len(set(body.active)) or any(
        m not in MODEL_IDS for m in body.active
    ):
        raise HTTPException(422, "Invalid model layout")
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO user_preferences VALUES (?,?)",
            (security.owner(), json.dumps(body.model_dump())),
        )
    return body


@app.post("/api/ratings")
def add_rating(body: RatingRequest):
    with db() as c:
        security.require_owner(c, "job", body.job_id)
        if not c.execute(
            "SELECT id FROM jobs WHERE id=? AND status='complete'", (body.job_id,)
        ).fetchone():
            raise HTTPException(404, "Completed recording not found")
        c.execute(
            "INSERT OR REPLACE INTO human_ratings VALUES (?,?,?,?)",
            (body.job_id, body.rater, body.category, body.rating),
        )
    return {"status": "recorded"}


@app.get("/api/ratings/{job_id}")
def ratings(job_id: str):
    with db() as c:
        security.require_owner(c, "job", job_id)
        rows = c.execute(
            "SELECT rater,category,rating FROM human_ratings WHERE job_id=?", (job_id,)
        ).fetchall()
    return [dict(x) for x in rows]


@app.get("/api/agreement")
def agreement(category: str = "naturalness"):
    with db() as c:
        rows = c.execute(
            "SELECT r.job_id,r.rater,r.rating FROM human_ratings r JOIN resource_owners o ON o.resource_id=r.job_id AND o.kind='job' WHERE r.category=? AND o.owner_id=?",
            (category, security.owner()),
        ).fetchall()
    raters = sorted(set(x["rater"] for x in rows))
    by = {(x["job_id"], x["rater"]): x["rating"] for x in rows}
    if len(raters) < 2:
        return {"status": "insufficient_raters", "kappa": None}
    a, b = raters[:2]
    common = sorted(
        set(x["job_id"] for x in rows if x["rater"] == a)
        & set(x["job_id"] for x in rows if x["rater"] == b)
    )
    if len(common) < 2:
        return {"status": "insufficient_shared_items", "kappa": None}
    try:
        from sklearn.metrics import cohen_kappa_score

        scores = [by[(j, a)] for j in common]
        other = [by[(j, b)] for j in common]
        kappa = float(cohen_kappa_score(scores, other))
        weighted = float(cohen_kappa_score(scores, other, weights="quadratic"))
        return {
            "status": "completed" if math.isfinite(kappa) else "undefined",
            "kappa": kappa if math.isfinite(kappa) else None,
            "weighted_kappa": weighted if math.isfinite(weighted) else None,
            "raters": [a, b],
            "items": len(common),
        }
    except Exception as e:
        return {"status": "unavailable", "reason": str(e), "kappa": None}


@app.post("/api/batches", status_code=202)
async def batch_experiment(body: BatchRequest):
    async with ADMISSION_LOCK:
        return await _batch_experiment_impl(body)


async def _batch_experiment_impl(body: BatchRequest):
    if len(body.models) != len(set(body.models)) or any(
        x not in MODEL_IDS for x in body.models
    ):
        raise HTTPException(422, "Unknown model")
    if any(not x.strip() or len(x) > 2000 for x in body.texts):
        raise HTTPException(422, "Invalid script")
    with db() as c:
        require_reference(c, body.reference_id)
        if not c.execute(
            "SELECT id FROM references_audio WHERE id=?", (body.reference_id,)
        ).fetchone():
            raise HTTPException(404, "Reference not found")
    if (
        len(body.models) * len(body.texts) * body.repetitions + len(TASKS)
        > MAX_PENDING
    ):
        raise HTTPException(429, "Batch exceeds available queue capacity")
    parent = str(uuid.uuid4())
    with db() as c:
        c.execute(
            "INSERT INTO batch_runs(id,params,created,status) VALUES (?,?,?,'planning')",
            (parent, body.model_dump_json(), time.time()),
        )
        security.claim(c, "batch", parent)
    generated, deferred = [], []
    try:
        for _ in range(body.repetitions):
            for text in body.texts:
                result = await _generate_impl(
                    GenerateRequest(text=text, reference_id=body.reference_id, models=body.models,
                                    reference_text=body.reference_text, use_cache=False),
                    deferred=deferred,
                )
                generated.append(result["id"])
                with db() as c:
                    c.execute("INSERT INTO batch_generations VALUES (?,?)", (parent, result["id"]))
    except BaseException:
        # No synthesis starts until every child has passed validation/admission.
        with db() as c:
            c.execute("DELETE FROM batch_generations WHERE batch_id=?", (parent,))
            for generation_id in generated:
                jobs = [r[0] for r in c.execute("SELECT id FROM jobs WHERE batch_id=?", (generation_id,))]
                for job_id in jobs:
                    c.execute("DELETE FROM storage_reservations WHERE id=?", (job_id,))
                    c.execute("DELETE FROM job_provenance WHERE job_id=?", (job_id,))
                    c.execute("DELETE FROM resource_owners WHERE kind='job' AND resource_id=?", (job_id,))
                c.execute("DELETE FROM jobs WHERE batch_id=?", (generation_id,))
                c.execute("DELETE FROM batches WHERE id=?", (generation_id,))
                c.execute("DELETE FROM resource_owners WHERE kind='generation' AND resource_id=?", (generation_id,))
            c.execute("DELETE FROM batch_generations WHERE batch_id=?", (parent,))
            c.execute("DELETE FROM batch_runs WHERE id=?", (parent,))
            c.execute("DELETE FROM resource_owners WHERE kind='batch' AND resource_id=?", (parent,))
        raise
    with db() as c:
        c.execute("UPDATE batch_runs SET status='ready' WHERE id=?", (parent,))
    for args in deferred:
        submit_job(args)
    return {
        "id": parent,
        "generation_ids": generated,
        "total_jobs": len(generated) * len(body.models),
    }


def _batch_generations(batch_id):
    with db() as c:
        rows = c.execute(
            "SELECT generation_id FROM batch_generations WHERE batch_id=? ORDER BY rowid",
            (batch_id,),
        ).fetchall()
    return [batch(row["generation_id"]) for row in rows]


@app.get("/api/batches/{batch_id}")
def batch_status(batch_id: str):
    with db() as c:
        security.require_owner(c, "batch", batch_id)
        b = c.execute("SELECT * FROM batch_runs WHERE id=?", (batch_id,)).fetchone()
        if not b:
            raise HTTPException(404, "Batch not found")
    return {
        "id": batch_id,
        "created": b["created"],
        "configuration": json.loads(b["params"]),
        "status": b["status"],
        "generations": _batch_generations(batch_id),
    }


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value


@app.get("/api/exports/{batch_id}")
def export_batch(batch_id: str):
    with db() as c:
        security.require_owner(c, "generation", batch_id)
        rows = c.execute(
            "SELECT jobs.*,batches.text,batches.reference_id FROM jobs JOIN batches ON jobs.batch_id=batches.id WHERE batch_id=?",
            (batch_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(404, "Experiment not found")
        provenance = [dict(x) for x in c.execute("SELECT p.* FROM job_provenance p JOIN jobs j ON j.id=p.job_id WHERE j.batch_id=?", (batch_id,))]
        metrics = c.execute(
            "SELECT * FROM metrics WHERE job_id IN (SELECT id FROM jobs WHERE batch_id=?)",
            (batch_id,),
        ).fetchall()
    if not EXPORT_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Export capacity reached")
    exports = ROOT / "exports"
    exports.mkdir(mode=0o700, exist_ok=True)
    identifier = "export:" + str(uuid.uuid4())
    target = None
    released = False
    def release():
        nonlocal released
        if released:
            return
        released = True
        if target is not None:
            target.unlink(missing_ok=True)
        with db() as c:
            c.execute("DELETE FROM storage_reservations WHERE id=?", (identifier,))
        EXPORT_SLOTS.release()
    try:
        required = int(sum(Path(row["output"]).stat().st_size for row in rows if row["output"] and Path(row["output"]).is_file()) * 1.01) + 1048576
        if required > int(os.getenv("MAX_EXPORT_BYTES", "268435456")):
            raise HTTPException(413, "Export exceeds configured size limit")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            artifact_storage.reserve(c, ROOT, security.owner(), identifier, required)
        descriptor, filename = tempfile.mkstemp(prefix="export-", suffix=".zip", dir=exports)
        os.close(descriptor)
        target = Path(filename)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(
                ["job_id", "model_id", "status", "text", "reference_id", "error"]
            )
            for r in rows:
                writer.writerow(
                    [
                        r["id"],
                        r["model_id"],
                        r["status"],
                        csv_safe(r["text"]),
                        r["reference_id"],
                        r["error"] or "",
                    ]
                )
                if r["output"] and Path(r["output"]).is_file():
                    archive.write(stored_audio(r["output"]), f"audio/{r['model_id']}_{r['id']}.wav")
            archive.writestr("jobs.csv", out.getvalue())
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(["job_id", "metric", "value", "evaluator"])
            for m in metrics:
                writer.writerow([m["job_id"], m["name"], m["value"], m["evaluator"]])
            archive.writestr("metrics.csv", out.getvalue())
            archive.writestr(
                "metadata.json",
                json.dumps({"batch_id": batch_id, "generated_at": time.time(), "jobs": provenance}),
            )
        return PrivateExportResponse(target, release, media_type="application/zip", filename=f"voice-experiment-{batch_id}.zip",
                                     headers={"Cache-Control": "no-store"})
    except BaseException:
        release()
        raise


class Login(RequestModel):
    token: str = Field(default="", max_length=1000)
    username: str = Field(default="", max_length=100)
    password: str = Field(default="", max_length=1000)
    transport: Literal["cookie", "bearer"] = "cookie"


@app.post("/api/session")
def login(body: Login, request: Request):
    source = request.client.host if request.client else "unknown"
    account_name = "legacy" if os.getenv("AUTH_MODE", "token") == "token" else body.username.casefold().strip()
    account = hashlib.sha256(account_name.encode()).hexdigest()
    if not security.limiter.allow(("login-ip", source), 20, 300) or not security.limiter.allow(("login-account", account), 10, 300):
        logger.warning("Login rate limit reached")
        raise HTTPException(429, "Too many login attempts; retry later", headers={"Retry-After": "300"})
    failure = None
    with db() as c:
        try:
            cookie = security.authenticate(c, body.token, body.username, body.password)
            security.event(c, "login_success", request.state.request_id)
        except HTTPException as error:
            security.event(c, "login_failure", request.state.request_id)
            failure = error
    if failure:
        logger.warning("Login failed request=%s", request.state.request_id)
        raise failure
    response = JSONResponse({"authenticated": True} | ({"session_token": cookie} if body.transport == "bearer" else {}))
    secure = os.getenv("COOKIE_SECURE", "0") == "1"
    response.set_cookie("studio_session", cookie, httponly=True, secure=secure,
                        samesite="none" if secure else "lax", max_age=security.SESSION_SECONDS, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


class PasswordReset(RequestModel):
    token: str = Field(min_length=1, max_length=1000)
    password: str = Field(min_length=12, max_length=1000)


@app.post("/api/password-reset")
def reset_password(body: PasswordReset, request: Request):
    from manage_accounts import valid_password
    source = request.client.host if request.client else "unknown"
    if not security.limiter.allow(("reset", source), 10, 300):
        raise HTTPException(429, "Too many requests; retry later")
    try:
        valid_password(body.password)
    except ValueError:
        raise HTTPException(422, "Use a unique password of at least 12 characters")
    digest = hashlib.sha256(body.token.encode()).hexdigest()
    with db() as c:
        if not c.execute("SELECT 1 FROM password_resets WHERE digest=? AND expires>?", (digest,time.time())).fetchone():
            raise HTTPException(400, "Invalid or expired reset token")
    hashed = security.passwords.hash(body.password)
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("DELETE FROM password_resets WHERE digest=? AND expires>? RETURNING owner_id", (digest, time.time())).fetchone()
        if not row:
            raise HTTPException(400, "Invalid or expired reset token")
        c.execute("UPDATE accounts SET password_hash=?,credential_version=credential_version+1 WHERE id=?", (hashed, row["owner_id"]))
        c.execute("DELETE FROM sessions WHERE owner_id=?", (row["owner_id"],))
    return {"status": "password_reset"}


@app.get("/api/session")
def session_state():
    return {"authenticated": security.principal.get() is not None,
            "auth_mode": os.getenv("AUTH_MODE", "token"), "principal": security.principal.get()}


@app.delete("/api/session")
def logout(request: Request):
    with db() as c:
        c.execute("DELETE FROM sessions WHERE digest=?", (hashlib.sha256(security.session_value(request).encode()).hexdigest(),))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie("studio_session", path="/")
    return response


@app.get("/api/deployment")
def deployment():
    return {"backend_version": app.version, "git_revision": os.getenv("GIT_REVISION", "unknown"),
            "api_version": "1", "environment": os.getenv("APP_ENV", "development")}


@app.get("/api/readiness")
def readiness():
    with db() as c:
        c.execute("SELECT 1").fetchone()
    return {"status": "ready", **deployment()}


@app.middleware("http")
async def protect_api(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    user = None
    context = None
    try:
        if security.production() and request.url.scheme != "https" and (request.client is None or request.client.host not in ("127.0.0.1", "::1")):
            return JSONResponse({"detail": "HTTPS required"}, status_code=400)
        if request.method != "OPTIONS" and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            own_origin = str(request.base_url).rstrip("/")
            mutable = request.method not in ("GET", "HEAD")
            if mutable and origin and origin not in ORIGINS and origin != own_origin:
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
            with db() as c:
                user, cookie_auth = security.identify(request, c)
            public = request.url.path in ("/api/health", "/api/deployment", "/api/password-reset") or (request.url.path == "/api/session" and request.method in ("GET", "POST"))
            # Admin reload remains separately bearer-authorized, never cookie-authorized.
            admin = request.url.path.startswith("/api/admin/")
            if not public and not admin and user is None:
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            if mutable and cookie_auth and request.headers.get("X-Studio-CSRF") != "1":
                return JSONResponse({"detail": "CSRF header required"}, status_code=403)
            if mutable and not public:
                source = request.client.host if request.client else "unknown"
                limit = 6 if request.url.path in ("/api/generations", "/api/batches", "/api/references/upload") else 60
                if not security.limiter.allow((user or source, request.url.path), limit):
                    return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
            if request.method == "GET" and request.url.path.startswith("/api/exports/") and not security.limiter.allow((user, "exports"), 10):
                return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        context = security.principal.set(user)
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request error id=%s", request_id)
        response = JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
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

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        maximum = 17 * 1024 * 1024 if scope["path"] == "/api/references/upload" else 256 * 1024
        try:
            if int(headers.get(b"content-length", b"0")) > maximum:
                return await JSONResponse({"detail": "Request too large"}, status_code=413)(scope, receive, send)
        except ValueError:
            return await JSONResponse({"detail": "Invalid content length"}, status_code=400)(scope, receive, send)
        # Spool within the fixed bound before forwarding; handles chunked requests too.
        messages, size = [], 0
        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > maximum:
                    return await JSONResponse({"detail": "Request too large"}, status_code=413)(scope, receive, send)
                messages.append(message)
                if not message.get("more_body", False):
                    break
        async def bounded_receive():
            return messages.pop(0) if messages else await receive()
        await self.app(scope, bounded_receive, send)

app.add_middleware(BodyLimitMiddleware)
app.add_middleware(SecurityHeaders)

# Register CORS after authentication so it wraps early 401/403 responses too.
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
