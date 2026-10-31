"""Explicit, isolated test-only anonymous mode; clear abuse buckets per test."""
import os
import sys
from pathlib import Path
import pytest

os.environ['APP_ENV'] = 'test'
os.environ['DEV_ALLOW_ANONYMOUS'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

@pytest.fixture(autouse=True)
def reset_rate_limits():
    import security
    security.limiter.buckets.clear()
    yield
