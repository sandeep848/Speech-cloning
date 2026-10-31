"""Regressions for audit findings; all voices/credentials are synthetic."""
import asyncio
import sys
import time
from types import SimpleNamespace
import numpy as np
import pytest
from fastapi.testclient import TestClient
import backend
from test_security import accounts as account_fixture, audio
accounts = account_fixture
from models.pacing import plan_speech


def test_unknown_admin_paths_do_not_allocate_limit_rows(monkeypatch):
    monkeypatch.setenv('DEV_ALLOW_ANONYMOUS', '0')
    with TestClient(backend.app) as client:
        with backend.db() as c:
            before = c.execute('SELECT count(*) FROM rate_limits').fetchone()[0]
        for index in range(25):
            assert client.post('/api/admin/unknown-' + str(index), json={}).status_code == 404
        with backend.db() as c:
            assert c.execute('SELECT count(*) FROM rate_limits').fetchone()[0] == before


def test_successful_logins_do_not_exhaust_account_bucket(monkeypatch):
    monkeypatch.setenv('API_TOKEN', 'synthetic-token')
    with TestClient(backend.app) as client:
        for _ in range(15):
            client.cookies.clear()
            assert client.post('/api/session', json={'token': 'synthetic-token'}).status_code == 200


def test_authentication_precedes_private_body_receive(monkeypatch):
    monkeypatch.setenv('DEV_ALLOW_ANONYMOUS', '0')
    called = []
    async def receive():
        called.append(True)
        raise AssertionError('Unauthenticated body must not be read')
    sent = []
    async def send(message): sent.append(message)
    scope = {'type': 'http', 'path': '/api/references/upload', 'method': 'POST', 'headers': [],
             'query_string': b'', 'scheme': 'http', 'server': ('testserver',80), 'client': ('127.0.0.1',123)}
    asyncio.run(backend.BodyLimitMiddleware(None)(scope, receive, send))
    assert not called
    assert sent[0]['status'] == 401


def test_duplicate_set_cookie_preserved():
    from security_headers import SecurityHeaders
    async def app(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200,
                    'headers': [(b'set-cookie',b'a=1'),(b'set-cookie',b'b=2')]})
    sent=[]
    async def send(message): sent.append(message)
    asyncio.run(SecurityHeaders(app)({'type':'http','path':'/api/test'},None,send))
    assert [v for k,v in sent[0]['headers'] if k==b'set-cookie']==[b'a=1',b'b=2']


def test_german_ordinals_abbreviations_and_real_sentence_end():
    assert [x.text for x in plan_speech('Am 3. Oktober kommen wir. Danach gehen wir.')] == ['Am 3. Oktober kommen wir.', 'Danach gehen wir.']
    assert [x.text for x in plan_speech('Das gilt u. a. für Berlin.')] == ['Das gilt u. a. für Berlin.']
    assert len(plan_speech('Brot usw. Danach gehen wir.')) == 2
    assert len(plan_speech('Er bekommt 3. Danach gehen wir.')) == 2


def test_explicit_evaluation_language_and_observed_first_audio(tmp_path):
    from evaluator import Evaluator
    path = tmp_path/'sample.wav';path.write_bytes(audio())
    options=[]
    evaluator=Evaluator.__new__(Evaluator)
    evaluator.initialization_errors={}
    evaluator.asr=SimpleNamespace(transcribe=lambda path,**kwargs: options.append(kwargs) or {'text':'Hallo'})
    evaluator.speaker=evaluator.scoreq=evaluator.dnsmos=None
    # No jiwer installation required to prove ASR options; add minimal test double.
    from unittest.mock import patch
    jiwer=SimpleNamespace(Compose=lambda _: lambda x:x, ToLowerCase=lambda:None,
        RemovePunctuation=lambda:None,RemoveMultipleSpaces=lambda:None,Strip=lambda:None,wer=lambda *a:0,cer=lambda *a:0)
    with patch.dict(sys.modules,jiwer=jiwer):
        metrics=evaluator.evaluate(str(path),str(path),'Hallo',1,123,'German')
    assert options==[{'fp16':False,'language':'de'}]
    assert ('first_audio_ms',123,'clock-first-audio-chunk') in metrics


def test_dnsmos_reference_padding_hops_and_calibration(tmp_path):
    from evaluator import Evaluator
    path=tmp_path/'short.wav';path.write_bytes(audio())
    inputs=[]
    def run(_, feed):
        inputs.append(next(iter(feed.values())))
        return [np.array([[3.,4.,3.5]],dtype=np.float32)]
    e=Evaluator.__new__(Evaluator);e.initialization_errors={};e.asr=e.speaker=e.scoreq=None
    e.dnsmos=SimpleNamespace(get_inputs=lambda:[SimpleNamespace(name='input_1')],run=run)
    metrics=dict((k,v) for k,v,_ in e.evaluate(str(path),str(path),'test',1))
    assert inputs and all(x.shape==(1,144160) for x in inputs)
    assert metrics['dnsmos_sig_raw']==3
    assert metrics['dnsmos_sig']==pytest.approx(np.polyval([-.08397278,1.22083953,.0052439],3))


def test_exact_qwen_revision_not_cache_mtime(monkeypatch,tmp_path):
    from models.weights import resolve_snapshot
    calls=[]
    monkeypatch.setenv('APP_ENV','production')
    revision='a'*40
    monkeypatch.setitem(sys.modules,'huggingface_hub',SimpleNamespace(snapshot_download=lambda **kw: calls.append(kw) or '/verified/'+revision))
    assert resolve_snapshot('test/model',revision,tmp_path/'missing','Qwen')=='/verified/'+revision
    assert calls==[{'repo_id':'test/model','revision':revision,'local_files_only':True}]
    local=tmp_path/'local';local.mkdir();(local/'config.json').write_text('{}')
    with pytest.raises(RuntimeError,match='do not match'):
        resolve_snapshot('test/model',revision,local,'Qwen')


