"""Additional production API endpoints and evaluation orchestration."""

import csv, io, json, time, uuid, zipfile, threading, math
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import backend as core
from concurrent.futures import ThreadPoolExecutor

EVAL_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evaluation")

with core.db() as conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS human_ratings (job_id TEXT,rater TEXT,category TEXT,rating INTEGER,PRIMARY KEY(job_id,rater,category))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evaluation_errors (job_id TEXT,name TEXT,reason TEXT,PRIMARY KEY(job_id,name))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS batch_runs (id TEXT PRIMARY KEY,params TEXT,created REAL)"
    )


with core.db() as conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS batch_generations (batch_id TEXT,generation_id TEXT,PRIMARY KEY(batch_id,generation_id))"
    )


class BatchRequest(BaseModel):
    reference_id: str
    models: list[str] = Field(min_length=1, max_length=3)
    texts: list[str] = Field(min_length=1, max_length=100)
    repetitions: int = Field(default=1, ge=1, le=20)
    reference_text: str = ""


class RatingRequest(BaseModel):
    job_id: str
    rater: str = Field(min_length=1, max_length=100)
    category: str = Field(pattern="^(naturalness|similarity|intelligibility)$")
    rating: int = Field(ge=1, le=5)


class LayoutRequest(BaseModel):
    active: list[str]


@core.app.get("/api/workspace")
def workspace():
    with core.db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='layout'").fetchone()
    return json.loads(row["value"]) if row else {"active": list(core.MODEL_IDS)}


@core.app.patch("/api/workspace")
def save_workspace(body: LayoutRequest):
    if len(body.active) != len(set(body.active)) or any(
        m not in core.MODEL_IDS for m in body.active
    ):
        raise HTTPException(422, "Invalid model layout")
    with core.db() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings VALUES (?,?)",
            ("layout", json.dumps(body.model_dump())),
        )
    return body


@core.app.post("/api/ratings")
def add_rating(body: RatingRequest):
    with core.db() as c:
        if not c.execute(
            "SELECT id FROM jobs WHERE id=? AND status='complete'", (body.job_id,)
        ).fetchone():
            raise HTTPException(404, "Completed recording not found")
        c.execute(
            "INSERT OR REPLACE INTO human_ratings VALUES (?,?,?,?)",
            (body.job_id, body.rater, body.category, body.rating),
        )
    return {"status": "recorded"}


@core.app.get("/api/ratings/{job_id}")
def ratings(job_id: str):
    with core.db() as c:
        rows = c.execute(
            "SELECT rater,category,rating FROM human_ratings WHERE job_id=?", (job_id,)
        ).fetchall()
    return [dict(x) for x in rows]


@core.app.get("/api/agreement")
def agreement(category: str = "naturalness"):
    with core.db() as c:
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


@core.app.post("/api/batches", status_code=202)
async def batch_experiment(body: BatchRequest):
    if len(body.models) != len(set(body.models)) or any(
        x not in core.MODEL_IDS for x in body.models
    ):
        raise HTTPException(422, "Unknown model")
    if any(not x.strip() or len(x) > 2000 for x in body.texts):
        raise HTTPException(422, "Invalid script")
    with core.db() as c:
        if not c.execute(
            "SELECT id FROM references_audio WHERE id=?", (body.reference_id,)
        ).fetchone():
            raise HTTPException(404, "Reference not found")
    if (
        len(body.models) * len(body.texts) * body.repetitions + len(core.TASKS)
        > core.MAX_PENDING
    ):
        raise HTTPException(429, "Batch exceeds available queue capacity")
    parent = str(uuid.uuid4())
    with core.db() as c:
        c.execute(
            "INSERT INTO batch_runs VALUES (?,?,?)",
            (parent, body.model_dump_json(), time.time()),
        )
    # Reuse the same generation API, cache and dispatch logic.
    generated = []
    for repeat in range(body.repetitions):
        for text in body.texts:
            result = await core.generate(
                core.GenerateRequest(
                    text=text,
                    reference_id=body.reference_id,
                    models=body.models,
                    reference_text=body.reference_text,
                    use_cache=False,
                )
            )
            generated.append(result["id"])
            with core.db() as c:
                c.execute(
                    "INSERT INTO batch_generations VALUES (?,?)", (parent, result["id"])
                )
    return {
        "id": parent,
        "generation_ids": generated,
        "total_jobs": len(generated) * len(body.models),
    }


