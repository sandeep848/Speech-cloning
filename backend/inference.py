"""Route each model to its own environment when a worker URL is configured."""

import io
import json
import os
import urllib.error
import urllib.request
import wave
from pathlib import Path


def worker_url(model_id):
    return os.getenv(
        "WORKER_" + model_id.upper().replace("-", "_") + "_URL", ""
    ).rstrip("/")


def model_availability(model_id):
    url = worker_url(model_id)
    if not url:
        from direct_inference import model_availability as local

        return local(model_id)
    try:
        request = urllib.request.Request(
            url + "/health",
            headers={"Authorization": "Bearer " + os.getenv("WORKER_TOKEN", "")},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            health = json.load(response)
        if health.get("model_id") != model_id:
            raise ValueError("Worker serves a different model")
        return {
            "configured": bool(health.get("configured", health.get("ready"))),
            "ready": bool(health.get("ready")),
            "loaded": bool(health.get("loaded")),
            "detail": health.get("error") or health.get("detail"),
            "transport": "worker",
        }
    except Exception as exc:
        return {
            "configured": False,
            "ready": False,
            "loaded": False,
            "detail": f"{model_id} worker unavailable: {exc}",
            "transport": "worker",
        }


def generate_audio(
    model_id: str, text: str, reference: Path, transcript: str, output: Path
):
    url = worker_url(model_id)
    if not url:
        from direct_inference import generate_audio as local

        return local(model_id, text, reference, transcript, output)
    payload = json.dumps(
        {
            "text": text,
            "reference_path": str(reference.resolve()),
            "reference_text": transcript,
            "language": os.getenv("GENERATION_LANGUAGE", "German"),
        }
    ).encode()
    request = urllib.request.Request(
        url + "/generate",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.getenv("WORKER_TOKEN", ""),
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=int(os.getenv("INFERENCE_TIMEOUT", "600"))
        ) as response:
            if response.headers.get_content_type() != "audio/wav":
                raise RuntimeError("Worker returned a non-WAV response")
            audio = response.read(30 * 1024 * 1024 + 1)
        if len(audio) > 30 * 1024 * 1024:
            raise RuntimeError("Worker output exceeds 30 MB")
        with wave.open(io.BytesIO(audio), "rb") as recording:
            if recording.getnframes() == 0:
                raise RuntimeError("Worker returned empty audio")
        output.write_bytes(audio)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode(errors="replace")
        raise RuntimeError(f"{model_id} worker: {detail}") from exc
