"""Security regressions use synthetic users/audio; never real inference or data."""
import hashlib
import io
import os
import secrets
import time
import uuid
import wave

import pytest
from fastapi.testclient import TestClient
import backend
import security


def audio(channels=1):
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(12000 * channels * 2))
    return out.getvalue()


def provision(name):
    identifier = str(uuid.uuid4())
    with backend.db() as c:
        c.execute('INSERT INTO principals VALUES (?)', (identifier,))
        c.execute('INSERT INTO accounts(id,username,password_hash) VALUES (?,?,?)',
                  (identifier, name, security.passwords.hash('unique test password 578!')))
    return identifier


@pytest.fixture
def accounts(monkeypatch):
    monkeypatch.setenv('AUTH_MODE', 'accounts')
    monkeypatch.setenv('AUTH_EPOCH', 'test-epoch')
    monkeypatch.setenv('AUTO_EVALUATE', '0')
    name = secrets.token_hex(4)
    users = [provision(name+'a'), provision(name+'b')]
    with TestClient(backend.app, headers={'X-Studio-CSRF': '1'}) as a, TestClient(backend.app, headers={'X-Studio-CSRF': '1'}) as b:
        for client, suffix in [(a, 'a'), (b, 'b')]:
            assert client.post('/api/session', json={'username':name+suffix, 'password':'unique test password 578!'}).status_code == 200
        yield a, b, users


def test_missing_credentials_fail_closed(monkeypatch):
    monkeypatch.delenv('API_TOKEN', raising=False)
    monkeypatch.delenv('DEV_ALLOW_ANONYMOUS', raising=False)
    with TestClient(backend.app) as api:
        assert api.get('/api/health').status_code == 200
        for path in ['/api/models', '/api/workspace', '/api/experiments', '/api/readiness']:
            assert api.get(path).status_code == 401
        assert api.post('/api/session', json={'token':''}).status_code == 401


