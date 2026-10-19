"""Run one resident model per environment; never expose workers to the internet."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from direct_inference import generate_audio, get_engine, model_availability
import tempfile

MODEL_ID = os.getenv("MODEL_ID", "")
if MODEL_ID not in ("qwen3", "cosyvoice3", "xtts-v2"):
    raise RuntimeError("Set MODEL_ID to qwen3, cosyvoice3 or xtts-v2")


@asynccontextmanager
async def lifespan(app):
    preload()
    yield


app = FastAPI(title=f"{MODEL_ID} worker", lifespan=lifespan)
_error = None


def authorize(authorization):
    import hmac

    token = os.getenv("WORKER_TOKEN")
    if token and not hmac.compare_digest(authorization or "", "Bearer " + token):
        raise HTTPException(401, "Unauthorized")


class Request(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    reference_path: str
    reference_text: str = Field(default="", max_length=4000)
    language: str = "German"


def preload():
    global _error
    if os.getenv("PRELOAD_MODELS", "1") == "1":
        try:
            get_engine(MODEL_ID)
        except Exception as exc:
            _error = str(exc)


@app.get("/health")
def health(authorization: str | None = Header(None)):
    authorize(authorization)
    result = model_availability(MODEL_ID)
    return {
        **result,
        "ready": result["configured"] and _error is None,
        "model_id": MODEL_ID,
        "error": _error,
    }


@app.post("/generate")
def generate(body: Request, authorization: str | None = Header(None)):
    global _error
    authorize(authorization)
    ref = Path(body.reference_path).resolve()
    shared = (
        Path(
            os.getenv("VOICE_STUDIO_DATA", Path(__file__).resolve().parents[1] / "data")
        ).resolve()
        / "audio"
    )
    if (
        not ref.is_file()
        or not ref.is_relative_to(shared)
        or ref.suffix.lower() != ".wav"
    ):
        raise HTTPException(
            422, "Reference path is not inside the shared audio directory"
        )
    # Workers have a fixed language; differing requests must not race via environment mutation.
    if body.language != os.getenv("GENERATION_LANGUAGE", "German"):
        raise HTTPException(422, "Worker GENERATION_LANGUAGE differs from the API")
    output = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporary:
            output = Path(temporary.name)
        generate_audio(MODEL_ID, body.text, ref, body.reference_text, output)
        _error = None
        return Response(output.read_bytes(), media_type="audio/wav")
    except Exception as exc:
        _error = str(exc)
        raise HTTPException(503, f"{MODEL_ID}: {exc}") from exc
    finally:
        if output:
            output.unlink(missing_ok=True)
