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
import time
import uuid
import wave
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).parent / ".env", override=False)

import direct_inference
import security
from generation_config import GenerationConfig, current as generation_config

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
EVAL_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evaluation")
MODEL_LOCKS = {model: asyncio.Lock() for model in MODEL_IDS}
MAX_PENDING = int(os.getenv("MAX_PENDING_JOBS", "12"))
TASKS: dict[str, asyncio.Task] = {}
ADMISSION_LOCK = asyncio.Lock()
REPRODUCIBILITY_LOCK = threading.Lock()

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
    direct_inference.preload_all_models()
    try:
        print("[evaluator] Loading Whisper, SpeechBrain ECAPA, and ScoreQ...", flush=True)
        get_evaluator()
        print("[evaluator] All metric evaluators ready.", flush=True)
    except Exception as exc:
        print(f"[evaluator] Preload warning: {exc}", flush=True)


@asynccontextmanager
async def lifespan(app):
    security.validate_configuration()
    load_backend_reference()
    if os.getenv("PRELOAD_MODELS", "1") == "1":
        threading.Thread(target=_startup_preload, daemon=True).start()
    yield
    for task in list(TASKS.values()):
        task.cancel()
    if TASKS:
        await asyncio.gather(*list(TASKS.values()), return_exceptions=True)


app = FastAPI(title="Voice Comparison Studio", version="1.1.0", lifespan=lifespan,
              docs_url=None if security.production() else "/docs",
              redoc_url=None if security.production() else "/redoc",
              openapi_url=None if security.production() else "/openapi.json")



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
    c.execute(
        "CREATE TABLE IF NOT EXISTS references_audio (id TEXT PRIMARY KEY, sha256 TEXT, filename TEXT, path TEXT, created REAL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, text TEXT, reference_id TEXT, created REAL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, batch_id TEXT, model_id TEXT, status TEXT, output TEXT, error TEXT, created REAL, updated REAL, cache_key TEXT)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS metrics (job_id TEXT, name TEXT, value REAL, evaluator TEXT, PRIMARY KEY(job_id,name))"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS evaluation_runs (job_id TEXT PRIMARY KEY, status TEXT, error TEXT, updated REAL)"
    )
    for column in ("started_at", "runtime_seconds"):
        existing_columns = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        if column not in existing_columns:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {column} REAL")
    c.execute(
        "CREATE TABLE IF NOT EXISTS reference_profiles (id INTEGER PRIMARY KEY CHECK(id=1), reference_id TEXT, transcript TEXT NOT NULL DEFAULT '', updated REAL)"
    )
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(
        "CREATE TABLE IF NOT EXISTS human_ratings (job_id TEXT, rater TEXT, category TEXT, rating INTEGER, PRIMARY KEY(job_id,rater,category))"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS evaluation_errors (job_id TEXT, name TEXT, reason TEXT, PRIMARY KEY(job_id,name))"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS batch_runs (id TEXT PRIMARY KEY, params TEXT, created REAL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS batch_generations (batch_id TEXT, generation_id TEXT, PRIMARY KEY(batch_id,generation_id))"
    )

with db() as c:
    security.initialize(c)
    c.execute("CREATE TABLE IF NOT EXISTS job_provenance (job_id TEXT PRIMARY KEY REFERENCES jobs(id), configuration TEXT NOT NULL)")
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
            "SELECT id,path FROM references_audio WHERE sha256=? ORDER BY created DESC LIMIT 1",
            (digest,),
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
        mark_shared_reference(c, ref_id)
        c.execute(
            "INSERT INTO reference_profiles(id,reference_id,transcript,updated) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET reference_id=excluded.reference_id,transcript=excluded.transcript,updated=excluded.updated",
            (ref_id, transcript, time.time()),
        )
    return True


def load_backend_reference():
    with db() as c:
        for table, column in (("references_audio", "path"), ("jobs", "output")):
            for row in c.execute(
                f"SELECT id,{column} AS path FROM {table} WHERE {column} IS NOT NULL"
            ).fetchall():
                relocated = ROOT / "audio" / Path(row["path"]).name
                if not Path(row["path"]).is_file() and relocated.is_file():
                    c.execute(
                        f"UPDATE {table} SET {column}=? WHERE id=?",
                        (str(relocated), row["id"]),
                    )
    install_backend_reference()
    with db() as c:
        c.execute(
            "UPDATE jobs SET status='failed',error='Inference interrupted by backend restart',updated=? WHERE status IN ('pending','running')",
            (time.time(),),
        )
        c.execute(
            "UPDATE evaluation_runs SET status='interrupted',error='Evaluation interrupted by backend restart',updated=? WHERE status IN ('pending','running')",
            (time.time(),),
        )


