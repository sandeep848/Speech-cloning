"""Unified single-environment model manager for Qwen3-TTS, CosyVoice 3, and XTTS-v2."""

from __future__ import annotations
from contextvars import ContextVar
import importlib.util
import os
import threading
import traceback
from pathlib import Path

from models.cache import BoundedCache
from models.pacing import (
    get_speech_rate as get_speech_rate,
    plan_speech as plan_speech,
    slow_speech,
)

from models.qwen3 import model as qwen3_adapter
from models.cosyvoice3 import model as cosyvoice3_adapter
from models.xtts_v2 import model as xtts_v2_adapter

MODEL_IDS = ("qwen3", "cosyvoice3", "xtts-v2")
ADAPTERS = {
    "qwen3": qwen3_adapter,
    "cosyvoice3": cosyvoice3_adapter,
    "xtts-v2": xtts_v2_adapter,
}

_locks = {m: threading.RLock() for m in MODEL_IDS}
_import_lock = threading.RLock()
_request_language = ContextVar("generation_language", default=None)
_first_audio_clock = ContextVar("first_audio_clock", default=None)


def generation_language() -> str:
    return _request_language.get() or os.getenv("GENERATION_LANGUAGE", "German")


def mark_first_audio() -> None:
    """Measure the first completed audio chunk, never infer token latency."""
    import time

    clock = _first_audio_clock.get()
    if clock is not None and clock[1] is None:
        clock[1] = (time.perf_counter() - clock[0]) * 1000


_engines: dict[str, object] = {}
_states: dict[str, str] = {m: "idle" for m in MODEL_IDS}
_errors: dict[str, str | None] = {m: None for m in MODEL_IDS}


def select_device(env_var: str = "MODEL_DEVICE") -> str:
    configured = os.getenv(env_var)
    if configured:
        return configured
    try:
        import torch

        if torch.cuda.is_available():
            torch.ones(1, device="cuda:0")
            return "cuda:0"
    except Exception:
        pass
    return "cpu"


def _package_present(model_id: str) -> bool:
    if model_id == "qwen3":
        return importlib.util.find_spec("qwen_tts") is not None
    if model_id == "xtts-v2":
        return importlib.util.find_spec("TTS") is not None
    if model_id == "cosyvoice3":
        return (
            importlib.util.find_spec("cosyvoice") is not None
            or (cosyvoice3_adapter.REPO_DIR / "cosyvoice").is_dir()
            or (
                Path(
                    os.getenv("COSYVOICE_SOURCE", str(cosyvoice3_adapter.REPO_DIR))
                ).expanduser()
                / "cosyvoice"
            ).is_dir()
        )
    return False


def model_availability(model_id: str) -> dict:
    if model_id not in ADAPTERS:
        raise ValueError(f"Unknown model: {model_id}")
    state = _states.get(model_id, "idle")
    error = _errors.get(model_id)
    loaded = model_id in _engines
    installed = _package_present(model_id)
    configured = installed and state != "failed"
    ready = loaded and state == "ready"
    detail = error
    if not detail and state == "loading":
        detail = "Downloading/loading model weights into memory…"
    elif not detail and not installed:
        detail = f"Missing package for {model_id}; install the reviewed model environment and provision weights."
    return {
        "configured": configured,
        "ready": ready,
        "loaded": loaded,
        "state": "ready" if ready else state,
        "detail": detail,
    }


def get_engine(model_id: str):
    if model_id not in ADAPTERS:
        raise ValueError(f"Unknown model: {model_id}")
    with _locks[model_id]:
        if model_id in _engines:
            return _engines[model_id]
        _states[model_id] = "loading"
        _errors[model_id] = None
        try:
            # Serialize initial model imports/loads so transformers/torch never race
            with _import_lock:
                engine = ADAPTERS[model_id].load()
            _engines[model_id] = engine
            _states[model_id] = "ready"
            return engine
        except Exception as exc:
            _states[model_id] = "failed"
            _errors[model_id] = str(exc)
            raise


