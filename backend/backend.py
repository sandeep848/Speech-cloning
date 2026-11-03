"""Voice Comparison Studio API — unified single-environment server with all models and metrics."""

from __future__ import annotations
import asyncio
import csv
import hashlib
import hmac
import io
import json
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

ROOT = Path(
    os.environ.get("VOICE_STUDIO_DATA", Path(__file__).parent / "data")
).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / "audio").mkdir(exist_ok=True)
DB = ROOT / "studio.sqlite3"
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
    load_backend_reference()
    if os.getenv("PRELOAD_MODELS", "1") == "1":
        threading.Thread(target=_startup_preload, daemon=True).start()
    yield
    for task in list(TASKS.values()):
        task.cancel()
    if TASKS:
        await asyncio.gather(*list(TASKS.values()), return_exceptions=True)


app = FastAPI(title="Voice Comparison Studio", version="1.0.0", lifespan=lifespan)



@contextmanager
def db():
    connection = sqlite3.connect(DB, timeout=30)
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

REFERENCE_DIR = Path(
    os.getenv("REFERENCE_DIR", Path(__file__).resolve().parent / "reference")
).resolve()
REFERENCE_AUDIO = REFERENCE_DIR / "reference.wav"
REFERENCE_TEXT = REFERENCE_DIR / "reference.txt"


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
    if not token or authorization != "Bearer " + token:
        raise HTTPException(403, "Admin token required")
    return {"loaded": install_backend_reference()}


class GenerateRequest(BaseModel):
    use_cache: bool = True
    text: str = Field(min_length=1, max_length=2000)
    reference_id: str | None = None
    models: list[str] = Field(min_length=1, max_length=3)
    reference_text: str = Field(default="", max_length=4000)
    language: str = "German"


class BatchRequest(BaseModel):
    reference_id: str
    models: list[str] = Field(min_length=1, max_length=3)
    texts: list[str] = Field(min_length=1, max_length=100)
    repetitions: int = Field(default=1, ge=1, le=20)
    reference_text: str = ""


class RatingRequest(BaseModel):
    job_id: str
    rater: str = Field(default="Evaluator", min_length=1, max_length=100)
    category: str = Field(pattern="^(naturalness|similarity|intelligibility)$")
    rating: int = Field(ge=1, le=5)


class LayoutRequest(BaseModel):
    active: list[str]


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
    content = await file.read()
    if len(content) < 44:
        raise HTTPException(400, "Invalid WAV file")
    try:
        with wave.open(io.BytesIO(content), "rb") as reader:
            duration = reader.getnframes() / reader.getframerate()
            if duration < 0.25 or duration > 120:
                raise ValueError("Reference WAV must be 0.25-120 seconds long")
    except Exception as exc:
        raise HTTPException(400, f"Invalid PCM WAV file: {exc}")

    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCE_AUDIO.write_bytes(content)
    REFERENCE_TEXT.write_text(transcript.strip(), encoding="utf-8")

    install_backend_reference()
    return get_reference_profile()


@app.get("/api/references/{reference_id}/audio")
def reference_audio(reference_id: str):
    with db() as c:
        row = c.execute(
            "SELECT path FROM references_audio WHERE id=?", (reference_id,)
        ).fetchone()
    if not row or not Path(row["path"]).is_file():
        raise HTTPException(
            404, "Reference file unavailable. Check server reference configuration."
        )
    return FileResponse(row["path"], media_type="audio/wav")


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
    job_id, model_id, text, reference_path, reference_text, language="German"
):
    output = ROOT / "audio" / f"{job_id}.wav"
    try:
        t0 = time.perf_counter()
        from models import _request_language
        token = _request_language.set(language)
        try:
            direct_inference.generate_audio(
                model_id, text, Path(reference_path), reference_text, output
            )
        finally:
            _request_language.reset(token)
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
                    (job_id, name, str(reason)[:1000]),
                )
            c.execute(
                "UPDATE evaluation_runs SET status=?,error=?,updated=? WHERE job_id=?",
                ("partial" if errors else "completed", None, time.time(), job_id),
            )
    except Exception as exc:
        with db() as c:
            c.execute(
                "INSERT INTO evaluation_runs VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,error=excluded.error,updated=excluded.updated",
                (job_id, "failed", str(exc)[:1000], time.time()),
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
                (job_id, "failed", str(exc)[:1000], time.time()),
            )