@app.post("/api/admin/reload-reference")
def reload_backend_reference(authorization: str | None = Header(None)):
    token = os.environ.get("ADMIN_TOKEN")
    if security.production() and os.getenv("AUTH_MODE") == "accounts":
        with db() as c:
            account = c.execute("SELECT role FROM accounts WHERE id=?", (security.principal.get(),)).fetchone()
        if not account or account["role"] != "admin":
            raise HTTPException(403, "Administrator account required")
    if not token or not hmac.compare_digest(authorization or "", "Bearer " + token):
        raise HTTPException(403, "Admin token required")
    return {"loaded": install_backend_reference()}


class GenerateRequest(BaseModel):
    use_cache: bool = True
    text: str = Field(min_length=1, max_length=2000)
    reference_id: str | None = None
    models: list[str] = Field(min_length=1, max_length=3)
    reference_text: str = Field(default="", max_length=4000)
    language: str = Field(default="German", pattern="^(German|English|de|en)$")
    temperature: float | None = Field(default=None, ge=0.1, le=1.5, allow_inf_nan=False)
    speech_rate: float | None = Field(default=None, ge=0.8, le=1.1, allow_inf_nan=False)
    seed: int | None = Field(default=None, ge=0, le=4294967295)


class BatchRequest(BaseModel):
    reference_id: str
    models: list[str] = Field(min_length=1, max_length=3)
    texts: list[str] = Field(min_length=1, max_length=100)
    repetitions: int = Field(default=1, ge=1, le=20)
    reference_text: str = Field(default="", max_length=4000)


class RatingRequest(BaseModel):
    job_id: str
    rater: str = Field(default="Evaluator", min_length=1, max_length=100)
    category: str = Field(pattern="^(naturalness|similarity|intelligibility)$")
    rating: int = Field(ge=1, le=5)


class LayoutRequest(BaseModel):
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
    return [
        {"id": m, "name": LABELS[m], **direct_inference.model_availability(m)}
        for m in MODEL_IDS
    ]


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
            "SELECT id,path FROM references_audio WHERE sha256=? ORDER BY created DESC LIMIT 1",
            (digest,),
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
):
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
        count = c.execute("SELECT count(*) FROM resource_owners WHERE kind='reference' AND owner_id=?", (security.owner(),)).fetchone()[0]
        if count >= 50:
            raise HTTPException(429, "Reference storage quota reached")
        target.write_bytes(content)
        try:
            c.execute("INSERT INTO references_audio VALUES (?,?,?,?,?)",
                      (reference_id, hashlib.sha256(content).hexdigest(), "reference.wav", str(target), time.time()))
            security.claim(c, "reference", reference_id)
            c.execute("INSERT INTO user_reference_profiles VALUES (?,?,?,?) ON CONFLICT(owner_id,language) DO UPDATE SET reference_id=excluded.reference_id,transcript=excluded.transcript",
                      (security.owner(), "German", reference_id, transcript.strip()))
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return get_reference_profile()


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
        with wave.open(str(output), "rb") as f:
            if f.getnframes() == 0:
                raise RuntimeError("Model produced empty WAV")
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
        evaluator = get_evaluator()
        with _evaluator_lock:
            elapsed = (
                row["runtime_seconds"]
                if row["runtime_seconds"] is not None
                else max(0, row["updated"] - row["created"])
            )
            metrics = evaluator.evaluate(
                row["output"], row["reference"], row["text"], elapsed
            )
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


async def run_job(job_id, model, text, path, reference_text, language="German", config=GenerationConfig()):
    async with MODEL_LOCKS[model]:
        update_job(job_id, "running")
        try:
            loop = asyncio.get_running_loop()
            output, runtime = await loop.run_in_executor(
                POOL,
                execute_worker,
                job_id,
                model,
                text,
                path,
                reference_text,
                language,
                config,
            )
            update_job(job_id, "complete", output, runtime_seconds=runtime)
            if os.environ.get("AUTO_EVALUATE", "1") == "1":
                auto_evaluate(job_id)
        except Exception as exc:
            logger.exception("Inference failed job=%s", job_id)
            err_msg = "Inference failed; contact the administrator with the job ID"
            update_job(
                job_id,
                "failed",
                error=err_msg[:2000],
                runtime_seconds=locals().get("runtime"),
            )


