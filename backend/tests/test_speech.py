"""Audio regressions run without downloading models or requiring a GPU."""

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import models


def tone(rate=24000, seconds=0.5):
    return (
        0.1 * np.sin(2 * np.pi * 180 * np.arange(int(rate * seconds)) / rate)
    ).astype("float32")


@pytest.mark.parametrize("rate", [22050, 24000, 48000])
def test_natural_duration_never_padded_or_truncated(rate):
    audio = tone(rate, 2)
    for target in (100, rate * 30):
        result = models.assemble_speech_waveform([audio], target, rate)
        np.testing.assert_array_equal(result, audio)
        np.testing.assert_array_equal(
            models._postprocess_audio(audio, rate, target), audio
        )


def test_comma_and_sentence_boundaries_keep_context():
    text = "Dr. Müller zahlt 1.85 Euro, bzw. ca. zwei Euro. Danach geht er nach Hause."
    assert models.split_speech_sentences(text) == [text]
    chunks = models.split_speech_sentences("Hallo, Welt. " * 70)
    assert all(len(c) <= 220 for c in chunks)
    assert " ".join(chunks) == ("Hallo, Welt. " * 70).strip()
    assert all(len(c) <= 220 for c in models.split_speech_sentences("Wort " * 200))
    with pytest.raises(ValueError):
        models.split_speech_sentences("x" * 500)


def test_join_pause_is_bounded_and_preserves_all_speech():
    a = tone()
    result = models.assemble_speech_waveform(
        [a, a], 24000 * 40, texts=["Hello.", "World."]
    )
    assert len(result) == len(a) * 2 + int(24000 * 0.32)
    np.testing.assert_array_equal(result[: len(a)], a)
    np.testing.assert_array_equal(result[-len(a) :], a)


def test_only_excessive_internal_silence_is_shortened(monkeypatch):
    monkeypatch.setenv("MAX_INTERNAL_PAUSE_MS", "650")
    a = tone()
    short = np.r_[a, np.zeros(7200), a].astype("float32")
    np.testing.assert_array_equal(models.cap_long_pauses(short, 24000), short)
    long = np.r_[a, np.zeros(72000), a].astype("float32")
    result = models.cap_long_pauses(long, 24000)
    assert abs(len(result) - (len(a) * 2 + 15600)) <= 480
    np.testing.assert_array_equal(result[: len(a)], a)
    np.testing.assert_array_equal(result[-len(a) :], a)


def test_quiet_speech_and_partial_last_frame_are_preserved():
    audio = tone(seconds=0.503) * 0.001
    np.testing.assert_array_equal(models.trim_speech_silence(audio), audio)


@pytest.mark.parametrize("audio", [[], np.zeros(3000), [np.nan], [np.inf]])
def test_invalid_model_audio_fails(audio):
    with pytest.raises(RuntimeError):
        models.assemble_speech_waveform([audio])


def test_generation_runs_once_and_keeps_native_sample_rate(monkeypatch, tmp_path):
    calls = []

    def synthesize(*args):
        calls.append(args)
        models.mark_first_audio()
        return tone(48000, 1.5), 48000

    monkeypatch.setitem(
        models.ADAPTERS, "qwen3", SimpleNamespace(synthesize=synthesize)
    )
    monkeypatch.setattr(models, "get_engine", lambda _: None)
    output = tmp_path / "audio.wav"
    models.generate_audio("qwen3", "Hello, world.", tmp_path / "ref.wav", "ref", output)
    assert sf.info(output).duration == 1.5
    assert sf.info(output).samplerate == 48000
    assert len(calls) == 1
    assert models.get_timing(output, "first_audio_ms") >= 0
    assert models.get_timing(output, "ttft_ms") is None


def test_internal_typeerror_is_not_retried(monkeypatch, tmp_path):
    calls = []

    def synthesize(*args):
        calls.append(args)
        raise TypeError("decoder failed")

    monkeypatch.setitem(
        models.ADAPTERS, "qwen3", SimpleNamespace(synthesize=synthesize)
    )
    monkeypatch.setattr(models, "get_engine", lambda _: None)
    with pytest.raises(TypeError, match="decoder failed"):
        models.generate_audio("qwen3", "Hello", tmp_path / "ref", "", tmp_path / "out")
    assert len(calls) == 1


def test_language_is_request_local():
    barrier = threading.Barrier(2)

    def run(language):
        token = models._request_language.set(language)
        try:
            barrier.wait(timeout=2)
            return models.generation_language()
        finally:
            models._request_language.reset(token)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, ["German", "English"]))
    assert results == ["German", "English"]


def test_evaluator_does_not_invent_ttft_or_ffe(monkeypatch, tmp_path):
    from evaluator import Evaluator

    for key in ("WHISPER", "ECAPA", "SCOREQ", "DNSMOS"):
        monkeypatch.setenv("ENABLE_" + key, "0")
    path = tmp_path / "sample.wav"
    sf.write(path, tone(), 24000)
    result = dict(
        (k, v) for k, v, _ in Evaluator().evaluate(str(path), str(path), "Hello", 1)
    )
    assert not {"ttft_ms", "ffe", "first_audio_ms"} & result.keys()
    assert result["rtf"] == 2


