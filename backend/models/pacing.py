"""Punctuation-aware phrasing and gentle, pitch-preserving speech-rate control."""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
import re


@dataclass(frozen=True)
class SpeechChunk:
    text: str
    pause_after_ms: float


def _pause(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or not 0 <= value <= 1500:
        raise ValueError(f"{name} must be between 0 and 1500 milliseconds")
    return value


def plan_speech(text: str, max_chars: int = 220) -> list[SpeechChunk]:
    """Keep paragraph boundaries and phrase-ending punctuation in each model input.

    Decimal/thousands separators, common titles, initials and abbreviations do
    not introduce artificial speech breaks. Model context remains word-bounded.
    """
    if max_chars < 20:
        raise ValueError("max_chars must be at least 20")
    comma = _pause("COMMA_PAUSE_MS", 150)
    sentence = _pause("SENTENCE_PAUSE_MS", 320)
    paragraph = _pause("PARAGRAPH_PAUSE_MS", 600)
    continuation = _pause("CONTINUATION_PAUSE_MS", 90)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = [re.sub(r"\s+", " ", p).strip()
                  for p in re.split(r"\n[ \t]*\n+", text) if p.strip()]
    result: list[SpeechChunk] = []
    for paragraph_index, content in enumerate(paragraphs):
        protected = set()
        for pattern in (
            r"(?<=\d)[.,](?=\d)",
            r"\b(?:Dr|Prof|Mr|Mrs|Ms|Sr|Jr|bzw|ca|usw|etc|z|B|d|h)\.",
            r"\b(?:[A-Za-z]\.){2,}",
            r"\b[A-Z]\.(?=\s+[A-ZÄÖÜ])",
        ):
            for match in re.finditer(pattern, content):
                protected.update(range(match.start(), match.end()))
        boundaries = []
        for match in re.finditer(r'''[.!?]+["'”’»)]*(?=\s|$)|[,;:](?=\s|$)''', content):
            if match.start() in protected:
                continue
            pause = sentence if content[match.start()] in '.!?' else comma
            boundaries.append((match.end(), pause))
        if not boundaries or boundaries[-1][0] != len(content):
            boundaries.append((len(content), continuation))
        start = 0
        for end, pause in boundaries:
            phrase = content[start:end].strip()
            start = end
            while len(phrase) > max_chars:
                cut = phrase.rfind(" ", 0, max_chars + 1)
                if cut <= 0:
                    raise ValueError("A speech token exceeds the model context limit")
                result.append(SpeechChunk(phrase[:cut].strip(), continuation))
                phrase = phrase[cut:].strip()
            if phrase:
                result.append(SpeechChunk(phrase, pause))
        if paragraph_index < len(paragraphs) - 1 and result:
            result[-1] = SpeechChunk(result[-1].text, paragraph)
    return result


def get_speech_rate(model_id: str) -> float:
    variable = {"qwen3": "QWEN_SPEECH_RATE", "cosyvoice3": "COSY_SPEECH_RATE",
                "xtts-v2": "XTTS_SPEECH_RATE"}[model_id]
    default = "0.92" if model_id == "qwen3" else "0.96"
    rate = float(os.getenv(variable, os.getenv("SPEECH_RATE", default)))
    if not math.isfinite(rate) or not 0.8 <= rate <= 1.1:
        raise ValueError(f"{variable} must be between 0.8 and 1.1; 1.0 keeps native speed")
    return rate


def slow_speech(samples, rate: float):
    """Stretch speech, not the sample-rate header or inserted punctuation pauses."""
    import numpy as np
    if rate == 1.0:
        return samples
    import librosa
    # 1024/256 windows retain more consonant detail than long default windows.
    stretched = librosa.effects.time_stretch(
        samples, rate=rate, n_fft=1024, hop_length=256
    )
    stretched = np.asarray(stretched, dtype=np.float32)
    if not stretched.size or not np.isfinite(stretched).all():
        raise RuntimeError("Speech rate processing returned invalid audio")
    return stretched