def preload_all_models() -> None:
    """Download any missing weights into backend/models/ and preload all 3 TTS engines."""
    for model_id in MODEL_IDS:
        try:
            print(f"[models] Loading {model_id}...", flush=True)
            get_engine(model_id)
            print(f"[models] {model_id} ready.", flush=True)
        except Exception as exc:
            print(f"[models] {model_id} failed to load: {exc}", flush=True)
            traceback.print_exc()


def split_speech_sentences(text: str, max_chars: int = 220) -> list[str]:
    """Bound context without splitting ordinary commas, decimals or common titles."""
    import re

    if max_chars < 20:
        raise ValueError("max_chars must be at least 20")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    # Protect decimal points and abbreviations before finding sentence boundaries.
    protected = re.sub(r"(?<=\d)\.(?=\d)", "\ue000", text)
    protected = re.sub(
        r"\b(?:Dr|Prof|Mr|Mrs|Ms|Sr|Jr|bzw|ca|usw|z|B|d|h)\.",
        lambda m: m[0].replace(".", "\ue000"),
        protected,
        flags=re.I,
    )
    units = re.split(r"(?<=[.!?])\s+", protected)
    chunks, current = [], ""
    for unit in units:
        unit = unit.replace("\ue000", ".")
        while len(unit) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            cut = unit.rfind(" ", 0, max_chars + 1)
            if cut <= 0:
                raise ValueError("A speech token exceeds the model context limit")
            chunks.append(unit[:cut].strip())
            unit = unit[cut:].strip()
        if current and len(current) + 1 + len(unit) > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = f"{current} {unit}".strip()
    if current:
        chunks.append(current)
    return chunks


def trim_speech_silence(chunk, rate: int = 24000, thresh: float = 0.001):
    """Trim only quiet edges, retaining 40 ms around detected activity."""
    import numpy as np

    chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
    if not chunk.size or not np.isfinite(chunk).all():
        raise RuntimeError("Model returned empty or non-finite audio")
    win = max(1, int(rate * 0.01))
    padded = np.pad(chunk, (0, (-chunk.size) % win))
    rms = np.sqrt(np.mean(padded.reshape(-1, win) ** 2, axis=1))
    peak_rms = float(rms.max())
    if peak_rms <= 1e-7:
        raise RuntimeError("Model returned silent audio")
    threshold = min(thresh, peak_rms * 0.02)
    active = np.flatnonzero(rms > threshold)
    margin = int(rate * 0.04)
    start = max(0, int(active[0]) * win - margin)
    end = min(chunk.size, int(active[-1] + 1) * win + margin)
    return chunk[start:end].copy()


def cap_long_pauses(samples, rate: int):
    """Cap only sustained very quiet gaps; preserve normal prosody and word endings."""
    import numpy as np

    max_pause = float(os.getenv("MAX_INTERNAL_PAUSE_MS", "650")) / 1000
    if not 0.15 <= max_pause <= 3.0:
        raise ValueError("MAX_INTERNAL_PAUSE_MS must be between 150 and 3000")
    win = max(1, int(rate * 0.01))
    padded = np.pad(samples, (0, (-samples.size) % win))
    rms = np.sqrt(np.mean(padded.reshape(-1, win) ** 2, axis=1))
    threshold = min(0.001, float(rms.max()) * 0.01)
    quiet = rms <= threshold
    edges = np.diff(np.r_[False, quiet, False].astype(int))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    keep = int(rate * max_pause)
    pieces, cursor = [], 0
    for start, end in zip(starts, ends):
        left, right = int(start) * win, min(samples.size, int(end) * win)
        if left > 0 and right < samples.size and right - left > keep + 2 * win:
            # Remove the middle of silence, leaving both speech boundaries intact.
            cut_start, cut_end = left + keep // 2, right - (keep - keep // 2)
            pieces.append(samples[cursor:cut_start])
            cursor = cut_end
    pieces.append(samples[cursor:])
    return np.concatenate(pieces)


