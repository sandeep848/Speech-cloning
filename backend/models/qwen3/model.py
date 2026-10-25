"""Qwen3-TTS model loader and voice cloning inference."""

from __future__ import annotations
import os
import re
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "weights"
REPO_ID = os.getenv("QWEN_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")

_engine = None
_prompt_cache: dict[tuple[str, float, str], object] = {}


def _resolve_weights_dir() -> str:
    env_dir = os.getenv("QWEN_MODEL_DIR")
    target = Path(env_dir).resolve() if env_dir else MODEL_DIR

    if (target / "config.json").is_file():
        return str(target)

    # Check local HuggingFace cache (respecting HF_HOME)
    hf_home = Path(os.getenv("HF_HOME", str(Path.home() / ".cache/huggingface"))).resolve()
    for search_dir in (
        hf_home / "hub" / f"models--{REPO_ID.replace('/', '--')}" / "snapshots",
        Path.home() / ".cache/huggingface/hub" / f"models--{REPO_ID.replace('/', '--')}" / "snapshots",
    ):
        if search_dir.is_dir():
            snapshots = sorted(
                [p for p in search_dir.iterdir() if (p / "config.json").is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if snapshots:
                return str(snapshots[0])

    # First-time deployment: download weights into target
    from huggingface_hub import snapshot_download

    if target.is_symlink() and not target.exists():
        target.unlink()

    target.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=REPO_ID, local_dir=str(target))
    return str(target)


def load():
    global _engine
    if _engine is not None:
        return _engine
    import torch
    from models import select_device
    from qwen_tts import Qwen3TTSModel

    device = select_device("MODEL_DEVICE")
    dtype = (
        torch.bfloat16
        if device.startswith("cuda") and torch.cuda.is_bf16_supported()
        else torch.float16
        if device.startswith("cuda")
        else torch.float32
    )
    weights_path = _resolve_weights_dir()
    attn_impl = "sdpa" if hasattr(torch.nn.functional, "scaled_dot_product_attention") else "eager"
    _engine = Qwen3TTSModel.from_pretrained(
        weights_path,
        device_map=device,
        dtype=dtype,
        attn_implementation=attn_impl,
    )
    return _engine


def synthesize(
    text: str,
    reference: Path,
    transcript: str,
    target_samples: int | None = None,
):
    import numpy as np
    import torch
    from models import (
        assemble_speech_waveform,
        generation_language,
        mark_first_audio,
        plan_speech,
        get_speech_rate,
    )

    engine = load()
    lang = generation_language()
    ref_resolved = reference.resolve()
    ref_str = str(ref_resolved)
    ref_mtime = ref_resolved.stat().st_mtime
    clean_transcript = re.sub(r"\s+", " ", transcript).strip()
    plan = plan_speech(text)
    chunks = [item.text for item in plan]
    rate = 24000

    with torch.inference_mode():
        prompt_item = None
        if clean_transcript and hasattr(engine, "create_voice_clone_prompt"):
            cache_key = (ref_str, ref_mtime, clean_transcript)
            if cache_key not in _prompt_cache:
                items = engine.create_voice_clone_prompt(
                    ref_audio=ref_str,
                    ref_text=clean_transcript,
                    x_vector_only_mode=False,
                )
                _prompt_cache[cache_key] = items[0]
            prompt_item = _prompt_cache[cache_key]

        wav_parts = []
        for chunk_text in chunks:
            gen_kwargs = {
                "temperature": float(os.getenv("QWEN_TEMPERATURE", "0.6")),
                "top_p": float(os.getenv("QWEN_TOP_P", "0.85")),
                "top_k": int(os.getenv("QWEN_TOP_K", "30")),
                "subtalker_temperature": float(os.getenv("QWEN_SUBTALKER_TEMP", "0.6")),
                "subtalker_top_p": float(os.getenv("QWEN_SUBTALKER_TOP_P", "0.85")),
            }
            if prompt_item is not None:
                wavs, rate = engine.generate_voice_clone(
                    text=chunk_text,
                    language=lang,
                    voice_clone_prompt=[prompt_item],
                    non_streaming_mode=True,
                    **gen_kwargs,
                )
            else:
                opts = {
                    "text": chunk_text,
                    "language": lang,
                    "ref_audio": ref_str,
                    "ref_text": clean_transcript,
                    "non_streaming_mode": True,
                    **gen_kwargs,
                }
                if not clean_transcript:
                    opts["x_vector_only_mode"] = True
                wavs, rate = engine.generate_voice_clone(**opts)
            mark_first_audio()
            rate = int(rate)
            part = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
            wav_parts.append(part)

        samples = assemble_speech_waveform(wav_parts, rate=rate, texts=chunks,
            pauses_ms=[item.pause_after_ms for item in plan],
            speech_rate=get_speech_rate("qwen3"))
        return samples, int(rate)
