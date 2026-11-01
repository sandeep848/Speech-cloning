"""Human evaluation ratings and agreement statistics."""

from __future__ import annotations
import logging
import math
from fastapi import APIRouter, HTTPException

import security
from api_schemas import RatingRequest

logger = logging.getLogger("studio")
router = APIRouter(prefix="/api", tags=["ratings"])


def _get_studio():
    import backend
    return backend


@router.post("/ratings")
def add_rating(body: RatingRequest):
    st = _get_studio()
    with st.db() as c:
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


@router.get("/ratings/{job_id}")
def ratings(job_id: str):
    st = _get_studio()
    with st.db() as c:
        security.require_owner(c, "job", job_id)
        rows = c.execute(
            "SELECT rater,category,rating FROM human_ratings WHERE job_id=?", (job_id,)
        ).fetchall()
    return [dict(x) for x in rows]


@router.get("/agreement")
def agreement(category: str = "naturalness"):
    st = _get_studio()
    with st.db() as c:
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
    except Exception:
        logger.exception("Agreement calculation failed")
        return {
            "status": "unavailable",
            "reason": "Agreement unavailable; contact the administrator",
            "kappa": None,
        }
