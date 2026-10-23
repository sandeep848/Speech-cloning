"""CosyVoice 3 model loader and zero-shot voice cloning inference in the unified environment."""

from __future__ import annotations
from functools import partial
import hashlib
import os
import re
import subprocess
import sys
import types
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR / "repo"
WEIGHTS_DIR = BASE_DIR / "weights"
HF_REPO_ID = os.getenv("COSY_HF_REPO", "FunAudioLLM/Fun-CosyVoice3-0.5B-2512")

_engine = None
_spk_cache: set[str] = set()


def _install_runtime_shims() -> None:
    """Stub unused training-only imports so CosyVoice 3 runs in the unified environment."""
    if "lightning" not in sys.modules:
        try:
            import lightning  # noqa: F401
        except ImportError:
            try:
                import pytorch_lightning as pl

                sys.modules["lightning"] = pl
                sys.modules["lightning.pytorch"] = pl
                sys.modules["lightning.pytorch.utilities"] = pl.utilities
            except ImportError:
                mod = types.ModuleType("lightning")
                sub = types.ModuleType("lightning.pytorch")
                util = types.ModuleType("lightning.pytorch.utilities")
                util.rank_zero_only = lambda fn: fn
                mod.pytorch = sub
                sub.utilities = util
                sys.modules["lightning"] = mod
                sys.modules["lightning.pytorch"] = sub
                sys.modules["lightning.pytorch.utilities"] = util

    if "matcha.models.components.decoder" not in sys.modules:
        dec = types.ModuleType("matcha.models.components.decoder")
        dec.Decoder = object
        sys.modules["matcha.models.components.decoder"] = dec

    if "pyworld" not in sys.modules:
        try:
            import pyworld  # noqa: F401
        except ImportError:
            sys.modules["pyworld"] = types.ModuleType("pyworld")

    if "modelscope" not in sys.modules:
        try:
            import modelscope  # noqa: F401
        except ImportError:
            from huggingface_hub import snapshot_download

            ms = types.ModuleType("modelscope")
            ms.snapshot_download = snapshot_download
            sys.modules["modelscope"] = ms


def _is_valid_weights_dir(d: Path) -> bool:
    if not (d / "cosyvoice3.yaml").is_file():
        return False
    blank_en = d / "CosyVoice-BlankEN"
    if not blank_en.is_dir():
        return False
    checkpoints = ["model.safetensors", "pytorch_model.bin"]
    return any((blank_en / cp).is_file() for cp in checkpoints)


def _ensure_repo_and_weights() -> Path:
    """Ensure CosyVoice source and Fun-CosyVoice3-0.5B weights exist."""
    external_repo = Path(
        os.getenv("COSYVOICE_SOURCE", str(Path.home() / "Downloads/CosyVoice"))
    ).expanduser()

    if not (REPO_DIR / "cosyvoice").is_dir():
        if (external_repo / "cosyvoice").is_dir():
            REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
            if not REPO_DIR.exists():
                REPO_DIR.symlink_to(external_repo, target_is_directory=True)
        else:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--recursive",
                    "https://github.com/FunAudioLLM/CosyVoice.git",
                    str(REPO_DIR),
                ],
                check=True,
            )

    for path in (REPO_DIR / "third_party/Matcha-TTS", REPO_DIR):
        path_str = str(path.resolve())
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

    env_weights = os.getenv("COSY_MODEL_DIR")
    target_dir = Path(env_weights).resolve() if env_weights else WEIGHTS_DIR.resolve()

    if _is_valid_weights_dir(target_dir):
        return target_dir

    if (
        WEIGHTS_DIR.exists()
        and WEIGHTS_DIR.resolve() != target_dir
        and _is_valid_weights_dir(WEIGHTS_DIR)
    ):
        return WEIGHTS_DIR.resolve()

    external_weights = external_repo / "pretrained_models/Fun-CosyVoice3-0.5B"
    if _is_valid_weights_dir(external_weights):
        if not target_dir.exists():
            try:
                target_dir.symlink_to(external_weights, target_is_directory=True)
                return target_dir
            except OSError:
                return external_weights.resolve()
        return external_weights.resolve()

    from huggingface_hub import snapshot_download

    target_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=HF_REPO_ID, local_dir=str(target_dir))
    return target_dir


def _patch_transformers_compat() -> None:
    """Ensure Qwen2Encoder.forward_one_step works with transformers >= 4.50 DynamicCache."""
    import torch
    from cosyvoice.llm.llm import Qwen2Encoder

    if getattr(Qwen2Encoder, "_patched_dynamic_cache", False):
        return

    def forward_one_step(self, xs, masks, cache=None):
        if cache is not None:
            if hasattr(cache, "get_seq_length"):
                past_len = cache.get_seq_length()
            elif isinstance(cache, (tuple, list)) and len(cache) > 0:
                past_len = cache[0][0].size(2)
            else:
                past_len = 0
            input_masks = torch.ones(
                (xs.size(0), past_len + xs.size(1)),
                dtype=torch.bool,
                device=xs.device,
            )
        else:
            input_masks = masks[:, -1, :]
        outs = self.model(
            inputs_embeds=xs,
            attention_mask=input_masks,
            output_hidden_states=True,
            return_dict=True,
            use_cache=True,
            past_key_values=cache,
        )
        return outs.hidden_states[-1], outs.past_key_values

    Qwen2Encoder.forward_one_step = forward_one_step
    Qwen2Encoder._patched_dynamic_cache = True