def test_production_configuration(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('INFERENCE_ISOLATION', 'process')
    monkeypatch.setenv('DEV_ALLOW_ANONYMOUS', '0')
    monkeypatch.setenv('COOKIE_SECURE', '1')
    monkeypatch.setenv('CORS_ORIGINS', 'https://studio.test')
    monkeypatch.delenv('API_TOKEN', raising=False)
    with pytest.raises(RuntimeError, match='API_TOKEN'):
        with TestClient(backend.app):
            pass
    monkeypatch.setenv('API_TOKEN', secrets.token_urlsafe(48))
    security.validate_configuration()
    monkeypatch.setenv('COOKIE_SECURE', '0')
    with pytest.raises(RuntimeError, match='COOKIE_SECURE'):
        security.validate_configuration()


def test_rotation_expiry_tampering_and_logout(monkeypatch):
    monkeypatch.setenv('API_TOKEN', 'old-token-for-test')
    monkeypatch.delenv('DEV_ALLOW_ANONYMOUS', raising=False)
    with TestClient(backend.app, headers={'X-Studio-CSRF':'1'}) as api:
        assert api.post('/api/session', json={'token':'old-token-for-test'}).status_code == 200
        cookie = api.cookies.get('studio_session')
        assert api.get('/api/models').status_code == 200
        monkeypatch.setenv('API_TOKEN', 'new-token-for-test')
        assert api.get('/api/models').status_code == 401
        assert api.get('/api/models', headers={'Authorization':'Bearer old-token-for-test'}).status_code == 401
        assert api.get('/api/models', headers={'Authorization':'Bearer new-token-for-test'}).status_code == 200
        api.cookies.set('studio_session', cookie+'tampered')
        assert api.get('/api/models').status_code == 401
        api.cookies.clear()
        assert api.post('/api/session', json={'token':'new-token-for-test'}).status_code == 200
        current = api.cookies.get('studio_session')
        with backend.db() as c:
            c.execute('UPDATE sessions SET expires=0 WHERE digest=?', (hashlib.sha256(current.encode()).hexdigest(),))
        assert api.get('/api/models').status_code == 401
        api.cookies.clear()
        api.post('/api/session', json={'token':'new-token-for-test'})
        current = api.cookies.get('studio_session')
        assert api.delete('/api/session').status_code == 200
        api.cookies.set('studio_session', current)
        assert api.get('/api/models').status_code == 401
        monkeypatch.delenv('API_TOKEN')
        assert api.get('/api/models').status_code == 401


def test_two_users_all_resources_isolated(accounts, monkeypatch):
    a, b, users = accounts
    original = backend.REFERENCE_AUDIO.read_bytes()
    upload = a.post('/api/references/upload', files={'file':('voice.wav', audio(),'audio/wav')}, data={'transcript':'Private A'})
    assert upload.status_code == 200, upload.text
    ref = upload.json()['reference']['id']
    second = a.post('/api/references/upload', files={'file':('voice.wav',audio(2),'audio/wav')},data={'transcript':'New version'})
    assert second.json()['reference']['id'] != ref
    assert backend.REFERENCE_AUDIO.read_bytes() == original
    assert a.get(f'/api/references/{ref}/audio').status_code == 200
    assert b.get(f'/api/references/{ref}/audio').status_code == 404
    assert b.post('/api/generations',json={'text':'Private','models':['qwen3'],'reference_id':ref}).status_code == 404
    monkeypatch.setattr(backend.direct_inference, 'model_availability', lambda _: {'configured':True})
    def synthesize(model,text,reference,transcript,output):
        output.write_bytes(audio())
    monkeypatch.setattr(backend.direct_inference, 'generate_audio', synthesize)
    result = a.post('/api/generations',json={'text':'Private','models':['qwen3'],'reference_id':ref})
    assert result.status_code == 202, result.text
    generation = result.json()['id']
    job = result.json()['jobs'][0]['id']
    for _ in range(100):
        state=a.get('/api/generations/'+generation).json()['jobs'][0]['status']
        if state == 'complete': break
        time.sleep(.01)
    assert state == 'complete'
    for path in [f'/api/generations/{generation}',f'/api/recordings/{job}/audio',f'/api/evaluations/{job}',f'/api/evaluation-errors/{job}',f'/api/ratings/{job}',f'/api/exports/{generation}']:
        assert a.get(path).status_code == 200, path
        assert b.get(path).status_code == 404, path
    assert b.post('/api/evaluations/'+job).status_code == 404
    assert b.post('/api/ratings',json={'job_id':job,'category':'naturalness','rating':5}).status_code == 404
    assert not any(x['id']==generation for x in b.get('/api/experiments').json())
    a.patch('/api/workspace',json={'active':['qwen3']})
    assert b.get('/api/workspace').json()['active'] == list(backend.MODEL_IDS)
    with backend.db() as c:
        assert c.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []


def test_csrf_rate_limit_and_bad_inputs(accounts):
    a,b,_=accounts
    assert a.patch('/api/workspace',json={'active':[]},headers={'X-Studio-CSRF':''}).status_code == 403
    assert a.patch('/api/workspace',json={'active':[]},headers={'Origin':'https://evil.test'}).status_code == 403
    assert a.post('/api/references/upload',files={'file':('x.wav',b'RIFFtruncated','audio/wav')}).status_code == 400
    assert a.post('/api/references/upload',files={'file':('x.wav',audio()[:-100],'audio/wav')}).status_code == 400
    assert a.post('/api/generations',content=b'x'*(256*1024+1)).status_code == 413
    assert a.post('/api/generations',json={'text':'test','models':['qwen3'],'speech_rate':float('inf') if False else 99}).status_code == 422
    assert a.post('/api/generations',json={'text':'test','models':['cosyvoice3'],'temperature':.7}).status_code == 422
    for _ in range(10):
        a.post('/api/session',json={'username':'nobody','password':'incorrect'})
    assert a.post('/api/session',json={'username':'nobody','password':'incorrect'}).status_code == 429


def test_password_reset_single_use_and_revocation(accounts):
    a, _, users=accounts
    token=secrets.token_urlsafe(48)
    with backend.db() as c:
        c.execute('INSERT INTO password_resets VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),users[0],time.time()+300))
    body={'token':token,'password':'a new unique password 578!'}
    assert a.post('/api/password-reset',json=body).status_code == 200
    assert a.get('/api/models').status_code == 401
    assert a.post('/api/password-reset',json=body).status_code == 400


def test_administrator_requires_separate_token(monkeypatch):
    monkeypatch.setenv('API_TOKEN','user-token')
    monkeypatch.setenv('ADMIN_TOKEN','admin-token')
    with TestClient(backend.app) as api:
        assert api.post('/api/admin/reload-reference').status_code == 403
        assert api.post('/api/admin/reload-reference',headers={'Authorization':'Bearer user-token'}).status_code == 403
        assert api.post('/api/admin/reload-reference',headers={'Authorization':'Bearer admin-token'}).status_code == 200


def test_request_controls_cache_provenance_and_download(accounts, monkeypatch):
    from generation_config import current
    import json
    import zipfile
    a, b, _ = accounts
    monkeypatch.setattr(backend.direct_inference,'model_availability',lambda _: {'configured':True})
    calls=[]
    def synthesize(model,text,ref,transcript,path):
        calls.append(current.get())
        path.write_bytes(audio())
    monkeypatch.setattr(backend.direct_inference,'generate_audio',synthesize)
    def generate(client, speed):
        r=client.post('/api/generations',json={'text':'Cache settings test','models':['qwen3'],'speech_rate':speed,'temperature':.65})
        assert r.status_code==202,r.text
        data=r.json()
        for _ in range(100):
            if client.get('/api/generations/'+data['id']).json()['jobs'][0]['status']=='complete':break
            time.sleep(.01)
        return data
    first=generate(a,.9)
    assert calls[0].speech_rate==.9 and calls[0].temperature==.65
    assert generate(a,.9)['jobs'][0]['cached'] is True
    assert generate(a,1.0)['jobs'][0]['cached'] is False
    assert generate(b,.9)['jobs'][0]['cached'] is False
    before=len(calls)
    job=first['jobs'][0]['id']
    assert a.get(f'/api/recordings/{job}/audio').content == audio()
    assert a.get(f'/api/recordings/{job}/audio').content == audio()
    archive=zipfile.ZipFile(io.BytesIO(a.get('/api/exports/'+first['id']).content))
    metadata=json.loads(archive.read('metadata.json'))
    provenance=json.loads(metadata['jobs'][0]['configuration'])
    assert provenance['speech_rate']==.9 and provenance['temperature']==.65
    assert len(calls)==before
    assert not list((backend.ROOT/'exports').glob('*.zip'))
    with backend.db() as c:
        assert c.execute("SELECT count(*) FROM storage_reservations WHERE id LIKE 'export:%'").fetchone()[0]==0


def test_concurrent_admission_never_exceeds_capacity(accounts,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    a,_,_=accounts
    monkeypatch.setattr(backend,'MAX_PENDING',2)
    monkeypatch.setattr(backend.direct_inference,'model_availability',lambda _: {'configured':True})
    def synthesize(model,text,ref,transcript,path):
        time.sleep(.4);path.write_bytes(audio())
    monkeypatch.setattr(backend.direct_inference,'generate_audio',synthesize)
    with ThreadPoolExecutor(5) as pool:
        responses=list(pool.map(lambda i:a.post('/api/generations',json={'text':f'concurrent {i}','models':['qwen3'],'use_cache':False}),range(5)))
    assert [r.status_code for r in responses].count(202)==2
    assert [r.status_code for r in responses].count(429)==3
    for response in responses:
        if response.status_code==202:
            for _ in range(200):
                if a.get('/api/generations/'+response.json()['id']).json()['jobs'][0]['status']=='complete':break
                time.sleep(.01)


def test_failed_batch_planning_never_starts_inference(accounts,monkeypatch):
    a,_,_=accounts
    monkeypatch.setenv('MAX_USER_JOBS','1')
    calls=[]
    monkeypatch.setattr(backend.direct_inference,'model_availability',lambda _: {'configured':True})
    monkeypatch.setattr(backend.direct_inference,'generate_audio',lambda *args:calls.append(args))
    ref=a.get('/api/reference-profile').json()['reference']['id']
    with backend.db() as c:
        before=c.execute('SELECT count(*) FROM batches').fetchone()[0]
    result=a.post('/api/batches',json={'reference_id':ref,'models':['qwen3'],'texts':['one','two']})
    assert result.status_code==429,result.text
    assert calls==[]
    with backend.db() as c:
        assert c.execute('SELECT count(*) FROM batches').fetchone()[0]==before


def test_explicit_reference_pins_its_original_transcript(accounts, monkeypatch):
    a,_,_=accounts
    monkeypatch.setattr(backend.direct_inference,'model_availability',lambda _: {'configured':True})
    seen=[]
    def synthesize(model,text,ref,transcript,path):
        seen.append(transcript);path.write_bytes(audio())
    monkeypatch.setattr(backend.direct_inference,'generate_audio',synthesize)
    first=a.post('/api/references/upload',files={'file':('x.wav',audio(),'audio/wav')},data={'transcript':'Original transcript','language':'English'})
    ref=first.json()['reference']['id']
    assert first.json()['language']=='English'
    a.post('/api/references/upload',files={'file':('x.wav',audio(),'audio/wav')},data={'transcript':'Replacement transcript','language':'English'})
    result=a.post('/api/generations',json={'text':'Test','reference_id':ref,'language':'English','models':['cosyvoice3']})
    assert result.status_code==202,result.text
    for _ in range(100):
        if a.get('/api/generations/'+result.json()['id']).json()['jobs'][0]['status']=='complete':break
        time.sleep(.01)
    assert seen==['Original transcript']


def test_storage_byte_limits_are_atomic(accounts, monkeypatch):
    a,_,_=accounts
    monkeypatch.setenv('MAX_USER_STORAGE_BYTES','1')
    before=a.get('/api/reference-profile').json()['reference']['id']
    denied=a.post('/api/references/upload',files={'file':('x.wav',audio(),'audio/wav')})
    assert denied.status_code==429
    assert a.get('/api/reference-profile').json()['reference']['id']==before
    monkeypatch.setattr(backend.direct_inference,'model_availability',lambda _: {'configured':True})
    with backend.db() as c:
        count=c.execute('SELECT count(*) FROM jobs').fetchone()[0]
    denied=a.post('/api/generations',json={'text':'No capacity','models':['qwen3']})
    assert denied.status_code==429
    with backend.db() as c:
        assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]==count
        assert c.execute('SELECT count(*) FROM storage_reservations').fetchone()[0]==0


def test_login_limits_survive_limiter_recreation():
    fresh=security.RateLimiter();fresh.bind(backend.DB)
    assert fresh.allow(('persisted','account'),1,300)
    restarted=security.RateLimiter();restarted.bind(backend.DB)
    assert not restarted.allow(('persisted','account'),1,300)


def test_actual_process_timeout_and_owned_cancellation(accounts, monkeypatch):
    from isolated_workers import WorkerPool, Worker
    from test_workers import rpc_audio
    a,b,_=accounts
    pool=WorkerPool()
    pool.workers['qwen3']=Worker('qwen3',rpc_audio,abort=pool.abort)
    monkeypatch.setattr(backend,'WORKERS',pool)
    monkeypatch.setenv('INFERENCE_ISOLATION','process')
    monkeypatch.setenv('GENERATION_TIMEOUT_SECONDS','0.5')
    monkeypatch.setattr(backend.direct_inference,'model_availability',lambda _: {'configured':True})
    def start(text):
        r=a.post('/api/generations',json={'text':text,'models':['qwen3'],'use_cache':False})
        assert r.status_code==202,r.text
        return r.json()
    def state(generation):
        return a.get('/api/generations/'+generation['id']).json()['jobs'][0]
    try:
        timed=start('hang')
        for _ in range(300):
            if state(timed)['status']=='failed':break
            time.sleep(.01)
        assert state(timed)['status']=='failed'
        assert pool.workers['qwen3'].process is None
        monkeypatch.setenv('GENERATION_TIMEOUT_SECONDS','10')
        cancelled=start('hang');job=cancelled['jobs'][0]['id']
        assert b.delete('/api/jobs/'+job).status_code==404
        assert a.delete('/api/jobs/'+job).status_code==200
        assert state(cancelled)['status']=='cancelled'
        assert pool.workers['qwen3'].process is None
        recovered=start('success')
        for _ in range(300):
            if state(recovered)['status']=='complete':break
            time.sleep(.01)
        assert state(recovered)['status']=='complete'
        with backend.db() as c:
            assert c.execute('SELECT count(*) FROM storage_reservations').fetchone()[0]==0
    finally:
        pool.close()


def test_comparison_model_settings_remain_independent(accounts, monkeypatch):
    from generation_config import current
    a,_,_=accounts
    monkeypatch.setattr(backend.direct_inference,'model_availability',lambda _: {'configured':True})
    seen={}
    def synthesize(model,text,ref,transcript,path):
        seen[model]=current.get();path.write_bytes(audio())
    monkeypatch.setattr(backend.direct_inference,'generate_audio',synthesize)
    result=a.post('/api/generations',json={'text':'Per-model controls','models':list(backend.MODEL_IDS),'model_settings':{'qwen3':{'temperature':.4},'xtts-v2':{'temperature':.9,'speech_rate':1.1}}})
    assert result.status_code==202,result.text
    for _ in range(100):
        jobs=a.get('/api/generations/'+result.json()['id']).json()['jobs']
        if all(job['status']=='complete' for job in jobs):break
        time.sleep(.01)
    assert seen['qwen3'].temperature==.4
    assert seen['xtts-v2'].temperature==.9 and seen['xtts-v2'].speech_rate==1.1
    assert seen['cosyvoice3'].temperature is None


def test_bad_tokens_and_validation_never_echo_secrets(monkeypatch):
    monkeypatch.setenv('API_TOKEN','valid-token')
    with TestClient(backend.app) as api:
        assert api.post('/api/session',json={'token':'悪いトークン'}).status_code==401
        private='never-echo-this-secret-'*100
        response=api.post('/api/session',json={'password':private})
        assert response.status_code==422
        assert private not in response.text and '"input"' not in response.text


def test_non_admin_cannot_read_security_events(accounts,monkeypatch):
    a,_,_=accounts
    monkeypatch.setenv('ADMIN_TOKEN','administrator-secret')
    assert a.get('/api/admin/security-events',headers={'Authorization':'Bearer administrator-secret'}).status_code==403


def test_explicit_browser_session_works_without_cookies(monkeypatch):
    monkeypatch.setenv('API_TOKEN','browser-login-token')
    with TestClient(backend.app) as api:
        cookie_login=api.post('/api/session',json={'token':'browser-login-token'})
        assert 'session_token' not in cookie_login.json()
        bearer=api.post('/api/session',json={'token':'browser-login-token','transport':'bearer'},headers={'X-Studio-CSRF':'1'})
        secret=bearer.json()['session_token'];api.cookies.clear()
        headers={'Authorization':'Session '+secret}
        assert api.get('/api/models',headers=headers).status_code==200
        assert api.patch('/api/workspace',json={'active':[]},headers=headers).status_code==200
        assert api.delete('/api/session',headers=headers).status_code==200
        assert api.get('/api/models',headers=headers).status_code==401


def test_security_headers_cover_unauthorized_and_oversized_responses(monkeypatch):
    monkeypatch.setenv('API_TOKEN','headers-test-token')
    with TestClient(backend.app) as api:
        for response in [api.get('/api/models'),api.post('/api/generations',content=b'x'*(256*1024+1))]:
            assert response.status_code in (401,413)
            assert response.headers['cache-control']=='no-store'
            assert response.headers['x-content-type-options']=='nosniff'
            assert 'x-request-id' in response.headers


def test_legacy_profile_does_not_disclose_unowned_transcript(accounts, monkeypatch, tmp_path):
    a,_,_=accounts
    monkeypatch.setattr(backend,'REFERENCE_DIR',tmp_path)
    identifier='quarantined-'+str(uuid.uuid4())
    target=backend.ROOT/'audio'/(identifier+'.wav');target.write_bytes(audio())
    with backend.db() as c:
        c.execute('INSERT INTO references_audio VALUES (?,?,?,?,?)',(identifier,'legacy-hash','private.wav',str(target),time.time()))
        old=c.execute('SELECT * FROM reference_profiles WHERE id=1').fetchone()
        c.execute('INSERT OR REPLACE INTO reference_profiles VALUES (1,?,?,?)',(identifier,'legacy private transcript',time.time()))
    try:
        response=a.get('/api/reference-profile')
        assert response.status_code==200 and response.json()['reference'] is None
        assert 'legacy private transcript' not in response.text
    finally:
        with backend.db() as c:
            if old:c.execute('INSERT OR REPLACE INTO reference_profiles VALUES (?,?,?,?)',tuple(old))
            else:c.execute('DELETE FROM reference_profiles WHERE id=1')
            c.execute('DELETE FROM references_audio WHERE id=?',(identifier,))
        target.unlink()


def test_untrusted_owner_fields_and_coerced_ratings_are_rejected(accounts):
    a,_,_=accounts
    assert a.post('/api/generations',json={'text':'test','models':['qwen3'],'owner_id':'another-user'}).status_code==422
    assert a.post('/api/ratings',json={'job_id':'unknown','category':'naturalness','rating':True}).status_code==422
