"""Audio generation and job management routes."""

from __future__ import annotations
import asyncio
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import lifecycle
import security
from api_schemas import GenerateRequest, LayoutRequest
from isolated_workers import TerminationFailure

router = APIRouter(prefix="/api", tags=["generations"])


def _get_studio():
    import backend
    return backend


@router.post("/generations", status_code=202)
async def generate(request: GenerateRequest):
    st = _get_studio()
    async with st.ADMISSION_LOCK:
        return await st._generate_impl(request)


@router.get("/generations/{batch_id}")
def batch(batch_id: str):
    st = _get_studio()
    with st.db() as c:
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


@router.delete("/generations/{generation_id}")
def delete_generation(generation_id: str):
    st = _get_studio()
    with st._eval_jobs_lock:
        with st.db() as c:
            c.execute("BEGIN IMMEDIATE")
            security.require_owner(c, "generation", generation_id)
            paths = []
            jobs = c.execute(
                "SELECT id FROM jobs WHERE batch_id=?", (generation_id,)
            ).fetchall()
            for job in jobs:
                paths.extend(lifecycle.remove_job(c, job["id"]))
            discard = lifecycle.discard_unreferenced(c, paths, st.ROOT)
    lifecycle.unlink(discard)
    return {"status": "deleted"}


@router.get("/recordings/{job_id}/audio")
def recording(job_id: str):
    st = _get_studio()
    with st.db() as c:
        security.require_owner(c, "job", job_id)
        row = c.execute(
            "SELECT output FROM jobs WHERE id=? AND status='complete'", (job_id,)
        ).fetchone()
    if not row or not row["output"] or not Path(row["output"]).exists():
        raise HTTPException(404, "Recording not available")
    return FileResponse(
        st.stored_audio(row["output"]), media_type="audio/wav", filename=f"{job_id}.wav"
    )


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    st = _get_studio()
    with st.db() as c:
        security.require_owner(c, "job", job_id)
        row = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row["status"] not in ("pending", "running"):
        await asyncio.to_thread(st.delete_finished_job, job_id)
        return {"id": job_id, "status": "deleted"}
    task = st.TASKS.get(job_id)
    if task is None:
        raise HTTPException(409, "Job is no longer active in this process")
    if not task.cancel():
        raise HTTPException(409, "Job already finished")
    results = await asyncio.gather(task, return_exceptions=True)
    if any(isinstance(result, TerminationFailure) for result in results):
        raise HTTPException(503, "Worker termination needs administrator intervention")
    return {"id": job_id, "status": "cancelled"}


@router.get("/experiments")
def experiments(limit: int = 30):
    st = _get_studio()
    with st.db() as c:
        rows = c.execute(
            "SELECT b.* FROM batches b JOIN resource_owners o ON o.resource_id=b.id AND o.kind='generation' WHERE o.owner_id=? ORDER BY b.created DESC LIMIT ?",
            (security.owner(), min(max(limit, 1), 100)),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/workspace")
def workspace():
    st = _get_studio()
    with st.db() as c:
        row = c.execute(
            "SELECT value FROM user_preferences WHERE owner_id=?", (security.owner(),)
        ).fetchone()
    return json.loads(row["value"]) if row else {"active": list(st.MODEL_IDS)}


@router.patch("/workspace")
def save_workspace(body: LayoutRequest):
    st = _get_studio()
    if len(body.active) != len(set(body.active)) or any(
        m not in st.MODEL_IDS for m in body.active
    ):
        raise HTTPException(422, "Invalid model layout")
    with st.db() as c:
        c.execute(
            "INSERT OR REPLACE INTO user_preferences VALUES (?,?)",
            (security.owner(), json.dumps(body.model_dump())),
        )
    return body
