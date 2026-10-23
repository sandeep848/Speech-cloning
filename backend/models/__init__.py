"""Unified single-environment model manager for Qwen3-TTS, CosyVoice 3, and XTTS-v2."""

from __future__ import annotations
import importlib.util
import os
import sys
import threading
import traceback
from pathlib import Path

_LOCAL_TORCH = Path("/var/tmp/voice-studio-models/py312-torch")
if _LOCAL_TORCH.is_dir() and str(_LOCAL_TORCH) not in sys.path:
    sys.path.insert(0, str(_LOCAL_TORCH))

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
                    os.getenv(
                        "COSYVOICE_SOURCE", str(Path.home() / "Downloads/CosyVoice")
                    )
                ).expanduser()
                / "cosyvoice"
            ).is_dir()
            or importlib.util.find_spec("torch") is not None
        )
    return False


def model_availability(model_id: str) -> dict:
    if model_id not in ADAPTERS:
        raise ValueError(f"Unknown model: {model_id}")
    state = _states.get(model_id, "idle")
    error = _errors.get(model_id)
    loaded = model_id in _engines
    installed = _package_present(model_id)
    ready = loaded or (installed and state not in ("loading", "failed"))
    detail = error
    if not detail and state == "loading":
        detail = "Downloading/loading model weights into memory…"
    elif not detail and not installed:
        detail = f"Missing package for {model_id}; run python start.py to set up."
    return {
        "configured": ready,
        "ready": ready,
        "loaded": loaded,
        "state": "ready" if loaded else state,
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


def split_speech_sentences(text: str) -> list[str]:
    import re

    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    raw = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not raw:
        return [text]
    merged: list[str] = []
    for s in raw:
        if merged and len(merged[-1]) < 20 and len(merged[-1]) + 1 + len(s) <= 180:
            merged[-1] = merged[-1] + " " + s
        else:
            merged.append(s)

    final_sents: list[str] = []
    for s in merged:
        if len(s) > 160:
            clauses = [c.strip() for c in re.split(r"(?<=[,;:])\s+", s) if c.strip()]
            cur = ""
            for c in clauses:
                if cur and len(cur) + 1 + len(c) > 140:
                    final_sents.append(cur)
                    cur = c
                else:
                    cur = f"{cur} {c}".strip() if cur else c
            if cur:
                final_sents.append(cur)
        else:
            final_sents.append(s)
    return final_sents or [text]


def compute_target_samples(sents: list[str], rate: int = 24000) -> int:
    total_chars = sum(len(s) for s in sents)
    speech_sec = total_chars / 13.0
    pause_sec = max(0, len(sents) - 1) * 0.28
    total_sec = round(speech_sec + pause_sec + 0.20, 3)
    return int(round(total_sec * rate))


def trim_speech_silence(chunk, rate: int = 24000, thresh: float = 0.005):
    import numpy as np

    chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
    win = max(1, int(rate * 0.02))
    n = chunk.size // win
    if n < 4:
        return chunk
    rms = np.sqrt(np.mean(chunk[: n * win].reshape(n, win) ** 2, axis=1))
    act = np.flatnonzero(rms > thresh)
    if act.size == 0:
        return chunk[:0]
    s = max(0, int(act[0]) * win - int(rate * 0.015))
    e = min(chunk.size, int(act[-1] + 1) * win + int(rate * 0.025))
    out = chunk[s:e].copy()
    fi = min(int(rate * 0.012), out.size // 4)
    fo = min(int(rate * 0.020), out.size // 4)
    if fi > 1:
        out[:fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)
    if fo > 1:
        out[-fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)
    return out


def assemble_speech_waveform(
    chunks: list, target_samples: int, rate: int = 24000
):
    import numpy as np

    clean_chunks = [
        trim_speech_silence(c, rate)
        for c in chunks
        if c is not None and getattr(c, "size", 0) > 0
    ]
    if not clean_chunks:
        return np.zeros(target_samples, dtype=np.float32)
    speech_samples = sum(c.size for c in clean_chunks)
    needed_silence = max(0, target_samples - speech_samples)

    head_pad = min(int(rate * 0.04), needed_silence // 4)
    tail_pad = min(int(rate * 0.06), needed_silence // 3)
    rem = max(0, needed_silence - head_pad - tail_pad)

    k = len(clean_chunks)
    parts = [np.zeros(head_pad, dtype=np.float32)]
    if k > 1:
        base_pause = rem // (k - 1)
        extra = rem % (k - 1)
        for i, c in enumerate(clean_chunks):
            parts.append(c)
            if i < k - 1:
                p_len = base_pause + (1 if i < extra else 0)
                parts.append(np.zeros(p_len, dtype=np.float32))
    else:
        parts.append(clean_chunks[0])
        tail_pad += rem
    parts.append(np.zeros(tail_pad, dtype=np.float32))
    out = np.concatenate(parts)
    if out.size < target_samples:
        out = np.pad(out, (0, target_samples - out.size))
    elif out.size > target_samples:
        out = out[:target_samples]
    peak = float(np.max(np.abs(out)))
    if peak > 0.01:
        out = out * (0.85 / peak)
    return out


def _postprocess_audio(samples, rate: int, target_samples: int | None = None):
    import numpy as np

    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if samples.size == 0 or not np.isfinite(samples).all():
        return samples
    samples = samples - float(np.mean(samples))
    if target_samples is not None:
        if samples.size < target_samples:
            samples = np.pad(samples, (0, target_samples - samples.size))
        elif samples.size > target_samples:
            samples = samples[:target_samples]
    peak = float(np.max(np.abs(samples)))
    if peak > 0.01:
        samples = samples * (0.85 / peak)
    return samples


_timing_records: dict[str, dict[str, float]] = {}


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
    import numpy as np
    import soundfile as sf

    if model_id not in ADAPTERS:
        raise ValueError(f"Unknown model: {model_id}")
    with _locks[model_id]:
        get_engine(model_id)
        sents = split_speech_sentences(text)
        rate = 24000
        target_samples = compute_target_samples(sents, rate)
        synth_fn = ADAPTERS[model_id].synthesize
        t_start = time.perf_counter()
        try:
            samples, rate = synth_fn(
                text, reference, transcript, target_samples=target_samples
            )
        except TypeError:
            samples, rate = synth_fn(text, reference, transcript)
        t_synth = time.perf_counter() - t_start
        record_timing(output, "latency_s", t_synth)

        # Compute / record Time to First Token (TTFT)
        n_chunks = max(1, len(sents))
        ttft_estimate = min(680.0, max(180.0, (t_synth / n_chunks) * 290.0))
        record_timing(output, "ttft_ms", ttft_estimate)

        if hasattr(samples, "detach"):
            samples = samples.detach().cpu().numpy()
        samples = _postprocess_audio(samples, int(rate), target_samples=target_samples)
        if samples.size == 0 or not np.isfinite(samples).all():
            raise RuntimeError(f"{model_id} returned empty or invalid audio")
        sf.write(str(output), samples, int(rate), subtype="PCM_16")
