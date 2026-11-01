"""Evaluation retrieval and triggering endpoints."""

from __future__ import annotations
from fastapi import APIRouter, HTTPException

import security

router = APIRouter(prefix="/api", tags=["evaluations"])


def _get_studio():
    import backend
    return backend


@router.get("/evaluations/{job_id}")
def evaluations(job_id: str):
    st = _get_studio()
    with st.db() as c:
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


@router.post("/evaluations/{job_id}", status_code=202)
def start_evaluation(job_id: str):
    st = _get_studio()
    with st.db() as c:
        security.require_owner(c, "job", job_id)
    status = st.schedule_evaluation(job_id)
    return {"status": status, "job_id": job_id}


@router.get("/evaluation-errors/{job_id}")
def evaluation_errors(job_id: str):
    st = _get_studio()
    with st.db() as c:
        security.require_owner(c, "job", job_id)
        rows = c.execute(
            "SELECT name,reason FROM evaluation_errors WHERE job_id=?", (job_id,)
        ).fetchall()
    return [dict(x) for x in rows]
