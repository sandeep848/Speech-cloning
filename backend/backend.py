"""Voice Comparison Studio API. No synthetic inference or fabricated metrics."""

from __future__ import annotations
import asyncio, hashlib, hmac, json, os, sqlite3, time, uuid, wave, shutil
from pathlib import Path
from contextlib import contextmanager, asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=False)
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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


@asynccontextmanager
async def lifespan(app):
    load_backend_reference()
    yield
    for task in list(TASKS.values()):
        task.cancel()
    if TASKS:
        await asyncio.gather(*list(TASKS.values()), return_exceptions=True)


app = FastAPI(title="Voice Comparison Studio", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
MODEL_IDS = ("qwen3", "cosyvoice3", "xtts-v2")
LABELS = {"qwen3": "Qwen3-TTS", "cosyvoice3": "CosyVoice 3", "xtts-v2": "XTTS-v2"}
POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="inference")
MODEL_LOCKS = {model: asyncio.Lock() for model in MODEL_IDS}
MAX_PENDING = int(os.getenv("MAX_PENDING_JOBS", "12"))
TASKS: dict[str, asyncio.Task] = {}


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

# The reference is configured only on the server, never uploaded by the public frontend.
REFERENCE_DIR = Path(
    os.getenv("REFERENCE_DIR", Path(__file__).resolve().parent / "reference")
).resolve()
REFERENCE_AUDIO = REFERENCE_DIR / "reference.wav"
REFERENCE_TEXT = REFERENCE_DIR / "reference.txt"


def install_backend_reference():
    """Import/update the server-owned reference file and transcript, without exposing them to uploads."""
    if not REFERENCE_AUDIO.is_file():
        return False
    import wave as wav

    try:
        with wav.open(str(REFERENCE_AUDIO), "rb") as reader:
            duration = reader.getnframes() / reader.getframerate()
            if duration < 0.25 or duration > 120:
                raise ValueError("Backend reference WAV must be 0.25-120 seconds long")
    except (wav.Error, ZeroDivisionError) as exc:
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
    # Repair stored absolute paths after moving the whole data directory.
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
    # Tasks are held in process memory. A restart cannot resume incomplete inference.
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


class Workspace(BaseModel):
    active: list[str]


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "inference": "direct-or-isolated-workers",
        "execution": "parallel",
        "reference_configured": REFERENCE_AUDIO.is_file(),
    }


@app.get("/api/models")
def models():
    from inference import model_availability

    return [{"id": m, "name": LABELS[m], **model_availability(m)} for m in MODEL_IDS]


@app.get("/api/reference-profile")
def get_reference_profile():
    with db() as c:
        row = c.execute(
            "SELECT reference_id,transcript FROM reference_profiles WHERE id=1"
        ).fetchone()
        if not row:
            return {"reference": None, "transcript": ""}
        ref = c.execute(
            "SELECT id,filename,path FROM references_audio WHERE id=?",
            (row["reference_id"],),
        ).fetchone()
    return {
        "reference": {"id": ref["id"], "filename": ref["filename"]}
        if ref and Path(ref["path"]).exists()
        else None,
        "transcript": row["transcript"],
    }


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


def execute_worker(job_id, model_id, text, reference_path, reference_text):
    """Dispatch to a configured isolated worker or the local adapter."""
    from inference import generate_audio

    output = ROOT / "audio" / f"{job_id}.wav"
    try:
        generate_audio(model_id, text, Path(reference_path), reference_text, output)
        with wave.open(str(output), "rb") as f:
            if f.getnframes() == 0:
                raise RuntimeError("Model produced empty WAV")
        return str(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise


def auto_evaluate(job_id):
    try:
        __import__("extend_backend").schedule_evaluation(job_id)
    except Exception as exc:
        with db() as c:
            c.execute(
                "INSERT OR REPLACE INTO evaluation_runs VALUES (?,?,?,?)",
                (job_id, "failed", str(exc)[:1000], time.time()),
            )


async def run_job(job_id, model, text, path, reference_text):
    async with MODEL_LOCKS[model]:
        update_job(job_id, "running")
        try:
            loop = asyncio.get_running_loop()
            started = time.perf_counter()
            try:
                output = await loop.run_in_executor(
                    POOL, execute_worker, job_id, model, text, path, reference_text
                )
            finally:
                runtime = time.perf_counter() - started
            update_job(job_id, "complete", output, runtime_seconds=runtime)
            if os.environ.get("AUTO_EVALUATE", "1") == "1":
                auto_evaluate(job_id)
        except Exception as exc:
            update_job(
                job_id,
                "failed",
                error=str(exc)[:400],
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
    with db() as c:
        profile = c.execute(
            "SELECT reference_id,transcript FROM reference_profiles WHERE id=1"
        ).fetchone()
        selected_reference = request.reference_id or (
            profile["reference_id"] if profile else None
        )
        effective_transcript = request.reference_text or (
            profile["transcript"] if profile else ""
        )
        ref = c.execute(
            "SELECT * FROM references_audio WHERE id=?", (selected_reference,)
        ).fetchone()
    if not ref or not Path(ref["path"]).is_file():
        raise HTTPException(422, "Configure backend/reference/reference.wav first")
    from inference import model_availability

    available = await asyncio.gather(
        *(asyncio.to_thread(model_availability, m) for m in request.models)
    )
    # Recheck admission after network health probes yield to other requests.
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
                        os.getenv("GENERATION_LANGUAGE", "German"),
                        os.getenv("MODEL_CACHE_VERSION", "1"),
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
        errors = (
            c.execute(
                "SELECT name,reason FROM evaluation_errors WHERE job_id=?", (job_id,)
            ).fetchall()
            if table_exists(c, "evaluation_errors")
            else []
        )
    state = run["status"] if run else ("completed" if results else "not_evaluated")
    return {
        "job_id": job_id,
        "metrics": [dict(r) for r in results],
        "status": state,
        "error": run["error"] if run else None,
        "errors": [dict(e) for e in errors],
    }


def table_exists(conn, name):
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


# Additional endpoints: persistent workspace, batch experiments, evaluation, exports.
import extend_backend  # noqa: E402,F401


def session_signature(stamp):
    return hmac.new(
        os.environ.get("API_TOKEN", "").encode(), stamp.encode(), hashlib.sha256
    ).hexdigest()


def authenticated(request):
    token = os.getenv("API_TOKEN")
    if not token:
        return True
    if hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
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
    response.set_cookie(
        "studio_session",
        stamp + "." + session_signature(stamp),
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "0") == "1",
        samesite="lax",
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


# API routes are registered first; the production UI is served last.
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
