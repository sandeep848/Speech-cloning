"""Model adapters inside the FastAPI process; no network worker services.

Install each model library and its weights in the SAME environment as FastAPI.
Each model has its own lock; distinct models can run simultaneously.
"""

from __future__ import annotations
import importlib.util
import os
import threading
from pathlib import Path

_locks = {model: threading.RLock() for model in ("qwen3", "cosyvoice3", "xtts-v2")}
_engines = {}


def model_availability(model_id: str) -> dict:
    package = {"qwen3": "qwen_tts", "cosyvoice3": "cosyvoice", "xtts-v2": "TTS"}[
        model_id
    ]
    try:
        installed = importlib.util.find_spec(package) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        installed = False
    configured = installed and (
        model_id != "cosyvoice3"
        or bool(
            os.getenv("COSY_MODEL_DIR") and Path(os.environ["COSY_MODEL_DIR"]).is_dir()
        )
    )
    return {
        "ready": configured,
        "configured": configured,
        "loaded": model_id in _engines,
        "detail": None
        if configured
        else (
            {
                "qwen3": "Install qwen-tts in this interpreter, or set WORKER_QWEN3_URL",
                "cosyvoice3": "Install the official CosyVoice repository, set PYTHONPATH and COSY_MODEL_DIR, or set WORKER_COSYVOICE3_URL",
                "xtts-v2": "Install coqui-tts (imports as TTS), or set WORKER_XTTS_V2_URL",
            }[model_id]
        ),
    }


def get_engine(model_id: str):
    with _locks[model_id]:
        if model_id in _engines:
            return _engines[model_id]
        import torch

        device = os.getenv(
            "MODEL_DEVICE", "cuda:0" if torch.cuda.is_available() else "cpu"
        )
        if model_id == "qwen3":
            from qwen_tts import Qwen3TTSModel

            engine = Qwen3TTSModel.from_pretrained(
                os.getenv("QWEN_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-Base"),
                device_map=device,
                dtype=torch.bfloat16 if device.startswith("cuda") else torch.float32,
                attn_implementation="eager",
            )
        elif model_id == "cosyvoice3":
            from cosyvoice.cli.cosyvoice import AutoModel

            directory = os.getenv("COSY_MODEL_DIR")
            if not directory or not Path(directory).is_dir():
                raise RuntimeError(
                    "Set COSY_MODEL_DIR to your installed CosyVoice 3 model directory"
                )
            engine = AutoModel(model_dir=directory)
        elif model_id == "xtts-v2":
            from TTS.api import TTS

            engine = TTS(
                model_name=os.getenv(
                    "XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2"
                )
            ).to(device)
        else:
            raise ValueError("Unknown model: " + model_id)
        _engines[model_id] = engine
        return engine


def generate_audio(
    model_id: str, text: str, reference: Path, transcript: str, output: Path
) -> None:
    import numpy as np
    import soundfile as sf

    with _locks[model_id]:
        engine = get_engine(model_id)
        if model_id == "qwen3":
            opts = {
                "text": text,
                "language": os.getenv("GENERATION_LANGUAGE", "German"),
                "ref_audio": str(reference),
                "ref_text": transcript,
                "non_streaming_mode": True,
            }
            if not transcript.strip():
                opts["x_vector_only_mode"] = True
            result, rate = engine.generate_voice_clone(**opts)
            samples = result[0]
        elif model_id == "cosyvoice3":
            if not transcript.strip():
                raise RuntimeError(
                    "CosyVoice 3 requires backend/reference/reference.txt"
                )
            chunks = list(
                engine.inference_zero_shot(
                    tts_text=text,
                    prompt_text="You are a helpful assistant.<|endofprompt|>"
                    + transcript,
                    prompt_wav=str(reference),
                    stream=False,
                )
            )
            if not chunks:
                raise RuntimeError("CosyVoice 3 returned no audio")
            samples = np.concatenate(
                [
                    chunk["tts_speech"].detach().cpu().numpy().reshape(-1)
                    for chunk in chunks
                ]
            )
            rate = int(engine.sample_rate)
        elif model_id == "xtts-v2":
            lang = os.getenv("GENERATION_LANGUAGE", "German")
            aliases = {"German": "de", "English": "en", "Auto": "de"}
            lang = aliases.get(lang, lang)
            samples = engine.tts(text=text, speaker_wav=str(reference), language=lang)
            rate = int(engine.synthesizer.output_sample_rate)
        else:
            raise ValueError("Unknown model")
        if hasattr(samples, "detach"):
            samples = samples.detach().cpu().numpy()
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if samples.size == 0 or not np.isfinite(samples).all():
            raise RuntimeError("Model returned empty or invalid audio")
        sf.write(str(output), samples, rate, subtype="PCM_16")
