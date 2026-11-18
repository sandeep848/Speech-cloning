"""Behavioral regressions for extracted security/database boundaries."""
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.connection import connect
from app.security.session import authenticated, session_signature


def test_transaction_rolls_back_on_failure(tmp_path):
    database = tmp_path / "test.sqlite3"
    with connect(database) as c:
        c.execute("CREATE TABLE events (value TEXT)")
    with pytest.raises(RuntimeError):
        with connect(database) as c:
            c.execute("INSERT INTO events VALUES ('partial')")
            raise RuntimeError("abort")
    with connect(database) as c:
        assert c.execute("SELECT count(*) FROM events").fetchone()[0] == 0


@pytest.mark.parametrize("age", [-100, 43201])
def test_session_rejects_future_and_expired_cookie(monkeypatch, age):
    monkeypatch.setenv("API_TOKEN", "secret")
    stamp = str(int(time.time()) - age)
    request = SimpleNamespace(headers={}, cookies={"studio_session": stamp + "." + session_signature(stamp)})
    assert not authenticated(request)


def test_session_rejects_tampered_cookie(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret")
    request = SimpleNamespace(headers={}, cookies={"studio_session": str(int(time.time())) + ".invalid"})
    assert not authenticated(request)
