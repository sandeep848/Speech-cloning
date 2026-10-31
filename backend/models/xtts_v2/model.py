"""Coqui XTTS-v2 model loader and voice cloning inference."""

from __future__ import annotations
import os
from pathlib import Path

WEIGHTS_DIR = Path(__file__).resolve().parent / "weights"
MODEL_NAME = os.getenv("XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")

_engine = None
from models.cache import BoundedCache
_latent_cache = BoundedCache()


def _configure_weights_dir() -> None:
    if os.getenv("COQUI_TOS_AGREED") != "1":
        raise RuntimeError("Review the XTTS model license and explicitly set COQUI_TOS_AGREED=1 to accept it")
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


def synthesize(
    text: str,
    reference: Path,
    transcript: str,
    target_samples: int | None = None,
):
    import numpy as np
    from models import (
        assemble_speech_waveform,
        generation_language,
        mark_first_audio,
        plan_speech,
        get_speech_rate,
    )

    engine = load()
    lang = generation_language()
    aliases = {"German": "de", "English": "en", "Auto": "de"}
    lang = aliases.get(lang, lang)
    ref_resolved = reference.resolve()
    ref_str = str(ref_resolved)
    cache_key = (ref_str, ref_resolved.stat().st_mtime)

    model = engine.synthesizer.tts_model
    if cache_key not in _latent_cache:
        cond_paths = [ref_str]
        gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
            audio_path=cond_paths,
            gpt_cond_len=30,
            gpt_cond_chunk_len=6,
            sound_norm_refs=True,
        )
        _latent_cache[cache_key] = (gpt_cond_latent, speaker_embedding)
    else:
        gpt_cond_latent, speaker_embedding = _latent_cache[cache_key]

    clean_text = text.strip()
    plan = plan_speech(clean_text)
    sents = [item.text for item in plan]
    rate = int(engine.synthesizer.output_sample_rate)

    wav_chunks = []
    speed_val = float(os.getenv("XTTS_SPEED", "1.00")) * get_speech_rate("xtts-v2")
    if not 0.5 <= speed_val <= 1.5:
        raise ValueError("Combined XTTS_SPEED and XTTS_SPEECH_RATE must be between 0.5 and 1.5")
    from generation_config import current
    temp_val = current.get().temperature if current.get().temperature is not None else float(os.getenv("XTTS_TEMPERATURE", "0.75"))
    rep_val = float(os.getenv("XTTS_REPETITION_PENALTY", "10.0"))

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
        mark_first_audio()
        chunk = np.asarray(out["wav"], dtype=np.float32).reshape(-1)
        if chunk.size > 0:
            wav_chunks.append(chunk)

    if not wav_chunks:
        raise RuntimeError("XTTS-v2 returned no audio")
    samples = assemble_speech_waveform(wav_chunks, rate=rate, texts=sents,
            pauses_ms=[item.pause_after_ms for item in plan],
            speech_rate=1.0)
    return samples, rate
