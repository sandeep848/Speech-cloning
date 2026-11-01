"""Authentication and session management routes."""

from __future__ import annotations
import hashlib
import logging
import os
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import security
from api_schemas import Login, PasswordReset

logger = logging.getLogger("studio")
router = APIRouter(prefix="/api", tags=["auth"])


def _get_studio():
    import backend
    return backend


@router.post("/session")
def login(body: Login, request: Request):
    st = _get_studio()
    source = request.client.host if request.client else "unknown"
    account_name = (
        "legacy"
        if os.getenv("AUTH_MODE", "token") == "token"
        else body.username.casefold().strip()
    )
    account = hashlib.sha256(account_name.encode()).hexdigest()
    failure_key = ("login-failures", source, account)
    if not security.limiter.allow(
        ("login-ip", source), 120, 300
    ) or security.limiter.blocked(failure_key, 10):
        logger.warning("Login rate limit reached")
        raise HTTPException(
            429, "Too many login attempts; retry later", headers={"Retry-After": "300"}
        )
    failure = None
    with st.db() as c:
        try:
            cookie = security.authenticate(c, body.token if hasattr(body, "token") else "", body.username, body.password)
            security.event(c, "login_success", request.state.request_id)
        except HTTPException as error:
            security.event(c, "login_failure", request.state.request_id)
            failure = error
    if failure:
        security.limiter.allow(failure_key, 10, 300)
        logger.warning("Login failed request=%s", request.state.request_id)
        raise failure
    response = JSONResponse(
        {"authenticated": True}
        | ({"session_token": cookie} if body.transport == "bearer" else {})
    )
    secure = os.getenv("COOKIE_SECURE", "0") == "1"
    response.set_cookie(
        "studio_session",
        cookie,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=security.SESSION_SECONDS,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/password-reset")
def reset_password(body: PasswordReset, request: Request):
    from manage_accounts import valid_password

    st = _get_studio()
    source = request.client.host if request.client else "unknown"
    if not security.limiter.allow(("reset", source), 10, 300):
        raise HTTPException(429, "Too many requests; retry later")
    try:
        valid_password(body.password)
    except ValueError:
        raise HTTPException(422, "Use a unique password of at least 12 characters")
    digest = hashlib.sha256(body.token.encode()).hexdigest()
    with st.db() as c:
        if not c.execute(
            "SELECT 1 FROM password_resets WHERE digest=? AND expires>?",
            (digest, time.time()),
        ).fetchone():
            raise HTTPException(400, "Invalid or expired reset token")
    hashed = security.passwords.hash(body.password)
    with st.db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "DELETE FROM password_resets WHERE digest=? AND expires>? RETURNING owner_id",
            (digest, time.time()),
        ).fetchone()
        if not row:
            raise HTTPException(400, "Invalid or expired reset token")
        c.execute(
            "UPDATE accounts SET password_hash=?,credential_version=credential_version+1 WHERE id=?",
            (hashed, row["owner_id"]),
        )
        c.execute("DELETE FROM sessions WHERE owner_id=?", (row["owner_id"],))
    return {"status": "password_reset"}


@router.get("/session")
def session_state():
    return {
        "authenticated": security.principal.get() is not None,
        "auth_mode": os.getenv("AUTH_MODE", "token"),
        "principal": security.principal.get(),
    }


@router.delete("/session")
def logout(request: Request):
    st = _get_studio()
    with st.db() as c:
        c.execute(
            "DELETE FROM sessions WHERE digest=?",
            (hashlib.sha256(security.session_value(request).encode()).hexdigest(),),
        )
    response = JSONResponse({"authenticated": False})
    response.delete_cookie("studio_session", path="/")
    return response
