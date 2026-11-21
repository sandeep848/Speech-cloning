"""Strict PCM validation and atomic files shared by uploads and inference."""
import io
import os
import tempfile
import wave
from pathlib import Path


def validate_pcm(content, minimum=0.25, maximum=120):
    with wave.open(io.BytesIO(content), 'rb') as reader:
        rate, frames = reader.getframerate(), reader.getnframes()
        channels, width = reader.getnchannels(), reader.getsampwidth()
        if rate < 8000 or rate > 192000 or channels not in (1, 2) or width not in (1, 2, 3, 4):
            raise ValueError('Unsupported PCM format')
        if not minimum <= frames / rate <= maximum:
            raise ValueError('Invalid recording duration')
        if len(reader.readframes(frames)) != frames * channels * width:
            raise ValueError('Truncated PCM data')
    return rate, frames


def atomic_write(path, content):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