def _batch_generations(batch_id):
    with core.db() as c:
        rows = c.execute(
            "SELECT generation_id FROM batch_generations WHERE batch_id=? ORDER BY rowid",
            (batch_id,),
        ).fetchall()
    return [core.batch(row["generation_id"]) for row in rows]


@core.app.get("/api/batches/{batch_id}")
def batch_status(batch_id: str):
    with core.db() as c:
        b = c.execute("SELECT * FROM batch_runs WHERE id=?", (batch_id,)).fetchone()
        if not b:
            raise HTTPException(404, "Batch not found")
    return {
        "id": batch_id,
        "created": b["created"],
        "configuration": json.loads(b["params"]),
        "generations": _batch_generations(batch_id),
    }


@core.app.get("/api/exports/{batch_id}")
def export_batch(batch_id: str):
    with core.db() as c:
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


_evaluator = None
_evaluator_lock = threading.Lock()
_evaluation_jobs = set()
_eval_jobs_lock = threading.Lock()


def evaluate_job(job_id: str):
    global _evaluator
    try:
        with core.db() as c:
            c.execute(
                "UPDATE evaluation_runs SET status='running',updated=? WHERE job_id=?",
                (time.time(), job_id),
            )
        with core.db() as c:
            row = c.execute(
                "SELECT jobs.output,jobs.runtime_seconds,jobs.created,jobs.updated,batches.text,references_audio.path reference FROM jobs JOIN batches ON batches.id=jobs.batch_id JOIN references_audio ON batches.reference_id=references_audio.id WHERE jobs.id=? AND jobs.status=?",
                (job_id, "complete"),
            ).fetchone()
        if not row or not row["output"]:
            raise RuntimeError("Recording missing or job not complete")
        with _evaluator_lock:
            if _evaluator is None:
                from evaluator import Evaluator

                _evaluator = Evaluator()
            # generation runtime does not include waiting in the GPU queue
            elapsed = (
                row["runtime_seconds"]
                if row["runtime_seconds"] is not None
                else max(0, row["updated"] - row["created"])
            )
            metrics = _evaluator.evaluate(
                row["output"], row["reference"], row["text"], elapsed
            )
            errors = dict(_evaluator.errors)
            _evaluator.errors.clear()
        with core.db() as c:
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
        with core.db() as c:
            c.execute(
                "INSERT INTO evaluation_runs VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,error=excluded.error,updated=excluded.updated",
                (job_id, "failed", str(exc)[:1000], time.time()),
            )
    finally:
        with _eval_jobs_lock:
            _evaluation_jobs.discard(job_id)


def schedule_evaluation(job_id: str, force: bool = False) -> str:
    with _eval_jobs_lock:
        with core.db() as c:
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
            if len(_evaluation_jobs) >= core.MAX_PENDING:
                raise HTTPException(429, "Evaluation queue is full")
            c.execute(
                "INSERT INTO evaluation_runs VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,error=NULL,updated=excluded.updated",
                (job_id, "pending", None, time.time()),
            )
        _evaluation_jobs.add(job_id)
    EVAL_POOL.submit(evaluate_job, job_id)
    return "scheduled"


@core.app.post("/api/evaluations/{job_id}", status_code=202)
async def start_evaluation(job_id: str):
    status = schedule_evaluation(job_id)
    return {"status": status, "job_id": job_id}


@core.app.get("/api/evaluation-errors/{job_id}")
def evaluation_errors(job_id: str):
    with core.db() as c:
        rows = c.execute(
            "SELECT name,reason FROM evaluation_errors WHERE job_id=?", (job_id,)
        ).fetchall()
    return [dict(x) for x in rows]
