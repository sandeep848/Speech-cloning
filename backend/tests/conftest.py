"""Explicit, isolated test-only anonymous mode; clear abuse buckets per test."""

import os
import sys
import tempfile
import wave
import math
import struct
from pathlib import Path
import pytest

os.environ["APP_ENV"] = "test"
os.environ["VOICE_STUDIO_DATA"] = tempfile.mkdtemp(prefix="studio-suite-")
os.environ["PRELOAD_MODELS"] = "0"
os.environ["INFERENCE_ISOLATION"] = "thread"
os.environ["API_TOKEN"] = ""
os.environ["ADMIN_TOKEN"] = ""
os.environ["AUTH_MODE"] = "token"
os.environ["COOKIE_SECURE"] = "0"
os.environ["DEV_ALLOW_ANONYMOUS"] = "1"
# Synthetic tone and transcript replace personal recordings in all API fixtures.
_reference = Path(tempfile.mkdtemp(prefix="studio-reference-fixture-"))
with wave.open(str(_reference / "reference.wav"), "wb") as sample:
    sample.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
    sample.writeframes(
        b"".join(
            struct.pack("<h", int(2500 * math.sin(2 * math.pi * 180 * i / 24000)))
            for i in range(12000)
        )
    )
(_reference / "reference.txt").write_text("Synthetic test reference.", encoding="utf-8")
os.environ["REFERENCE_DIR"] = str(_reference)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def reset_rate_limits():
    import security

    security.limiter.clear()
    yield
