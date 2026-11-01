"""Optimization contracts; these do not claim GPU speed or perceptual gains."""

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models.qwen3 import model as qwen


def mock_qwen(
    monkeypatch, device="cuda:0", prompt=True, bad_count=False, different_rate=False
):
    calls = []
    prompt_calls = []

    class Engine:
        model = SimpleNamespace(device=device)

        def create_voice_clone_prompt(self, **kwargs):
            prompt_calls.append(kwargs)
            return ["speaker-prompt"]

        def generate_voice_clone(self, **kwargs):
            calls.append(kwargs)
            texts = (
                kwargs["text"] if isinstance(kwargs["text"], list) else [kwargs["text"]]
            )
            # Stable distinct outputs verify batch order survives assembly.
            outputs = [
                np.full(2400, (len(calls) * 10 + i) / 100, dtype="float32")
                for i in range(len(texts))
            ]
            return outputs[:-1] if bad_count else outputs, (
                22050 if different_rate and len(calls) > 1 else 24000
            )

    if not prompt:
        Engine.create_voice_clone_prompt = None
    # Simulate the public engine API without importing heavyweight libraries.
    engine = Engine()
    if not prompt:
        del Engine.create_voice_clone_prompt
    monkeypatch.setattr(qwen, "load", lambda: engine)
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(inference_mode=nullcontext)
    )
    monkeypatch.setenv("QWEN_SPEECH_RATE", "1")
    monkeypatch.delenv("QWEN_BATCH_SIZE", raising=False)
    return calls, prompt_calls


def test_qwen_first_phrase_stays_separate_and_remaining_phrases_batch(
    monkeypatch, tmp_path
):
    calls, prompts = mock_qwen(monkeypatch)
    ref = tmp_path / "reference.wav"
    ref.touch()
    text = "One, two. Three, four. Five."
    samples, rate = qwen.synthesize(text, ref, "Reference transcript")
    assert [c["text"] for c in calls] == [
        "One,",
        ["two.", "Three,"],
        ["four.", "Five."],
    ]
    assert [len(c["voice_clone_prompt"]) for c in calls] == [1, 2, 2]
    assert len(prompts) == 1 and rate == 24000
    # All five distinct model outputs are present in original text order.
    values = samples[np.r_[True, np.diff(samples) != 0]]
    assert np.allclose(values[values > 0], [0.10, 0.20, 0.21, 0.30, 0.31])


def test_qwen_cpu_defaults_to_unbatched_inference(monkeypatch, tmp_path):
    calls, _ = mock_qwen(monkeypatch, device="cpu")
    ref = tmp_path / "reference.wav"
    ref.touch()
    qwen.synthesize("One. Two. Three.", ref, "Ref")
    assert [c["text"] for c in calls] == ["One.", "Two.", "Three."]


def test_qwen_batch_limit_and_tail_reference_inputs(monkeypatch, tmp_path):
    calls, _ = mock_qwen(monkeypatch, prompt=False)
    monkeypatch.setenv("QWEN_BATCH_SIZE", "3")
    ref = tmp_path / "reference.wav"
    ref.touch()
    qwen.synthesize("One. Two. Three. Four. Five.", ref, "")
    assert [c["text"] for c in calls] == ["One.", ["Two.", "Three.", "Four."], "Five."]
    assert calls[1]["ref_audio"] == [str(ref)] * 3
    assert calls[1]["ref_text"] == [""] * 3
    assert all(c["x_vector_only_mode"] for c in calls)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"bad_count": True}, "number of audio chunks"),
        ({"different_rate": True}, "changed sample rate"),
    ],
)
def test_qwen_batch_integrity_checks(monkeypatch, tmp_path, kwargs, message):
    mock_qwen(monkeypatch, **kwargs)
    ref = tmp_path / "reference.wav"
    ref.touch()
    with pytest.raises(RuntimeError, match=message):
        qwen.synthesize("One. Two. Three.", ref, "Ref")


def test_qwen_invalid_batch_size_fails_clearly(monkeypatch, tmp_path):
    mock_qwen(monkeypatch)
    monkeypatch.setenv("QWEN_BATCH_SIZE", "0")
    ref = tmp_path / "reference.wav"
    ref.touch()
    with pytest.raises(ValueError, match="QWEN_BATCH_SIZE"):
        qwen.synthesize("One.", ref, "Ref")


def test_evaluators_default_to_cpu_even_when_gpu_exists(monkeypatch):
    from evaluator import Evaluator

    captured = []
    monkeypatch.delenv("EVAL_DEVICE", raising=False)
    for name in ("ECAPA", "SCOREQ", "DNSMOS"):
        monkeypatch.setenv("ENABLE_" + name, "0")
    monkeypatch.setenv("ENABLE_WHISPER", "1")

    def load_model(*args, **kwargs):
        captured.append(kwargs["device"])
        return object()

    monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(load_model=load_model))
    Evaluator()
    assert captured == ["cpu"]


def test_benchmark_statistics_preserve_actual_values_and_missing_metrics():
    from benchmark import metric_statistics

    rows = [{"model": "qwen3", "metrics": {"latency_s": value}} for value in [1, 2, 20]]
    rows.append({"model": "qwen3", "metrics": {"wer": 0.1}})
    rows.append({"model": "xtts-v2", "metrics": {"latency_s": 0.01}})
    stats = metric_statistics(rows, "qwen3")
    assert stats["latency_s"] == {
        "n": 3,
        "median": 2,
        "mean": 23 / 3,
        "min": 1,
        "max": 20,
        "p95_nearest_rank": 20,
    }
    assert stats["wer"]["n"] == 1 and stats["wer"]["median"] == 0.1
    assert "scoreq" not in stats


def test_reference_diagnostics_report_clipping_and_quiet_audio(tmp_path):
    import soundfile as sf
    from doctor import reference_diagnostics

    clipped = tmp_path / "clipped.wav"
    sf.write(clipped, np.ones(24000), 24000, subtype="FLOAT")
    report = reference_diagnostics(clipped)
    assert report["near_full_scale_fraction"] == 1
    assert any("clipping" in warning for warning in report["warnings"])
    quiet = tmp_path / "quiet.wav"
    sf.write(quiet, np.zeros(96000), 24000)
    report = reference_diagnostics(quiet)
    assert any("quiet" in warning for warning in report["warnings"])
    assert reference_diagnostics(tmp_path / "missing.wav")["error"]


def test_qwen_recovers_from_batch_oom_without_losing_text(monkeypatch, tmp_path):
    calls = []
    cleared = []

    class OOM(RuntimeError):
        pass

    class Engine:
        model = SimpleNamespace(device="cuda:0")

        def create_voice_clone_prompt(self, **kwargs):
            return ["prompt"]

        def generate_voice_clone(self, **kwargs):
            text = kwargs["text"]
            calls.append(text)
            if isinstance(text, list):
                raise OOM("batch does not fit")
            return [np.full(2400, 0.1, dtype="float32")], 24000

    monkeypatch.setattr(qwen, "load", lambda: Engine())
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            inference_mode=nullcontext,
            OutOfMemoryError=OOM,
            cuda=SimpleNamespace(empty_cache=lambda: cleared.append(True)),
        ),
    )
    monkeypatch.delenv("QWEN_BATCH_SIZE", raising=False)
    monkeypatch.setenv("QWEN_SPEECH_RATE", "1")
    ref = tmp_path / "reference.wav"
    ref.touch()
    qwen.synthesize("One. Two. Three. Four.", ref, "Ref")
    assert calls == ["One.", ["Two.", "Three."], "Two.", "Three.", "Four."]
    assert cleared == [True]
