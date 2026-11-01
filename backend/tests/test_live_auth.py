"""Real loopback HTTP and process restarts; no mocked authentication."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
import httpx


@contextmanager
def server(data, token):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(
        os.environ,
        APP_ENV="test",
        DEV_ALLOW_ANONYMOUS="0",
        API_TOKEN=token,
        PRELOAD_MODELS="0",
        VOICE_STUDIO_DATA=str(data),
        REFERENCE_DIR=str(data / "no-references"),
        COOKIE_SECURE="0",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers={"X-Studio-CSRF": "1"},
        trust_env=False,
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError("Backend exited before readiness")
            try:
                if client.get("/api/health").status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(0.05)
        else:
            raise AssertionError("Backend failed to start")
        yield client
    finally:
        client.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def test_token_rotation_on_actual_http_restart(tmp_path):
    with server(tmp_path, "old-test-token") as api:
        assert api.get("/api/models").status_code == 401
        assert (
            api.post("/api/session", json={"token": "old-test-token"}).status_code
            == 200
        )
        cookie = api.cookies.get("studio_session")
        assert api.get("/api/models").status_code == 200
    with server(tmp_path, "new-test-token") as api:
        api.cookies.set("studio_session", cookie)
        assert api.get("/api/models").status_code == 401
        assert (
            api.get(
                "/api/models", headers={"Authorization": "Bearer old-test-token"}
            ).status_code
            == 401
        )
        assert (
            api.post("/api/session", json={"token": "new-test-token"}).status_code
            == 200
        )
        assert api.get("/api/readiness").status_code == 200
    with server(tmp_path, "") as api:
        assert api.get("/api/models").status_code == 401