@app.post("/api/generations", status_code=202)
async def generate(request: GenerateRequest):
    async with ADMISSION_LOCK:
        return await _generate_impl(request)


async def _generate_impl(request: GenerateRequest, deferred=None):
    if request.temperature is not None and "cosyvoice3" in request.models:
        raise HTTPException(422, "CosyVoice temperature is unsupported; leave temperature unset for comparison")
    if request.seed is not None and os.getenv("REPRODUCIBLE") != "1":
        raise HTTPException(422, "Seeded requests require REPRODUCIBLE=1 (serialized inference)")
    config = GenerationConfig(request.temperature, request.speech_rate, request.seed)
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
        raise HTTPException(503, "; ".join(unavailable))
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
            key = hashlib.sha256(
                json.dumps(
                    [
                        model,
                        security.owner(),
                        request.temperature, request.speech_rate, request.seed,
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
            provenance = {"model_id": model, "model_revision": os.getenv({"qwen3":"QWEN_REVISION", "cosyvoice3":"COSY_REVISION", "xtts-v2":"XTTS_REVISION"}[model], "unverified"),
                          "reference_sha256": ref["sha256"], "transcript_sha256": hashlib.sha256(effective_transcript.encode()).hexdigest(),
                          "language": req_lang, "temperature": request.temperature, "speech_rate": request.speech_rate,
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
            args = (job_id, model, request.text.strip(), ref["path"], effective_transcript, req_lang, config)
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
    task = asyncio.create_task(run_job(*args))
    TASKS[job_id] = task
    task.add_done_callback(lambda _, jid=job_id: TASKS.pop(jid, None))


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
            "INSERT INTO batch_runs VALUES (?,?,?)",
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
            for generation_id in generated:
                jobs = [r[0] for r in c.execute("SELECT id FROM jobs WHERE batch_id=?", (generation_id,))]
                for job_id in jobs:
                    c.execute("DELETE FROM job_provenance WHERE job_id=?", (job_id,))
                    c.execute("DELETE FROM resource_owners WHERE kind='job' AND resource_id=?", (job_id,))
                c.execute("DELETE FROM jobs WHERE batch_id=?", (generation_id,))
                c.execute("DELETE FROM batches WHERE id=?", (generation_id,))
                c.execute("DELETE FROM resource_owners WHERE kind='generation' AND resource_id=?", (generation_id,))
            c.execute("DELETE FROM batch_generations WHERE batch_id=?", (parent,))
            c.execute("DELETE FROM batch_runs WHERE id=?", (parent,))
            c.execute("DELETE FROM resource_owners WHERE kind='batch' AND resource_id=?", (parent,))
        raise
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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
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
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="voice-experiment-{batch_id}.zip"'
        },
    )


class Login(BaseModel):
    token: str = Field(default="", max_length=1000)
    username: str = Field(default="", max_length=100)
    password: str = Field(default="", max_length=1000)


@app.post("/api/session")
def login(body: Login, request: Request):
    source = request.client.host if request.client else "unknown"
    account = hashlib.sha256(body.username.casefold().strip().encode()).hexdigest()
    if not security.limiter.allow(("login-ip", source), 20, 300) or not security.limiter.allow(("login-account", account), 10, 300):
        logger.warning("Login rate limit reached")
        raise HTTPException(429, "Too many login attempts; retry later", headers={"Retry-After": "300"})
    with db() as c:
        try:
            cookie = security.authenticate(c, body.token, body.username, body.password)
        except HTTPException:
            logger.warning("Login failed")
            raise
    response = JSONResponse({"authenticated": True})
    secure = os.getenv("COOKIE_SECURE", "0") == "1"
    response.set_cookie("studio_session", cookie, httponly=True, secure=secure,
                        samesite="none" if secure else "lax", max_age=security.SESSION_SECONDS, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


class PasswordReset(BaseModel):
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
        c.execute("DELETE FROM sessions WHERE digest=?", (hashlib.sha256(request.cookies.get("studio_session", "").encode()).hexdigest(),))
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
            admin = request.url.path == "/api/admin/reload-reference"
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
