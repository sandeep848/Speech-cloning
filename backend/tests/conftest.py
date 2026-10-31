"""Explicit, isolated test-only anonymous mode; clear abuse buckets per test."""
import os
import sys
import tempfile
from pathlib import Path
import pytest

os.environ['APP_ENV'] = 'test'
os.environ['VOICE_STUDIO_DATA'] = tempfile.mkdtemp(prefix='studio-suite-')
os.environ['PRELOAD_MODELS'] = '0'
os.environ['INFERENCE_ISOLATION'] = 'thread'
os.environ['API_TOKEN'] = ''
os.environ['ADMIN_TOKEN'] = ''
os.environ['AUTH_MODE'] = 'token'
os.environ['COOKIE_SECURE'] = '0'
os.environ['DEV_ALLOW_ANONYMOUS'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

@pytest.fixture(autouse=True)
def reset_rate_limits():
    import security
    security.limiter.clear()
    yield