async def run_job(job_id, model, text, path, reference_text, language="German"):
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
            )
            update_job(job_id, "complete", output, runtime_seconds=runtime)
            if os.environ.get("AUTO_EVALUATE", "1") == "1":
                auto_evaluate(job_id)
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            update_job(
                job_id,
                "failed",
                error=err_msg[:2000],
                runtime_seconds=locals().get("runtime"),
            )


@app.post("/api/generations", status_code=202)
async def generate(request: GenerateRequest):
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
        c.execute(
            "INSERT INTO batches VALUES (?,?,?,?)",
            (batch_id, request.text.strip(), selected_reference, now),
        )
        for model in request.models:
            key = hashlib.sha256(
                json.dumps(
                    [
                        model,
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
            planned.append((job_id, model, out))
    for job_id, model, out in planned:
        if out and os.environ.get("AUTO_EVALUATE", "1") == "1":
            auto_evaluate(job_id)
        if not out:
            task = asyncio.create_task(
                run_job(
                    job_id,
                    model,
                    request.text.strip(),
                    ref["path"],
                    effective_transcript,
                    req_lang,
                )
            )
            TASKS[job_id] = task
            task.add_done_callback(lambda _, jid=job_id: TASKS.pop(jid, None))
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


@app.get("/api/generations/{batch_id}")
def batch(batch_id: str):
    with db() as c:
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
        row = c.execute(
            "SELECT output FROM jobs WHERE id=? AND status='complete'", (job_id,)
        ).fetchone()
    if not row or not row["output"] or not Path(row["output"]).exists():
        raise HTTPException(404, "Recording not available")
    return FileResponse(row["output"], media_type="audio/wav", filename=f"{job_id}.wav")


@app.get("/api/experiments")
def experiments(limit: int = 30):
    with db() as c:
        rows = c.execute(
            "SELECT * FROM batches ORDER BY created DESC LIMIT ?",
            (min(max(limit, 1), 100),),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/evaluations/{job_id}")
def evaluations(job_id: str):
    with db() as c:
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
    status = schedule_evaluation(job_id)
    return {"status": status, "job_id": job_id}


@app.get("/api/evaluation-errors/{job_id}")
def evaluation_errors(job_id: str):
    with db() as c:
        rows = c.execute(
            "SELECT name,reason FROM evaluation_errors WHERE job_id=?", (job_id,)
        ).fetchall()
    return [dict(x) for x in rows]


@app.get("/api/workspace")
def workspace():
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='layout'").fetchone()
    return json.loads(row["value"]) if row else {"active": list(MODEL_IDS)}


@app.patch("/api/workspace")
def save_workspace(body: LayoutRequest):
    if len(body.active) != len(set(body.active)) or any(
        m not in MODEL_IDS for m in body.active
    ):
        raise HTTPException(422, "Invalid model layout")
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings VALUES (?,?)",
            ("layout", json.dumps(body.model_dump())),
        )
    return body


@app.post("/api/ratings")
def add_rating(body: RatingRequest):
    with db() as c:
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
        rows = c.execute(
            "SELECT rater,category,rating FROM human_ratings WHERE job_id=?", (job_id,)
        ).fetchall()
    return [dict(x) for x in rows]


@app.get("/api/agreement")
def agreement(category: str = "naturalness"):
    with db() as c:
        rows = c.execute(
            "SELECT job_id,rater,rating FROM human_ratings WHERE category=?",
            (category,),
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
    if len(body.models) != len(set(body.models)) or any(
        x not in MODEL_IDS for x in body.models
    ):
        raise HTTPException(422, "Unknown model")
    if any(not x.strip() or len(x) > 2000 for x in body.texts):
        raise HTTPException(422, "Invalid script")
    with db() as c:
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
    generated = []
    for _ in range(body.repetitions):
        for text in body.texts:
            result = await generate(
                GenerateRequest(
                    text=text,
                    reference_id=body.reference_id,
                    models=body.models,
                    reference_text=body.reference_text,
                    use_cache=False,
                )
            )
            generated.append(result["id"])
            with db() as c:
                c.execute(
                    "INSERT INTO batch_generations VALUES (?,?)", (parent, result["id"])
                )
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
        b = c.execute("SELECT * FROM batch_runs WHERE id=?", (batch_id,)).fetchone()
        if not b:
            raise HTTPException(404, "Batch not found")
    return {
        "id": batch_id,
        "created": b["created"],
        "configuration": json.loads(b["params"]),
        "generations": _batch_generations(batch_id),
    }


@app.get("/api/exports/{batch_id}")
def export_batch(batch_id: str):
    with db() as c:
        rows = c.execute(
            "SELECT jobs.*,batches.text,batches.reference_id FROM jobs JOIN batches ON jobs.batch_id=batches.id WHERE batch_id=?",
            (batch_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(404, "Experiment not found")
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
                    r["text"],
                    r["reference_id"],
                    r["error"] or "",
                ]
            )
            if r["output"] and Path(r["output"]).is_file():
                archive.write(r["output"], f"audio/{r['model_id']}_{r['id']}.wav")
        archive.writestr("jobs.csv", out.getvalue())
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["job_id", "metric", "value", "evaluator"])
        for m in metrics:
            writer.writerow([m["job_id"], m["name"], m["value"], m["evaluator"]])
        archive.writestr("metrics.csv", out.getvalue())
        archive.writestr(
            "metadata.json",
            json.dumps({"batch_id": batch_id, "generated_at": time.time()}),
        )
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="voice-experiment-{batch_id}.zip"'
        },
    )


def session_signature(stamp):
    return hmac.new(
        os.environ.get("API_TOKEN", "").encode(), stamp.encode(), hashlib.sha256
    ).hexdigest()


def authenticated(request):
    token = os.getenv("API_TOKEN")
    if not token:
        return True
    if hmac.compare_digest(
        request.headers.get("authorization", ""), "Bearer " + token
    ):
        return True
    value = request.cookies.get("studio_session", "")
    try:
        stamp, signature = value.split(".", 1)
        return 0 <= time.time() - int(stamp) < 43200 and hmac.compare_digest(
            signature, session_signature(stamp)
        )
    except (ValueError, TypeError):
        return False


class Login(BaseModel):
    token: str = Field(max_length=1000)


@app.post("/api/session")
def login(body: Login):
    token = os.getenv("API_TOKEN")
    if not token or not hmac.compare_digest(body.token, token):
        raise HTTPException(401, "Invalid access token")
    stamp = str(int(time.time()))
    response = JSONResponse({"authenticated": True})
    secure_cookie = os.getenv("COOKIE_SECURE", "0") == "1"
    response.set_cookie(
        "studio_session",
        stamp + "." + session_signature(stamp),
        httponly=True,
        secure=secure_cookie,
        samesite="none" if secure_cookie else "lax",
        max_age=43200,
        path="/",
    )
    return response


@app.delete("/api/session")
def logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie("studio_session", path="/")
    return response


@app.middleware("http")
async def protect_api(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        own_origin = str(request.base_url).rstrip("/")
        if (
            request.method not in ("GET", "HEAD")
            and origin
            and origin not in ORIGINS
            and origin != own_origin
        ):
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        public = ("/api/health", "/api/session", "/api/admin/reload-reference")
        if request.url.path not in public and not authenticated(request):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


# Register CORS after authentication so it wraps early 401/403 responses too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
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
