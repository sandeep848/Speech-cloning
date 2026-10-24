import os
import shutil
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from fastapi.testclient import TestClient

backend_dir = Path(__file__).resolve().parents[1]
project_root = Path(__file__).resolve().parents[2]
os.environ["VOICE_STUDIO_DATA"] = tempfile.mkdtemp(prefix="studio-test-")
os.environ["PRELOAD_MODELS"] = "0"
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(backend_dir))

import backend
import direct_inference


def test_health_reference_models():
    with TestClient(backend.app) as api:
        assert api.get("/api/health").status_code == 200
        profile = api.get("/api/reference-profile")
        assert profile.status_code == 200
        assert profile.json()["reference"] is not None
        assert "transcript" not in profile.json()
        models = api.get("/api/models")
        assert models.status_code == 200
        assert len(models.json()) == 3


def test_generation_validation_and_missing_metric():
    with TestClient(backend.app) as api:
        assert (
            api.post(
                "/api/generations", json={"text": "Hello", "models": ["not-a-model"]}
            ).status_code
            == 422
        )
        assert api.get("/api/evaluations/not-real").status_code == 404
        assert api.post("/api/evaluations/not-real").status_code == 404


def test_audio_range_and_reference_stability():
    with TestClient(backend.app) as api:
        ref = api.get("/api/reference-profile").json()["reference"]["id"]
        r = api.get(f"/api/references/{ref}/audio", headers={"Range": "bytes=0-127"})
        assert r.status_code == 206
        assert len(r.content) == 128


def test_evaluation_saved_and_deduplicated(monkeypatch):
    with TestClient(backend.app) as api:
        reference = api.get("/api/reference-profile").json()["reference"]["id"]
        batch = "test-batch"
        job = "test-job"
        output = Path(os.environ["VOICE_STUDIO_DATA"]) / "audio" / "test-result.wav"
        with wave.open(str(output), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(bytes(24000 * 2))
        with backend.db() as c:
            c.execute(
                "INSERT INTO batches VALUES (?,?,?,?)",
                (batch, "Das ist ein Test.", reference, time.time()),
            )
            c.execute(
                "INSERT INTO jobs (id,batch_id,model_id,status,output,created,updated,cache_key,runtime_seconds) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job,
                    batch,
                    "qwen3",
                    "complete",
                    str(output),
                    time.time(),
                    time.time(),
                    "test",
                    2.5,
                ),
            )

        class FakeEvaluator:
            errors = {}

            def evaluate(self, output, reference, text, elapsed):
                assert elapsed == 2.5
                return [
                    ("duration_s", 1.0, "wav"),
                    ("latency_s", elapsed, "clock"),
                    ("rtf", 2.5, "clock"),
                    ("wer", 0.0, "whisper+jiwer"),
                    ("cer", 0.0, "whisper+jiwer"),
                    ("ecapa_similarity", 0.92, "speechbrain-ecapa"),
                    ("scoreq", 4.1, "scoreq"),
                ]

        monkeypatch.setattr(backend, "_evaluator", FakeEvaluator())
        assert backend.schedule_evaluation(job) in ("scheduled", "running")
        for _ in range(40):
            result = api.get("/api/evaluations/" + job).json()
            if result["status"] == "completed":
                break
            time.sleep(0.03)
        assert result["status"] == "completed", result
        assert len(result["metrics"]) == 7
        assert api.post("/api/evaluations/" + job).json()["status"] == "completed"


def test_root_removed_routes_and_validation():
    with TestClient(backend.app) as api:
        assert api.get("/").status_code == 200
        assert (
            api.post(
                "/api/generations", json={"text": "   ", "models": ["qwen3"]}
            ).status_code
            == 422
        )
        assert api.patch("/api/workspace", json={"active": ["bad"]}).status_code == 422


def test_auth_cookie_audio_and_cors(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-secret")
    with TestClient(backend.app) as api:
        assert api.get("/api/health").status_code == 200
        assert api.get("/api/models").status_code == 401
        assert (
            api.options(
                "/api/models",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                },
            ).status_code
            == 200
        )
        assert api.post("/api/session", json={"token": "bad"}).status_code == 401
        assert (
            api.post(
                "/api/session",
                json={"token": "test-secret"},
                headers={"Origin": "https://untrusted.test"},
            ).status_code
            == 403
        )
        login = api.post("/api/session", json={"token": "test-secret"})
        assert login.status_code == 200
        assert "httponly" in login.headers["set-cookie"].lower()
        ref = api.get("/api/reference-profile").json()["reference"]["id"]
        assert (
            api.get(
                "/api/references/" + ref + "/audio", headers={"Range": "bytes=0-127"}
            ).status_code
            == 206
        )
        assert api.delete("/api/session").status_code == 200
        assert api.get("/api/models").status_code == 401
        assert (
            api.get(
                "/api/models", headers={"Authorization": "Bearer test-secret"}
            ).status_code
            == 200
        )


