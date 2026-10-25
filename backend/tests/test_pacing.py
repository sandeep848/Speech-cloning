"""Pacing regressions: text boundaries, actual gap lengths, tempo and pitch."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models import assemble_speech_waveform
from models.pacing import get_speech_rate, plan_speech, slow_speech


def test_distinct_comma_sentence_and_paragraph_pauses():
    plan = plan_speech('Hello, this is a sentence. Next sentence!\n\nNew paragraph, then goodbye.')
    assert [c.text for c in plan] == [
        'Hello,', 'this is a sentence.', 'Next sentence!', 'New paragraph,', 'then goodbye.']
    assert [c.pause_after_ms for c in plan] == [150, 320, 600, 150, 320]


def test_paragraph_break_after_unpunctuated_text_is_retained():
    plan = plan_speech('First paragraph\r\n\r\nSecond paragraph')
    assert [c.text for c in plan] == ['First paragraph', 'Second paragraph']
    assert plan[0].pause_after_ms == 600


def test_single_wrapped_line_does_not_create_paragraph_pause():
    plan = plan_speech('A sentence wrapped\nonto another line.')
    assert len(plan) == 1 and plan[0].text == 'A sentence wrapped onto another line.'


def test_numbers_titles_initials_and_quotes_are_preserved():
    text = 'Dr. A. Müller zahlt 1,85 Euro, bzw. 65.000 Euro. "Goodbye!"'
    plan = plan_speech(text)
    assert [c.text for c in plan] == [
        'Dr. A. Müller zahlt 1,85 Euro,', 'bzw. 65.000 Euro.', '"Goodbye!"']
    assert ' '.join(c.text for c in plan) == text


def test_long_input_is_word_bounded_without_changing_content():
    text = 'word ' * 100 + 'done.\n\nA second paragraph.'
    plan = plan_speech(text)
    assert all(len(c.text) <= 220 for c in plan)
    assert ' '.join(c.text for c in plan) == ' '.join(text.split())
    assert any(c.pause_after_ms == 600 for c in plan)
    with pytest.raises(ValueError):
        plan_speech('x' * 300)


def test_user_can_tune_pauses(monkeypatch):
    monkeypatch.setenv('COMMA_PAUSE_MS', '180')
    monkeypatch.setenv('SENTENCE_PAUSE_MS', '350')
    monkeypatch.setenv('PARAGRAPH_PAUSE_MS', '700')
    plan = plan_speech('Hi, there.\n\nNext. End.')
    assert [c.pause_after_ms for c in plan] == [180, 700, 350, 350]


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', '2000'])
def test_invalid_pause_settings_fail_clearly(monkeypatch, value):
    monkeypatch.setenv('COMMA_PAUSE_MS', value)
    with pytest.raises(ValueError, match='COMMA_PAUSE_MS'):
        plan_speech('Hi, there.')


def test_model_rate_defaults_and_override_precedence(monkeypatch):
    for name in ('SPEECH_RATE', 'QWEN_SPEECH_RATE', 'COSY_SPEECH_RATE', 'XTTS_SPEECH_RATE'):
        monkeypatch.delenv(name, raising=False)
    assert get_speech_rate('qwen3') == .92
    assert get_speech_rate('cosyvoice3') == get_speech_rate('xtts-v2') == .96
    monkeypatch.setenv('SPEECH_RATE', '0.95')
    assert get_speech_rate('qwen3') == .95
    monkeypatch.setenv('QWEN_SPEECH_RATE', '1.0')
    assert get_speech_rate('qwen3') == 1.0
    monkeypatch.setenv('QWEN_SPEECH_RATE', 'nan')
    with pytest.raises(ValueError, match='QWEN_SPEECH_RATE'):
        get_speech_rate('qwen3')


@pytest.mark.parametrize('sample_rate', [22050, 24000, 48000])
@pytest.mark.parametrize('speed', [.92, .96])
def test_slowdown_extends_speech_without_lowering_pitch(sample_rate, speed):
    t = np.arange(sample_rate) / sample_rate
    signal = (.1 * np.sin(2 * np.pi * 180 * t)).astype('float32')
    result = slow_speech(signal, speed)
    assert len(result) == round(len(signal) / speed)
    assert np.isfinite(result).all()
    # Ignore transform edges when checking the preserved dominant frequency.
    center = result[sample_rate // 5:-sample_rate // 5]
    frequencies = np.fft.rfftfreq(center.size, 1 / sample_rate)
    peak = frequencies[np.argmax(np.abs(np.fft.rfft(center)))]
    assert abs(peak - 180) < 2


def test_punctuation_gaps_are_not_stretched_or_doubled():
    sr = 24000
    signal = (.1 * np.sin(2 * np.pi * 180 * np.arange(sr // 2) / sr)).astype('float32')
    # A model already supplied quiet margins. They must count toward the join.
    audio = np.r_[np.zeros(1200), signal, np.zeros(1200)].astype('float32')
    result = assemble_speech_waveform([audio] * 4, rate=sr,
        pauses_ms=[150, 320, 600, 0], speech_rate=.92)
    win = 120  # five-ms RMS windows
    rms = np.sqrt(np.mean(result[:len(result)//win*win].reshape(-1, win)**2, axis=1))
    quiet = rms < .0001
    edges = np.diff(np.r_[False, quiet, False].astype(int))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    internal = [(b-a)*5 for a,b in zip(starts, ends) if a > 0 and b < len(rms) and (b-a)*5 > 50]
    assert len(internal) == 3
    assert np.allclose(internal, [150, 320, 600], atol=15)


def test_assembly_rejects_missing_text_chunk():
    with pytest.raises(ValueError, match='counts must match'):
        assemble_speech_waveform([np.ones(100)], texts=['one', 'two'])
