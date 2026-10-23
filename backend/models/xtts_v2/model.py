"""Coqui XTTS-v2 model loader and voice cloning inference."""

from __future__ import annotations
import os
import re
from pathlib import Path

WEIGHTS_DIR = Path(__file__).resolve().parent / "weights"
MODEL_NAME = os.getenv("XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")

_engine = None
_latent_cache: dict[tuple[str, float], tuple[object, object]] = {}


def _configure_weights_dir() -> None:
    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
    tts_home = Path(os.getenv("TTS_HOME", str(WEIGHTS_DIR))).resolve()
    default_tts = (
        Path.home()
        / ".local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2"
    )
    local_model = tts_home / "tts/tts_models--multilingual--multi-dataset--xtts_v2"
    if not local_model.is_dir() and default_tts.is_dir():
        local_model.parent.mkdir(parents=True, exist_ok=True)
        try:
            local_model.symlink_to(default_tts, target_is_directory=True)
        except OSError:
            pass
    tts_home.mkdir(parents=True, exist_ok=True)
    os.environ["TTS_HOME"] = str(tts_home)


def load():
    global _engine
    if _engine is not None:
        return _engine
    _configure_weights_dir()
    from models import select_device
    from TTS.tts.models.xtts import Xtts  # noqa: F401
    from TTS.api import TTS

    device = select_device("MODEL_DEVICE")
    _engine = TTS(model_name=MODEL_NAME).to(device)
    return _engine


def _trim_chunk(chunk, rate: int = 24000, n_chars: int = 40):
    """Trim leading and trailing silence without cutting internal sentence pauses."""
    import numpy as np

    chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
    if chunk.size == 0:
        return chunk
    win = max(1, int(rate * 0.02))
    n_wins = chunk.size // win
    if n_wins < 4:
        return chunk
    rms = np.sqrt(np.mean(chunk[: n_wins * win].reshape(n_wins, win) ** 2, axis=1))
    act = np.flatnonzero(rms > 0.005)
    if act.size == 0:
        return chunk[:0]

    first_idx = int(act[0])
    last_idx = int(act[-1])

    start = max(0, first_idx * win - int(rate * 0.02))
    end = min(chunk.size, (last_idx + 1) * win + int(rate * 0.04))
    trimmed = chunk[start:end].copy()
    fade_in = min(int(rate * 0.012), trimmed.size // 4)
    fade_out = min(int(rate * 0.020), trimmed.size // 4)
    if fade_in > 1:
        trimmed[:fade_in] *= np.linspace(0.0, 1.0, fade_in, dtype=np.float32)
    if fade_out > 1:
        trimmed[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, dtype=np.float32)
    return trimmed


def _extract_conditioning_paths(ref_path: Path) -> list[str]:
    """Extract multi-window reference slices for XTTS-v2 speaker embedding averaging.
    
    Averaging speaker latents across multiple reference windows substantially increases
    ECAPA speaker similarity and reduces vocoder artifacting without runtime overhead.
    """
    import hashlib
    import soundfile as sf

    ref_str = str(ref_path.resolve())
    try:
        info = sf.info(ref_str)
        if info.duration >= 6.5:
            cache_dir = Path("/tmp/xtts_slices")
            cache_dir.mkdir(parents=True, exist_ok=True)
            stat = ref_path.stat()
            h = hashlib.sha256(f"{ref_str}:{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()[:12]
            s1_path = cache_dir / f"slice1_{h}.wav"
            s2_path = cache_dir / f"slice2_{h}.wav"
            if not s1_path.is_file() or not s2_path.is_file():
                y, sr = sf.read(ref_str)
                s1_end = min(len(y), int(sr * 5.0))
                sf.write(str(s1_path), y[:s1_end], sr)
                s2_start = min(max(0, len(y) - int(sr * 2.0)), int(sr * 3.5))
                s2_end = min(len(y), int(sr * 8.5))
                if s2_end > s2_start:
                    sf.write(str(s2_path), y[s2_start:s2_end], sr)
                    return [str(s1_path), str(s2_path)]
                return [str(s1_path)]
            return [str(s1_path), str(s2_path)]
    except Exception:
        pass
    return [ref_str]


def synthesize(
    text: str,
    reference: Path,
    transcript: str,
    target_samples: int | None = None,
):
    import numpy as np
    from models import (
        assemble_speech_waveform,
        compute_target_samples,
        split_speech_sentences,
    )

    engine = load()
    lang = os.getenv("GENERATION_LANGUAGE", "German")
    aliases = {"German": "de", "English": "en", "Auto": "de"}
    lang = aliases.get(lang, lang)
    ref_resolved = reference.resolve()
    ref_str = str(ref_resolved)
    cache_key = (ref_str, ref_resolved.stat().st_mtime)

    model = engine.synthesizer.tts_model
    if cache_key not in _latent_cache:
        cond_paths = _extract_conditioning_paths(ref_resolved)
        gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
            audio_path=cond_paths,
            gpt_cond_len=6,
            gpt_cond_chunk_len=6,
            sound_norm_refs=True,
        )
        _latent_cache[cache_key] = (gpt_cond_latent, speaker_embedding)
    else:
        gpt_cond_latent, speaker_embedding = _latent_cache[cache_key]

    clean_text = re.sub(r"\s+", " ", text).strip()
    sents = split_speech_sentences(clean_text)
    rate = int(engine.synthesizer.output_sample_rate)
    if target_samples is None:
        target_samples = compute_target_samples(sents, rate)

    wav_chunks = []
    speed_val = float(os.getenv("XTTS_SPEED", "1.00"))
    temp_val = float(os.getenv("XTTS_TEMPERATURE", "0.75"))
    rep_val = float(os.getenv("XTTS_REPETITION_PENALTY", "3.5"))

    for sent in sents:
        out = model.inference(
            text=sent,
            language=lang,
            gpt_cond_latent=gpt_cond_latent,
            speaker_embedding=speaker_embedding,
            temperature=temp_val,
            length_penalty=1.0,
            repetition_penalty=rep_val,
            top_k=50,
            top_p=0.85,
            speed=speed_val,
            enable_text_splitting=False,
        )
        chunk = _trim_chunk(out["wav"], rate, len(sent))
        if chunk.size > 0:
            wav_chunks.append(chunk)

    if not wav_chunks:
        raise RuntimeError("XTTS-v2 returned no audio")
    samples = assemble_speech_waveform(wav_chunks, target_samples, rate)
    return samples, rate