def test_parallel_models_and_cache(monkeypatch):
    monkeypatch.setenv("AUTO_EVALUATE", "0")
    monkeypatch.setattr(
        direct_inference,
        "model_availability",
        lambda model: {"configured": True, "ready": True},
    )
    barrier = threading.Barrier(3, timeout=3)
    calls = []

    def synthesize(model, text, reference, transcript, output):
        calls.append(model)
        barrier.wait()
        with wave.open(str(output), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(bytes(4800))

    monkeypatch.setattr(direct_inference, "generate_audio", synthesize)
    with TestClient(backend.app) as api:
        payload = {
            "text": "Concurrent synthesis regression",
            "models": list(backend.MODEL_IDS),
        }
        result = api.post("/api/generations", json=payload)
        assert result.status_code == 202, result.text
        for _ in range(100):
            jobs = api.get("/api/generations/" + result.json()["id"]).json()["jobs"]
            if all(j["status"] not in ("pending", "running") for j in jobs):
                break
            time.sleep(0.02)
        assert all(j["status"] == "complete" for j in jobs), jobs
        assert len(calls) == 3
        assert all(j["runtime_seconds"] >= 0 for j in jobs)
        cached = api.post("/api/generations", json=payload).json()
        assert all(j["cached"] for j in cached["jobs"])
        assert len(calls) == 3


def test_missing_model_is_actionable(monkeypatch):
    monkeypatch.setattr(
        direct_inference,
        "model_availability",
        lambda model: {"configured": False, "detail": "Install the required package"},
    )
    with TestClient(backend.app) as api:
        result = api.post(
            "/api/generations", json={"text": "Hello", "models": ["xtts-v2"]}
        )
        assert result.status_code == 503
        assert "Install" in result.json()["detail"]


def test_evaluator_init_errors_persist(monkeypatch, tmp_path):
    import evaluator

    monkeypatch.setenv("ENABLE_WHISPER", "0")
    monkeypatch.setenv("ENABLE_ECAPA", "0")
    monkeypatch.setenv("ENABLE_SCOREQ", "0")
    monkeypatch.setenv("ENABLE_DNSMOS", "0")
    engine = evaluator.Evaluator()
    engine.initialization_errors = {"ecapa": "Checkpoint unavailable"}
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(48000))
    for _ in range(2):
        measured = engine.evaluate(str(path), str(path), "Hello", 2)
        assert engine.errors == {"ecapa": "Checkpoint unavailable"}
        assert ("rtf", 2.0, "clock") in measured
        engine.errors.clear()


def test_batch_trials_bypass_cache_and_track_children(monkeypatch):
    calls = []

    async def fake_generate(request):
        calls.append(request)
        return {"id": "trial-" + str(len(calls)), "jobs": []}

    monkeypatch.setattr(backend, "generate", fake_generate)
    monkeypatch.setattr(backend, "_batch_generations", lambda batch_id: ["tracked"])
    with TestClient(backend.app) as api:
        reference = api.get("/api/reference-profile").json()["reference"]["id"]
        result = api.post(
            "/api/batches",
            json={
                "reference_id": reference,
                "models": ["qwen3"],
                "texts": ["Trial"],
                "repetitions": 2,
            },
        )
        assert result.status_code == 202, result.text
        assert len(calls) == 2 and all(not request.use_cache for request in calls)
        with backend.db() as c:
            children = c.execute(
                "SELECT generation_id FROM batch_generations WHERE batch_id=?",
                (result.json()["id"],),
            ).fetchall()
        assert len(children) == 2
        assert api.get("/api/batches/" + result.json()["id"]).json()["generations"] == [
            "tracked"
        ]


def test_auth_failure_includes_cors_headers(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-secret")
    origin = backend.ORIGINS[0]
    with TestClient(backend.app) as api:
        for path in ("/api/models", "/api/reference-profile"):
            response = api.get(path, headers={"Origin": origin})
            assert response.status_code == 401
            assert response.json() == {"detail": "Unauthorized"}
            assert response.headers["access-control-allow-origin"] == origin
            assert response.headers["access-control-allow-credentials"] == "true"
        blocked = api.get("/api/models", headers={"Origin": "https://untrusted.test"})
        assert blocked.status_code == 401
        assert "access-control-allow-origin" not in blocked.headers


def test_https_cross_site_login_cookie(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-secret")
    monkeypatch.setenv("COOKIE_SECURE", "1")
    origin = backend.ORIGINS[0]
    with TestClient(backend.app, base_url="https://backend.test") as api:
        response = api.post(
            "/api/session", json={"token": "test-secret"}, headers={"Origin": origin}
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie
        assert "samesite=none" in cookie
        assert api.get("/api/models", headers={"Origin": origin}).status_code == 200


def test_local_http_login_cookie_remains_usable(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-secret")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    with TestClient(backend.app) as api:
        response = api.post("/api/session", json={"token": "test-secret"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "samesite=lax" in cookie
        assert "; secure" not in cookie
        assert api.get("/api/models").status_code == 200
