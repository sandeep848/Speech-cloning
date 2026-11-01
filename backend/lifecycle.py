"""Owned data deletion and opt-in retention, with shared-file reference tracking.

Database changes commit before unlinking files. A failed unlink leaves an orphan
for the next sweep, never a live database record pointing at a deleted file.
"""

from pathlib import Path
from fastapi import HTTPException


def remove_job(c, job_id):
    row = c.execute(
        "SELECT status,output,batch_id FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    if row["status"] in ("pending", "running"):
        raise HTTPException(409, "Cancel active generation before deleting it")
    evaluation = c.execute(
        "SELECT status FROM evaluation_runs WHERE job_id=?", (job_id,)
    ).fetchone()
    if evaluation and evaluation["status"] in ("pending", "running"):
        raise HTTPException(409, "Wait for evaluation before deleting this recording")
    for statement in (
        "DELETE FROM metrics WHERE job_id=?",
        "DELETE FROM human_ratings WHERE job_id=?",
        "DELETE FROM evaluation_errors WHERE job_id=?",
        "DELETE FROM evaluation_runs WHERE job_id=?",
        "DELETE FROM job_provenance WHERE job_id=?",
        "DELETE FROM job_observations WHERE job_id=?",
    ):
        c.execute(statement, (job_id,))
    c.execute("DELETE FROM storage_reservations WHERE id=?", (job_id,))
    c.execute(
        "DELETE FROM resource_owners WHERE kind='job' AND resource_id=?", (job_id,)
    )
    c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    if not c.execute(
        "SELECT 1 FROM jobs WHERE batch_id=?", (row["batch_id"],)
    ).fetchone():
        c.execute(
            "DELETE FROM batch_generations WHERE generation_id=?", (row["batch_id"],)
        )
        c.execute("DELETE FROM batches WHERE id=?", (row["batch_id"],))
        c.execute(
            "DELETE FROM resource_owners WHERE kind='generation' AND resource_id=?",
            (row["batch_id"],),
        )
    return [row["output"]] if row["output"] else []


def remove_reference(c, reference_id):
    row = c.execute(
        "SELECT path FROM references_audio WHERE id=?", (reference_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Reference not found")
    if c.execute(
        "SELECT 1 FROM batches WHERE reference_id=?", (reference_id,)
    ).fetchone():
        raise HTTPException(409, "Delete generations using this reference first")
    if c.execute(
        "SELECT 1 FROM settings WHERE key=?", ("shared-reference:" + reference_id,)
    ).fetchone():
        raise HTTPException(403, "Shared references are managed by the administrator")
    c.execute(
        "DELETE FROM user_reference_profiles WHERE reference_id=?", (reference_id,)
    )
    c.execute("DELETE FROM reference_versions WHERE reference_id=?", (reference_id,))
    c.execute(
        "DELETE FROM resource_owners WHERE kind='reference' AND resource_id=?",
        (reference_id,),
    )
    c.execute("DELETE FROM references_audio WHERE id=?", (reference_id,))
    return [row["path"]]


def discard_unreferenced(c, paths, root):
    """Called in the same write transaction as removal; returns safe unlink paths."""
    discard = []
    for path in set(paths):
        if c.execute(
            "SELECT 1 FROM jobs WHERE output=? UNION ALL SELECT 1 FROM references_audio WHERE path=?",
            (path, path),
        ).fetchone():
            continue
        resolved = Path(path).resolve()
        if resolved.is_relative_to(root / "audio"):
            c.execute("DELETE FROM artifact_sizes WHERE path=?", (path,))
            discard.append(resolved)
    return discard


def unlink(paths):
    import logging

    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logging.getLogger("studio").exception("Orphan artifact requires cleanup")