def _edge_silence_samples(samples, rate: int) -> tuple[int, int]:
    """Count quiet edge frames so joins do not stack pauses on retained silence."""
    import numpy as np

    win = max(1, int(rate * 0.005))
    padded = np.pad(samples, (0, (-samples.size) % win))
    rms = np.sqrt(np.mean(padded.reshape(-1, win) ** 2, axis=1))
    active = np.flatnonzero(rms > min(0.001, float(rms.max()) * 0.02))
    if not active.size:
        raise RuntimeError("Model returned silent audio")
    return int(active[0]) * win, max(0, samples.size - int(active[-1] + 1) * win)


def assemble_speech_waveform(
    chunks: list,
    target_samples=None,
    rate: int = 24000,
    texts: list[str] | None = None,
    pauses_ms: list[float] | None = None,
    speech_rate: float = 1.0,
):
    """Preserve full phrases, gently slow speech and insert bounded boundary pauses."""
    import numpy as np

    if not chunks:
        raise RuntimeError("Model returned no audio chunks")
    if texts is not None and len(texts) != len(chunks):
        raise ValueError("Text and audio chunk counts must match")
    if pauses_ms is not None and len(pauses_ms) != len(chunks):
        raise ValueError("Pause and audio chunk counts must match")
    parts = []
    previous_tail = 0
    for i, chunk in enumerate(chunks):
        clean = cap_long_pauses(trim_speech_silence(chunk, rate), rate)
        head, tail = _edge_silence_samples(clean, rate)
        if speech_rate != 1.0:
            # Keep edge margins out of the vocoder: stretching quiet margins can
            # smear speech into a boundary and make the effective gap too short.
            end = clean.size - tail
            clean = np.concatenate(
                (clean[:head], slow_speech(clean[head:end], speech_rate), clean[end:])
            )
        if i:
            previous = texts[i - 1].rstrip() if texts else ""
            pause_ms = (
                pauses_ms[i - 1]
                if pauses_ms is not None
                else 320
                if previous.endswith((".", "!", "?"))
                else 150
            )
            pause_samples = max(0, int(rate * pause_ms / 1000) - previous_tail - head)
            parts.append(np.zeros(pause_samples, dtype=np.float32))
        parts.append(clean)
        previous_tail = tail
    return np.concatenate(parts)


def _postprocess_audio(samples, rate: int, target_samples=None):
    import numpy as np

    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if samples.size == 0 or not np.isfinite(samples).all():
        raise RuntimeError("Model returned empty or invalid audio")
    # Attenuate clipping risk; do not amplify a quiet recording or force its length.
    peak = float(np.max(np.abs(samples)))
    if peak > 0.95:
        samples = samples * (0.95 / peak)
    return samples


_timing_records = BoundedCache(1024)


def record_timing(path: str | Path, key: str, value: float) -> None:
    path_str = str(Path(path).resolve())
    if path_str not in _timing_records:
        _timing_records[path_str] = {}
    _timing_records[path_str][key] = value


def get_timing(path: str | Path, key: str) -> float | None:
    path_str = str(Path(path).resolve())
    return _timing_records.get(path_str, {}).get(key)


def generate_audio(
    model_id: str, text: str, reference: Path, transcript: str, output: Path
) -> None:
    import time
    import soundfile as sf

    if model_id not in ADAPTERS:
        raise ValueError(f"Unknown model: {model_id}")
    if not text.strip():
        raise ValueError("Speech text must not be empty")
    with _locks[model_id]:
        get_engine(model_id)
        clock = [time.perf_counter(), None]
        token = _first_audio_clock.set(clock)
        try:
            samples, rate = ADAPTERS[model_id].synthesize(text, reference, transcript)
        finally:
            _first_audio_clock.reset(token)
        if hasattr(samples, "detach"):
            samples = samples.detach().cpu().numpy()
        samples = _postprocess_audio(samples, int(rate))
        sf.write(str(output), samples, int(rate), subtype="PCM_16")
        record_timing(output, "latency_s", time.perf_counter() - clock[0])
        if clock[1] is not None:
            record_timing(output, "first_audio_ms", clock[1])
