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


def test_missing_token_denies_access(monkeypatch):
    monkeypatch.delenv('API_TOKEN', raising=False)
    monkeypatch.delenv('ALLOW_INSECURE_LOCAL', raising=False)
    assert not authenticated(SimpleNamespace(headers={}, cookies={}))


def test_explicit_local_mode_rejects_network_clients(monkeypatch):
    monkeypatch.delenv('API_TOKEN', raising=False)
    monkeypatch.setenv('STUDIO_ENV', 'development')
    monkeypatch.setenv('ALLOW_INSECURE_LOCAL', '1')
    for host, expected in [('127.0.0.1', True), ('198.51.100.1', False)]:
        assert authenticated(SimpleNamespace(headers={}, cookies={}, client=SimpleNamespace(host=host))) is expected


def test_revocation_expiry_rotation_and_persistence(tmp_path, monkeypatch):
    from app.security.session import create_session, revoke_session
    monkeypatch.setenv('VOICE_STUDIO_DATA', str(tmp_path))
    monkeypatch.setenv('API_TOKEN', 'first-token')
    cookie, ttl = create_session()
    request = SimpleNamespace(headers={}, cookies={'studio_session': cookie})
    assert authenticated(request)
    with connect(tmp_path / 'studio.sqlite3') as c:
        assert cookie not in str(c.execute('SELECT * FROM sessions').fetchall()[0][:])
    monkeypatch.setenv('API_TOKEN', 'rotated-token')
    assert not authenticated(request)
    monkeypatch.setenv('API_TOKEN', 'first-token')
    revoke_session(cookie)
    assert not authenticated(request)
    cookie, _ = create_session()
    with connect(tmp_path / 'studio.sqlite3') as c:
        c.execute('UPDATE sessions SET expires=0')
    assert not authenticated(SimpleNamespace(headers={}, cookies={'studio_session': cookie}))


def test_rate_limit_window_and_capacity():
    from app.security.rate_limit import RateLimiter
    limiter = RateLimiter(capacity=1)
    assert limiter.allow('one', 2, now=0)
    assert limiter.allow('one', 2, now=1)
    assert not limiter.allow('one', 2, now=2)
    assert not limiter.allow('two', 2, now=2)
    assert limiter.allow('two', 2, now=62)


def test_deployment_config_fails_closed(monkeypatch):
    from app.config import validate_environment
    monkeypatch.delenv('API_TOKEN', raising=False)
    monkeypatch.delenv('ALLOW_INSECURE_LOCAL', raising=False)
    with pytest.raises(RuntimeError, match='API_TOKEN'):
        validate_environment([])
    monkeypatch.setenv('API_TOKEN', 'x' * 32)
    monkeypatch.setenv('STUDIO_ENV', 'production')
    monkeypatch.setenv('COOKIE_SECURE', '1')
    with pytest.raises(RuntimeError, match='CORS_ORIGINS'):
        validate_environment(['*'])
    validate_environment(['https://sandeep848.github.io'])
    monkeypatch.setenv('WEB_CONCURRENCY', '2')
    with pytest.raises(RuntimeError, match='one Uvicorn'):
        validate_environment(['https://sandeep848.github.io'])


def test_truncated_wav_is_rejected():
    import io
    import wave
    from app.services.audio import validate_pcm
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(bytes(32000))
    validate_pcm(stream.getvalue())
    with pytest.raises(ValueError, match='Truncated'):
        validate_pcm(stream.getvalue()[:-1000])


def test_atomic_write_failure_preserves_previous_file(tmp_path, monkeypatch):
    from app.services.audio import atomic_write
    target = tmp_path / 'audio.wav'
    target.write_bytes(b'previous')
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr('app.services.audio.os.replace', fail)
    with pytest.raises(OSError):
        atomic_write(target, b'new')
    assert target.read_bytes() == b'previous'
    assert list(tmp_path.iterdir()) == [target]


def test_restart_recovery_and_retry_exhaustion(tmp_path):
    from app.repositories import jobs
    database = tmp_path / 'jobs.sqlite3'
    with connect(database) as c:
        c.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT,error TEXT,updated REAL,created REAL,cache_key TEXT)')
        jobs.migrate(c)
        jobs.migrate(c)  # Idempotent migration on existing installations.
        for job in ('resume', 'exhausted', 'legacy'):
            c.execute("INSERT INTO jobs VALUES (?,'running',NULL,0,0,NULL)", (job,))
        jobs.save(c, 'resume', 'qwen3', 'hello', '/ref.wav', 'ref', 'German')
        jobs.save(c, 'exhausted', 'qwen3', 'hello', '/ref.wav', 'ref', 'German')
        c.execute("UPDATE job_inputs SET attempts=3 WHERE job_id='exhausted'")
        assert [r['id'] for r in jobs.recover(c, 3)] == ['resume']
        states = dict(c.execute('SELECT id,status FROM jobs').fetchall())
        assert states == {'resume': 'pending', 'exhausted': 'failed', 'legacy': 'failed'}


def test_backup_restore_and_corruption(tmp_path):
    from app.services.backup import backup, restore
    source, snapshot, restored = [tmp_path / name for name in ('source', 'snapshot', 'restored')]
    source.mkdir()
    audio = source / 'sample.wav'
    audio.write_bytes(b'synthetic recording')
    with connect(source / 'studio.sqlite3') as c:
        c.execute('CREATE TABLE jobs(id TEXT,status TEXT,output TEXT)')
        c.execute('CREATE TABLE references_audio(id TEXT,path TEXT)')
        c.execute("INSERT INTO jobs VALUES ('job','complete',?)", (str(audio),))
        c.execute("INSERT INTO references_audio VALUES ('ref',?)", (str(audio),))
    backup(source, snapshot)
    restore(snapshot, restored)
    with connect(restored / 'studio.sqlite3') as c:
        output = Path(c.execute('SELECT output FROM jobs').fetchone()[0])
        assert output == restored / 'audio' / 'sample.wav'
        assert output.read_bytes() == b'synthetic recording'
    with pytest.raises(ValueError, match='must not exist'):
        restore(snapshot, restored)
    (snapshot / 'audio' / 'sample.wav').write_bytes(b'corruption')
    with pytest.raises(ValueError, match='checksum'):
        restore(snapshot, tmp_path / 'corrupt-restore')
    assert not (tmp_path / 'corrupt-restore').exists()


def test_restart_reconciles_published_audio_without_rerunning(tmp_path):
    import wave
    from app.repositories import jobs
    output = tmp_path / 'published.wav'
    with wave.open(str(output), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(4800))
    with connect(tmp_path / 'restart.sqlite3') as c:
        c.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT,error TEXT,updated REAL,created REAL,cache_key TEXT,output TEXT)')
        c.execute("INSERT INTO jobs VALUES ('published','running',NULL,0,0,NULL,NULL)")
        jobs.migrate(c)
        assert jobs.recover(c, 3, tmp_path) == []
        row = c.execute('SELECT status,output FROM jobs').fetchone()
        assert row['status'] == 'complete'
        assert Path(row['output']) == output


def test_streamed_body_limit_without_content_length():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from app.security.body_limit import BodyLimitMiddleware
    app = FastAPI()
    @app.post('/')
    async def consume(request: Request):
        return {'bytes': len(await request.body())}
    app.add_middleware(BodyLimitMiddleware, maximum=4)
    with TestClient(app) as api:
        assert api.post('/', content=b'abcd').status_code == 200
        assert api.post('/', content=iter([b'abc', b'def'])).status_code == 413
        assert api.post('/', content=b'abcde').status_code == 413
