"""Qwen3-TTS model loader and voice cloning inference."""

from __future__ import annotations
from collections import deque
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
    attn_impl = os.getenv("QWEN_ATTN_IMPLEMENTATION") or (
        "sdpa" if hasattr(torch.nn.functional, "scaled_dot_product_attention") else "eager"
    )
    if attn_impl not in ("eager", "sdpa", "flash_attention_2"):
        raise ValueError("QWEN_ATTN_IMPLEMENTATION must be eager, sdpa or flash_attention_2")
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
        # Batch subsequent phrases on CUDA; keep the first phrase separate so
        # a larger batch does not delay the first completed audio observation.
        device = str(getattr(getattr(engine, "model", None), "device", "cpu"))
        batch_size = int(os.getenv("QWEN_BATCH_SIZE", "2" if device.startswith("cuda") else "1"))
        if not 1 <= batch_size <= 8:
            raise ValueError("QWEN_BATCH_SIZE must be between 1 and 8")
        gen_kwargs = {
            "temperature": float(os.getenv("QWEN_TEMPERATURE", "0.6")),
            "top_p": float(os.getenv("QWEN_TOP_P", "0.85")),
            "top_k": int(os.getenv("QWEN_TOP_K", "30")),
            "subtalker_temperature": float(os.getenv("QWEN_SUBTALKER_TEMP", "0.6")),
            "subtalker_top_p": float(os.getenv("QWEN_SUBTALKER_TOP_P", "0.85")),
        }
        groups = deque([chunks[:1]] + [chunks[i:i + batch_size] for i in range(1, len(chunks), batch_size)])
        output_rate = None
        while groups:
            group = groups.popleft()
            if not group:
                continue
            batched = len(group) > 1
            opts = {
                "text": group if batched else group[0],
                "language": lang,
                "non_streaming_mode": True,
                **gen_kwargs,
            }
            if prompt_item is not None:
                opts["voice_clone_prompt"] = [prompt_item] * len(group)
            else:
                opts["ref_audio"] = [ref_str] * len(group) if batched else ref_str
                opts["ref_text"] = [clean_transcript] * len(group) if batched else clean_transcript
                if not clean_transcript:
                    opts["x_vector_only_mode"] = True
            out_of_memory = False
            try:
                wavs, rate = engine.generate_voice_clone(**opts)
            except getattr(torch, "OutOfMemoryError", ()):
                if not batched:
                    raise
                out_of_memory = True
            if out_of_memory:
                # Leave the exception scope before freeing cached allocations.
                # Never retry unrelated decoder failures or a single-phrase OOM.
                torch.cuda.empty_cache()
                groups = deque([[item] for pending in [group, *groups] for item in pending])
                print("[qwen3] GPU batch exceeded memory; retrying remaining phrases individually", flush=True)
                continue
            mark_first_audio()
            rate = int(rate)
            if len(wavs) != len(group):
                raise RuntimeError("Qwen returned a different number of audio chunks than input phrases")
            if output_rate is not None and output_rate != rate:
                raise RuntimeError("Qwen changed sample rate between phrase batches")
            output_rate = rate
            wav_parts.extend(np.asarray(wav, dtype=np.float32).reshape(-1) for wav in wavs)

        samples = assemble_speech_waveform(wav_parts, rate=rate, texts=chunks,
            pauses_ms=[item.pause_after_ms for item in plan],
            speech_rate=get_speech_rate("qwen3"))
        return samples, int(rate)
