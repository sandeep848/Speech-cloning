"""Administrative operations, system health, batch experiments, and export endpoints."""

from __future__ import annotations
import csv
import hmac
import io
import json
import os
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException

import artifact_storage
import security
from api_schemas import BatchRequest
from export_response import PrivateExportResponse

router = APIRouter(prefix="/api", tags=["admin"])


def _get_studio():
    import backend
    return backend


def require_administrator(authorization: str | None):
    st = _get_studio()
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected or not hmac.compare_digest(
        (authorization or "").encode(), ("Bearer " + expected).encode()
    ):
        raise HTTPException(403, "Administrator credentials required")
    if os.getenv("AUTH_MODE") == "accounts":
        with st.db() as c:
            account = c.execute(
                "SELECT role FROM accounts WHERE id=?", (security.principal.get(),)
            ).fetchone()
        if not account or account["role"] != "admin":
            raise HTTPException(403, "Administrator account required")


@router.get("/health")
def health():
    st = _get_studio()
    return {
        "status": "ok",
        "inference": "direct-single-environment",
        "execution": "parallel",
        "reference_configured": st.REFERENCE_AUDIO.is_file(),
    }


@router.get("/deployment")
def deployment():
    st = _get_studio()
    return {
        "backend_version": st.app.version,
        "git_revision": os.getenv("GIT_REVISION", "unknown"),
        "api_version": "1",
        "environment": os.getenv("APP_ENV", "development"),
    }


@router.get("/readiness")
def readiness():
    st = _get_studio()
    with st.db() as c:
        c.execute("SELECT 1").fetchone()
    return {"status": "ready", **deployment()}


@router.post("/admin/reload-reference")
def reload_backend_reference(authorization: str | None = Header(None)):
    st = _get_studio()
    require_administrator(authorization)
    return {"loaded": st.install_backend_reference()}


@router.get("/admin/security-events")
def security_events(authorization: str | None = Header(None)):
    st = _get_studio()
    require_administrator(authorization)
    with st.db() as c:
        rows = c.execute(
            "SELECT created,kind,request_id FROM security_events ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/batches", status_code=202)
async def batch_experiment(body: BatchRequest):
    st = _get_studio()
    async with st.ADMISSION_LOCK:
        return await st._batch_experiment_impl(body)


@router.get("/batches/{batch_id}")
def batch_status(batch_id: str):
    st = _get_studio()
    with st.db() as c:
        security.require_owner(c, "batch", batch_id)
        b = c.execute("SELECT * FROM batch_runs WHERE id=?", (batch_id,)).fetchone()
        if not b:
            raise HTTPException(404, "Batch not found")
    return {
        "id": batch_id,
        "created": b["created"],
        "configuration": json.loads(b["params"]),
        "status": b["status"],
        "generations": st._batch_generations(batch_id),
    }


@router.get("/exports/{batch_id}")
def export_batch(batch_id: str):
    st = _get_studio()
    with st.db() as c:
        security.require_owner(c, "generation", batch_id)
        rows = c.execute(
            "SELECT jobs.*,batches.text,batches.reference_id FROM jobs JOIN batches ON jobs.batch_id=batches.id WHERE batch_id=?",
            (batch_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(404, "Experiment not found")
        provenance = [
            dict(x)
            for x in c.execute(
                "SELECT p.* FROM job_provenance p JOIN jobs j ON j.id=p.job_id WHERE j.batch_id=?",
                (batch_id,),
            )
        ]
        metrics = c.execute(
            "SELECT * FROM metrics WHERE job_id IN (SELECT id FROM jobs WHERE batch_id=?)",
            (batch_id,),
        ).fetchall()
    if not st.EXPORT_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Export capacity reached")
    exports = st.ROOT / "exports"
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
        with st.db() as c:
            c.execute("DELETE FROM storage_reservations WHERE id=?", (identifier,))
        st.EXPORT_SLOTS.release()

    try:
        required = (
            int(
                sum(
                    Path(row["output"]).stat().st_size
                    for row in rows
                    if row["output"] and Path(row["output"]).is_file()
                )
                * 1.01
            )
            + 1048576
        )
        if required > int(os.getenv("MAX_EXPORT_BYTES", "268435456")):
            raise HTTPException(413, "Export exceeds configured size limit")
        with st.db() as c:
            c.execute("BEGIN IMMEDIATE")
            artifact_storage.reserve(c, st.ROOT, security.owner(), identifier, required)
        descriptor, filename = tempfile.mkstemp(
            prefix="export-", suffix=".zip", dir=exports
        )
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
                        st.csv_safe(r["text"]),
                        r["reference_id"],
                        r["error"] or "",
                    ]
                )
                if r["output"] and Path(r["output"]).is_file():
                    archive.write(
                        st.stored_audio(r["output"]),
                        f"audio/{r['model_id']}_{r['id']}.wav",
                    )
            archive.writestr("jobs.csv", out.getvalue())
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(["job_id", "metric", "value", "evaluator"])
            for m in metrics:
                writer.writerow([m["job_id"], m["name"], m["value"], m["evaluator"]])
            archive.writestr("metrics.csv", out.getvalue())
            archive.writestr(
                "metadata.json",
                json.dumps(
                    {
                        "batch_id": batch_id,
                        "generated_at": time.time(),
                        "jobs": provenance,
                    }
                ),
            )
        return PrivateExportResponse(
            target,
            release,
            media_type="application/zip",
            filename=f"voice-experiment-{batch_id}.zip",
            headers={"Cache-Control": "no-store"},
        )
    except BaseException:
        release()
        raise
