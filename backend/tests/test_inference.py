import io
import json
import wave
import urllib.error
import pytest
import inference


class Response(io.BytesIO):
    class Headers:
        def get_content_type(self):
            return "audio/wav"

    headers = Headers()


def test_worker_valid_wav_and_request(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKER_QWEN3_URL", "http://worker.test")
    monkeypatch.setenv("WORKER_TOKEN", "worker-secret")
    data = io.BytesIO()
    with wave.open(data, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(bytes(4800))

    def open_worker(request, timeout):
        assert request.full_url == "http://worker.test/generate"
        assert request.get_header("Authorization") == "Bearer worker-secret"
        payload = json.loads(request.data)
        assert payload["language"] == "German"
        assert payload["reference_text"] == "Exact transcript"
        return Response(data.getvalue())

    monkeypatch.setattr(inference.urllib.request, "urlopen", open_worker)
    output = tmp_path / "result.wav"
    inference.generate_audio(
        "qwen3", "Hello", tmp_path / "reference.wav", "Exact transcript", output
    )
    assert output.read_bytes() == data.getvalue()


def test_worker_invalid_wav_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKER_QWEN3_URL", "http://worker.test")
    monkeypatch.setattr(
        inference.urllib.request, "urlopen", lambda *args, **kw: Response(b"not audio")
    )
    output = tmp_path / "result.wav"
    with pytest.raises((wave.Error, EOFError)):
        inference.generate_audio(
            "qwen3", "Hello", tmp_path / "ref.wav", "Transcript", output
        )
    assert not output.exists()


def test_worker_error_includes_detail(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKER_QWEN3_URL", "http://worker.test")

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "http://worker.test",
            503,
            "failed",
            {},
            io.BytesIO(b'{"detail":"Missing checkpoint"}'),
        )

    monkeypatch.setattr(inference.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="Missing checkpoint"):
        inference.generate_audio(
            "qwen3", "Hello", tmp_path / "ref.wav", "Transcript", tmp_path / "out.wav"
        )