def test_qwen_adapter_keeps_context_and_native_rate(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from models.qwen3 import model as adapter

    calls = []

    class Engine:
        def create_voice_clone_prompt(self, **kwargs):
            return ["cached-prompt"]

        def generate_voice_clone(self, **kwargs):
            calls.append(kwargs)
            return [tone(22050)], 22050

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(inference_mode=nullcontext, Tensor=type("Tensor", (), {})),
    )
    monkeypatch.setattr(adapter, "load", lambda: Engine())
    ref = tmp_path / "reference.wav"
    ref.touch()
    text = "Hello, world.\n\nAnother sentence."
    samples, rate = adapter.synthesize(
        text, ref, "Reference transcript", target_samples=999999
    )
    assert rate == 22050
    assert abs(len(samples) / rate - (1.5 / 0.92 + 0.15 + 0.60)) < 0.02
    assert [c["text"] for c in calls] == ["Hello,", "world.", "Another sentence."]
    assert all(c["voice_clone_prompt"] == ["cached-prompt"] for c in calls)


def test_xtts_uses_full_reference_conditioning(monkeypatch, tmp_path):
    from models.xtts_v2 import model as adapter

    calls, conditioning = [], []

    class Engine:
        output_sample_rate = 24000

        def get_conditioning_latents(self, **kwargs):
            conditioning.append(kwargs)
            return "latent", "speaker"

        def inference(self, **kwargs):
            calls.append(kwargs)
            return {"wav": tone(seconds=0.5 / kwargs["speed"])}

    engine = Engine()
    monkeypatch.setattr(
        adapter,
        "load",
        lambda: SimpleNamespace(
            synthesizer=SimpleNamespace(tts_model=engine, output_sample_rate=24000)
        ),
    )
    monkeypatch.delenv("XTTS_REPETITION_PENALTY", raising=False)
    ref = tmp_path / "reference.wav"
    ref.touch()
    for _ in range(2):
        samples, rate = adapter.synthesize("Hallo, Welt.\n\nNoch ein Satz.", ref, "")
        assert abs(len(samples) / rate - (1.5 / 0.96 + 0.15 + 0.60)) < 0.02
    assert len(conditioning) == 1
    assert conditioning[0]["audio_path"] == [str(ref)]
    assert conditioning[0]["gpt_cond_len"] == 30
    assert calls[0]["repetition_penalty"] == 10.0
    assert calls[0]["speed"] == 0.96


def test_cosy_internal_pieces_are_contiguous(monkeypatch, tmp_path):
    from models.cosyvoice3 import model as adapter

    class Tensor:
        def __init__(self, speed):
            self.speed = speed

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return tone(seconds=0.5 / self.speed)

    calls = []

    class Engine:
        sample_rate = 24000

        def add_zero_shot_spk(self, *args):
            pass

        def inference_zero_shot(self, **kwargs):
            calls.append(kwargs)
            yield {"tts_speech": Tensor(kwargs["speed"])}
            yield {"tts_speech": Tensor(kwargs["speed"])}

    monkeypatch.setattr(adapter, "load", lambda: Engine())
    ref = tmp_path / "reference.wav"
    ref.touch()
    text = "The year is 1980, and the price is 1.85 euros."
    samples, rate = adapter.synthesize(text, ref, "My reference transcript.")
    assert abs(len(samples) / rate - (2 / 0.96 + 0.15)) < 0.02
    assert len(calls) == 2
    assert " ".join(c["tts_text"] for c in calls) == text
    assert calls[1]["tts_text"] == "and the price is 1.85 euros."
    assert all(c["speed"] == 0.96 for c in calls)


def test_signal_diagnostics_known_levels_and_stereo(tmp_path):
    from evaluator import signal_diagnostics

    # Equal duration silence and near-full-scale stereo, with opposite phases.
    # Channel power must not cancel, and final short windows are sample-weighted.
    path = tmp_path / "diagnostics.wav"
    audio = np.concatenate([np.zeros((320, 2)), np.tile([1.0, -1.0], (320, 1))])
    sf.write(path, audio, 16000, subtype="FLOAT")
    metrics = {
        key: value for key, value, _ in signal_diagnostics(str(path), "Hello world")
    }
    assert metrics["quiet_frame_pct"] == 50
    assert metrics["near_clip_pct"] == 50
    assert metrics["script_wpm"] == 3000


def test_signal_diagnostics_silence_short_and_empty(tmp_path):
    from evaluator import signal_diagnostics

    path = tmp_path / "short.wav"
    sf.write(path, np.zeros(13), 16000)
    metrics = {key: value for key, value, _ in signal_diagnostics(str(path), "")}
    assert metrics == {"script_wpm": 0, "quiet_frame_pct": 100, "near_clip_pct": 0}
    sf.write(path, np.zeros(0), 16000)
    assert signal_diagnostics(str(path), "Hello") == []
