"""Shared-token session verification. This is not multi-user authentication."""
import hashlib
import hmac
import os
import time

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