def test_bounded_cache_evicts_least_recently_used():
    from models.cache import BoundedCache
    c=BoundedCache(2);c['a']=1;c['b']=2
    assert c['a']==1
    c['c']=3
    assert list(c)==['a','c']


def test_owned_deletion_preserves_cached_audio_until_last_user_job(accounts,monkeypatch):
    a,b,_=accounts
    monkeypatch.setattr(backend.direct_inference,'model_availability',lambda _: {'configured':True})
    monkeypatch.setattr(backend.direct_inference,'generate_audio',lambda model,text,ref,transcript,path:path.write_bytes(audio()))
    uploaded=a.post('/api/references/upload',files={'file':('fixture.wav',audio(),'audio/wav')},data={'transcript':'Synthetic'}).json()['reference']['id']
    generations=[]
    for _ in range(2):
        response=a.post('/api/generations',json={'text':'Synthetic deletion','models':['qwen3'],'reference_id':uploaded})
        assert response.status_code==202,response.text
        generation=response.json();generations.append(generation)
        for _ in range(100):
            status=a.get('/api/generations/'+generation['id']).json()['jobs'][0]['status']
            if status=='complete':break
            time.sleep(.01)
        assert status=='complete'
    first,second=generations
    assert b.delete('/api/generations/'+first['id']).status_code==404
    assert a.delete('/api/references/'+uploaded).status_code==409
    assert a.delete('/api/generations/'+first['id']).status_code==200
    assert a.get('/api/recordings/'+second['jobs'][0]['id']+'/audio').status_code==200
    assert a.delete('/api/generations/'+second['id']).status_code==200
    assert a.delete('/api/references/'+uploaded).status_code==200
    assert a.get('/api/references/'+uploaded+'/audio').status_code==404
    with backend.db() as c:
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_batch_language_is_forwarded(accounts,monkeypatch):
    a,_,_=accounts
    seen=[]
    async def generate(request,deferred=None):
        seen.append(request.language)
        raise backend.HTTPException(422,'synthetic stop')
    monkeypatch.setattr(backend,'_generate_impl',generate)
    reference=a.get('/api/reference-profile').json()['reference']['id']
    assert a.post('/api/batches',json={'reference_id':reference,'models':['qwen3'],'texts':['Hello'],'language':'English'}).status_code==422
    assert seen==['English']


def test_deterministic_policy_and_rng_are_restored(monkeypatch):
    import random
    from contextlib import nullcontext
    from inference_context import seeded_inference
    state={'enabled':False,'warn_only':True}
    def deterministic(value, warn_only=False):state.update(enabled=value,warn_only=warn_only)
    fake=SimpleNamespace(are_deterministic_algorithms_enabled=lambda:state['enabled'],
        is_deterministic_algorithms_warn_only_enabled=lambda:state['warn_only'],
        use_deterministic_algorithms=deterministic, manual_seed=lambda seed:None,
        cuda=SimpleNamespace(is_available=lambda:False),random=SimpleNamespace(fork_rng=lambda devices:nullcontext()))
    monkeypatch.setitem(sys.modules,'torch',fake)
    python_state,numpy_state=random.getstate(),np.random.get_state()
    with pytest.raises(RuntimeError,match='synthetic'):
        with seeded_inference(7):
            assert state['enabled']
            random.random();np.random.random()
            raise RuntimeError('synthetic failure')
    assert state=={'enabled':False,'warn_only':True}
    assert random.getstate()==python_state
    np.testing.assert_equal(np.random.get_state(),numpy_state)


def test_observed_timing_crosses_actual_evaluation_process(monkeypatch,tmp_path):
    from isolated_workers import Worker
    for key in ('WHISPER','ECAPA','SCOREQ','DNSMOS'):
        monkeypatch.setenv('ENABLE_'+key,'0')
    path=tmp_path/'synthetic.wav';path.write_bytes(audio())
    worker=Worker('evaluation')
    try:
        measures,errors=worker.call((str(path),str(path),'Synthetic',1.,42.,'English'),timeout=20)
        assert ('first_audio_ms',42.,'clock-first-audio-chunk') in measures
        assert not errors
    finally:worker.close()


def test_retention_removes_expired_owned_records(accounts,monkeypatch):
    a,_,_=accounts
    reference=a.post('/api/references/upload',files={'file':('old.wav',audio(),'audio/wav')},data={'transcript':'Synthetic'}).json()['reference']['id']
    with backend.db() as c:
        c.execute('UPDATE references_audio SET created=? WHERE id=?',(time.time()-5*86400,reference))
    monkeypatch.setenv('RETENTION_DAYS','2')
    backend.apply_retention()
    assert a.get('/api/references/'+reference+'/audio').status_code==404
    assert a.get('/api/reference-profile').json()['reference'] is not None


def test_schema_inventory_matches_live_definitions():
    from pathlib import Path
    import sqlite3
    inventory=Path(__file__).resolve().parents[2]/'docs/SCHEMA.sql'
    with sqlite3.connect(':memory:') as expected:
        expected.executescript(inventory.read_text())
        documented={row[0]:row[1] for row in expected.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}
    with backend.db() as c:
        actual={row[0]:row[1] for row in c.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}
    assert documented==actual