def _patch_multilingual_frontend(engine) -> None:
    """Prevent English wetext/inflect number-spelling from corrupting German/multilingual text."""
    if not hasattr(engine, "frontend"):
        return
    fe = engine.frontend
    if getattr(fe, "_patched_multilingual", False):
        return

    from cosyvoice.utils.frontend_utils import (
        contains_chinese,
        is_only_punctuation,
        split_paragraph,
    )

    orig_text_normalize = fe.text_normalize

    def _multilingual_text_normalize(text: str, split: bool = True, text_frontend: bool = True):
        lang = os.getenv("GENERATION_LANGUAGE", "German").strip().lower()
        if lang in ("english", "en", "chinese", "zh") or contains_chinese(text):
            return orig_text_normalize(text, split=split, text_frontend=text_frontend)
        if "<|" in text and "|>" in text:
            text_frontend = False
        if not text_frontend or text == "":
            return [text] if split else text
        clean = re.sub(r"\s+", " ", text).strip()
        if not split:
            return clean
        texts = list(
            split_paragraph(
                clean,
                partial(fe.tokenizer.encode, allowed_special=fe.allowed_special),
                "en",
                token_max_n=80,
                token_min_n=60,
                merge_len=20,
                comma_split=False,
            )
        )
        texts = [i for i in texts if not is_only_punctuation(i)]
        return texts or [clean]

    fe.text_normalize = _multilingual_text_normalize
    fe._patched_multilingual = True


def load():
    global _engine
    if _engine is not None:
        return _engine
    import torch
    weights_dir = _ensure_repo_and_weights()
    _install_runtime_shims()
    _patch_transformers_compat()
    from cosyvoice.cli.cosyvoice import AutoModel

    is_cuda = torch.cuda.is_available()
    _engine = AutoModel(model_dir=str(weights_dir), fp16=is_cuda)
    if hasattr(_engine, "frontend") and hasattr(_engine.frontend, "en_tn_model"):
        orig_norm = _engine.frontend.en_tn_model.normalize

        def _safe_normalize(t: str) -> str:
            t_clean = re.sub(r"\s+", " ", t).strip()
            if not t_clean:
                return t_clean
            try:
                return orig_norm(t_clean)
            except Exception:
                return t_clean

        _engine.frontend.en_tn_model.normalize = _safe_normalize
    _patch_multilingual_frontend(_engine)
    return _engine


def _normalize_german_text(text: str) -> str:
    """Normalize German numbers, dates, units, and symbols to spoken German words."""
    replacements = [
        (r"\b1980\b", "Neunzehnhundertachtzig"),
        (r"\b65\.000\b", "fünfundsechzigtausend"),
        (r"\b240\b", "zweihundertvierzig"),
        (r"\b1\.85\b", "eins Komma acht fünf"),
        (r"€", " Euro "),
        (r"%", " Prozent "),
        (r"\bTTS\b", "T-T-S"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    return re.sub(r"\s+", " ", text).strip()


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

    clean_transcript = _normalize_german_text(transcript)
    if not clean_transcript:
        raise RuntimeError("CosyVoice 3 requires backend/reference/reference.txt")
    clean_text = _normalize_german_text(text)
    if not clean_text:
        raise RuntimeError("CosyVoice 3 requires non-empty input text")
    engine = load()
    ref_resolved = reference.resolve()
    ref_str = str(ref_resolved)
    prompt_text = "You are a helpful assistant.<|endofprompt|>" + clean_transcript
    spk_id = hashlib.sha256(
        f"{ref_str}:{ref_resolved.stat().st_mtime}:{prompt_text}".encode()
    ).hexdigest()[:16]
    if spk_id not in _spk_cache:
        engine.add_zero_shot_spk(prompt_text, ref_str, spk_id)
        _spk_cache.add(spk_id)

    sentences = split_speech_sentences(clean_text)
    rate = int(engine.sample_rate)
    if target_samples is None:
        target_samples = compute_target_samples(sentences, rate)

    parts = []
    for sent in sentences:
        for chunk in engine.inference_zero_shot(
            tts_text=sent,
            prompt_text=prompt_text,
            prompt_wav=ref_str,
            zero_shot_spk_id=spk_id,
            stream=False,
            text_frontend=False,
        ):
            part = (
                chunk["tts_speech"]
                .detach()
                .cpu()
                .numpy()
                .astype(np.float32)
                .reshape(-1)
            )
            if part.size > 0:
                parts.append(part)

    if not parts:
        raise RuntimeError("CosyVoice 3 returned no audio")
    samples = assemble_speech_waveform(parts, target_samples, rate)
    return samples, rate
