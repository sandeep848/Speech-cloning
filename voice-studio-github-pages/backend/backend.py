"""Voice Comparison Studio API. No synthetic inference or fabricated metrics."""
from __future__ import annotations
import asyncio, hashlib, json, os, sqlite3, time, uuid, wave, subprocess, tempfile, shutil
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(os.environ.get('VOICE_STUDIO_DATA', Path(__file__).parent / 'data')).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / 'audio').mkdir(exist_ok=True)
DB = ROOT / 'studio.sqlite3'
ORIGINS = [x.strip() for x in os.environ.get('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if x.strip()]
app = FastAPI(title='Voice Comparison Studio', version='0.1.0')
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_credentials=False, allow_methods=['GET','POST','PATCH','PUT','DELETE'], allow_headers=['Content-Type','Authorization'])
MODEL_IDS = ('qwen3', 'cosyvoice3', 'xtts-v2')
LABELS = {'qwen3':'Qwen3-TTS','cosyvoice3':'CosyVoice 3','xtts-v2':'XTTS-v2'}
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='inference')
INFERENCE_LOCK = asyncio.Lock()  # One running model at a time on a shared GPU
TASKS: dict[str,asyncio.Task] = {}

@contextmanager
def db():
    connection = sqlite3.connect(DB, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally: connection.close()

with db() as c:
    c.execute('CREATE TABLE IF NOT EXISTS references_audio (id TEXT PRIMARY KEY, sha256 TEXT, filename TEXT, path TEXT, created REAL)')
    c.execute('CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, text TEXT, reference_id TEXT, created REAL)')
    c.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, batch_id TEXT, model_id TEXT, status TEXT, output TEXT, error TEXT, created REAL, updated REAL, cache_key TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS metrics (job_id TEXT, name TEXT, value REAL, evaluator TEXT, PRIMARY KEY(job_id,name))')
    c.execute("CREATE TABLE IF NOT EXISTS reference_profiles (id INTEGER PRIMARY KEY CHECK(id=1), reference_id TEXT, transcript TEXT NOT NULL DEFAULT '', updated REAL)")

# The reference is configured only on the server, never uploaded by the public frontend.
REFERENCE_DIR = Path(__file__).resolve().parent / 'reference'
REFERENCE_AUDIO = REFERENCE_DIR / 'reference.wav'
REFERENCE_TEXT = REFERENCE_DIR / 'reference.txt'

def install_backend_reference():
    """Import/update the server-owned reference file and transcript, without exposing them to uploads."""
    if not REFERENCE_AUDIO.is_file():
        return False
    import wave as wav
    try:
        with wav.open(str(REFERENCE_AUDIO),'rb') as reader:
            duration=reader.getnframes()/reader.getframerate()
            if duration<0.25 or duration>120:
                raise ValueError('Backend reference WAV must be 0.25-120 seconds long')
    except (wav.Error,ZeroDivisionError) as exc:
        raise RuntimeError('backend/reference/reference.wav must be a valid PCM WAV recording') from exc
    data=REFERENCE_AUDIO.read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    transcript=REFERENCE_TEXT.read_text(encoding='utf-8').strip() if REFERENCE_TEXT.is_file() else ''
    with db() as c:
        existing=c.execute('SELECT id,path FROM references_audio WHERE sha256=? ORDER BY created DESC LIMIT 1',(digest,)).fetchone()
        if existing and Path(existing['path']).is_file():
            ref_id=existing['id']
        else:
            ref_id=str(uuid.uuid4())
            target=ROOT/'audio'/f'{ref_id}.wav'
            shutil.copy2(REFERENCE_AUDIO,target)
            c.execute('INSERT INTO references_audio VALUES (?,?,?,?,?)',(ref_id,digest,'reference.wav',str(target),time.time()))
        c.execute("INSERT INTO reference_profiles(id,reference_id,transcript,updated) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET reference_id=excluded.reference_id,transcript=excluded.transcript,updated=excluded.updated",(ref_id,transcript,time.time()))
    return True

@app.on_event('startup')
def load_backend_reference():
    install_backend_reference()

@app.post('/api/admin/reload-reference')
def reload_backend_reference(authorization:str|None=Header(None)):
    token=os.environ.get('ADMIN_TOKEN')
    if not token or authorization!='Bearer '+token:
        raise HTTPException(403,'Admin token required')
    return {'loaded':install_backend_reference()}

class GenerateRequest(BaseModel):
    text: str = Field(min_length=1,max_length=2000)
    reference_id: str | None = None
    models: list[str] = Field(min_length=1,max_length=3)
    reference_text: str = Field(default='',max_length=4000)

class Workspace(BaseModel):
    active: list[str]

@app.get('/api/health')
def health(): return {'status':'ok','inference':'configured-workers-only'}

@app.get('/api/models')
def models():
    import urllib.request
    result=[]
    for model in MODEL_IDS:
        url=os.environ.get('WORKER_'+model.upper().replace('-','_')+'_URL','')
        ready=False
        if url:
            try:
                with urllib.request.urlopen(urllib.request.Request(url.rstrip('/')+'/health',headers={'Authorization':'Bearer '+os.environ.get('WORKER_TOKEN','')}),timeout=1.5) as r: ready=r.status==200 and json.loads(r.read()).get('ready',False)
            except Exception: pass
        result.append({'id':model,'name':LABELS[model],'ready':ready,'configured':bool(url)})
    return result

class TranscriptUpdate(BaseModel):
    transcript: str = Field(max_length=4000)

@app.get('/api/reference-profile')
def get_reference_profile():
    with db() as c:
        row=c.execute('SELECT reference_id,transcript FROM reference_profiles WHERE id=1').fetchone()
        if not row: return {'reference':None,'transcript':''}
        ref=c.execute('SELECT id,filename,path FROM references_audio WHERE id=?',(row['reference_id'],)).fetchone()
    return {'reference':{'id':ref['id'],'filename':ref['filename']} if ref and Path(ref['path']).exists() else None,'transcript':row['transcript']}

@app.put('/internal-disabled/reference-profile/transcript')
def set_reference_transcript(payload:TranscriptUpdate):
    with db() as c:
        c.execute("INSERT INTO reference_profiles(id,reference_id,transcript,updated) VALUES(1,NULL,?,?) ON CONFLICT(id) DO UPDATE SET transcript=excluded.transcript,updated=excluded.updated",(payload.transcript,time.time()))
    return {'transcript':payload.transcript}

@app.post('/internal-disabled/references')
async def add_reference(file:UploadFile=File(...)):
    limit=25*1024*1024
    content=await file.read(limit+1)
    if len(content)>limit: raise HTTPException(413,'Reference audio must be 25 MB or smaller')
    if not content: raise HTTPException(422,'The uploaded audio file is empty')
    ext=Path(file.filename or '').suffix.lower()
    if ext not in ('.wav','.mp3','.m4a','.ogg','.flac','.webm'):
        raise HTTPException(415,'Supported formats: WAV, MP3, M4A, OGG, FLAC and WebM')
    ref_id=str(uuid.uuid4())
    target=ROOT/'audio'/f'{ref_id}.wav'
    source=None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext,delete=False) as temp:
            temp.write(content)
            source=Path(temp.name)
        ffmpeg=shutil.which('ffmpeg')
        if ffmpeg:
            proc=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-i',str(source),'-ac','1','-ar','24000','-c:a','pcm_s16le',str(target)],capture_output=True,text=True,timeout=45)
            if proc.returncode: raise ValueError('Could not decode this audio. Please upload a valid audio recording.')
        elif ext=='.wav':
            with wave.open(str(source),'rb') as w:
                if w.getcomptype()!='NONE' or w.getsampwidth()!=2 or w.getnchannels()!=1 or w.getframerate()!=24000:
                    raise ValueError('Install ffmpeg to convert WAV audio automatically, or upload mono 24 kHz 16-bit PCM WAV')
            shutil.copyfile(source,target)
        else:
            raise ValueError('ffmpeg is required to accept this file format')
        with wave.open(str(target),'rb') as w:
            duration=w.getnframes()/w.getframerate()
            if duration<0.25 or duration>120: raise ValueError('Reference audio must be between 0.25 and 120 seconds')
    except (ValueError,subprocess.TimeoutExpired,wave.Error) as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(422,str(exc) or 'Invalid audio')
    finally:
        if source: source.unlink(missing_ok=True)
    with db() as c:
        c.execute('INSERT INTO references_audio VALUES (?,?,?,?,?)',(ref_id,hashlib.sha256(target.read_bytes()).hexdigest(),file.filename or 'reference.wav',str(target),time.time()))
        c.execute("INSERT INTO reference_profiles(id,reference_id,transcript,updated) VALUES(1,?,'',?) ON CONFLICT(id) DO UPDATE SET reference_id=excluded.reference_id,transcript='',updated=excluded.updated",(ref_id,time.time()))
    return {'id':ref_id,'duration':round(duration,2),'filename':file.filename}

@app.get('/api/references/{reference_id}/audio')
def reference_audio(reference_id:str):
    with db() as c: row=c.execute('SELECT path FROM references_audio WHERE id=?',(reference_id,)).fetchone()
    if not row or not Path(row['path']).is_file(): raise HTTPException(404,'Reference file unavailable. Upload the original audio again.')
    return FileResponse(row['path'],media_type='audio/wav')

def update_job(job_id,status,output=None,error=None):
    with db() as c: c.execute('UPDATE jobs SET status=?,output=?,error=?,updated=? WHERE id=?',(status,output,error,time.time(),job_id))

def execute_worker(job_id, model_id, text, reference_path, reference_text):
    """Calls a dedicated resident worker. No model substitution or generated placeholder."""
    import urllib.request
    url=os.environ.get('WORKER_'+model_id.upper().replace('-','_')+'_URL','').rstrip('/')
    if not url: raise RuntimeError(f'{model_id} worker is not configured')
    # Workers accept JSON with a shared mounted reference path and return WAV bytes.
    payload=json.dumps({'text':text,'reference_path':reference_path,'reference_text':reference_text}).encode()
    request=urllib.request.Request(url+'/generate',data=payload,headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ.get('WORKER_TOKEN','')},method='POST')
    with urllib.request.urlopen(request,timeout=int(os.environ.get('INFERENCE_TIMEOUT','600'))) as response:
        if response.headers.get_content_type()!='audio/wav': raise RuntimeError('Worker returned a non-WAV response')
        audio=response.read(30*1024*1024+1)
    if len(audio)>30*1024*1024 or audio[:4]!=b'RIFF' or audio[8:12]!=b'WAVE': raise RuntimeError('Worker returned invalid WAV')
    output=ROOT/'audio'/f'{job_id}.wav'
    output.write_bytes(audio)
    return str(output)

async def run_job(job_id,model,text,path,reference_text):
    async with INFERENCE_LOCK:
        update_job(job_id,'running')
        try:
            loop=asyncio.get_running_loop()
            output=await loop.run_in_executor(POOL,execute_worker,job_id,model,text,path,reference_text)
            update_job(job_id,'complete',output)
            if os.environ.get('AUTO_EVALUATE','1')=='1':
                asyncio.create_task(asyncio.to_thread(__import__('extend_backend').evaluate_job,job_id))
        except Exception as exc:
            update_job(job_id,'failed',error=str(exc)[:400])

@app.post('/api/generations',status_code=202)
async def generate(request:GenerateRequest):
    if len(set(request.models))!=len(request.models) or any(m not in MODEL_IDS for m in request.models): raise HTTPException(422,'Unsupported or duplicate models')
    with db() as c:
        profile=c.execute('SELECT reference_id,transcript FROM reference_profiles WHERE id=1').fetchone()
        selected_reference=request.reference_id or (profile['reference_id'] if profile else None)
        effective_transcript=request.reference_text or (profile['transcript'] if profile else '')
        ref=c.execute('SELECT * FROM references_audio WHERE id=?',(selected_reference,)).fetchone()
    if not ref: raise HTTPException(422,'Upload and save an original reference recording first')
    now=time.time(); batch_id=str(uuid.uuid4()); planned=[]
    with db() as c:
        c.execute('INSERT INTO batches VALUES (?,?,?,?)',(batch_id,request.text.strip(),selected_reference,now))
        for model in request.models:
            key=hashlib.sha256(json.dumps([model,ref['sha256'],request.text.strip(),effective_transcript],sort_keys=True).encode()).hexdigest()
            prior=c.execute("SELECT output FROM jobs WHERE cache_key=? AND status='complete' AND output IS NOT NULL ORDER BY created DESC LIMIT 1",(key,)).fetchone()
            job_id=str(uuid.uuid4()); out=prior['output'] if prior and Path(prior['output']).exists() else None
            c.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',(job_id,batch_id,model,'complete' if out else 'pending',out,None,now,now,key))
            planned.append((job_id,model,out))
    for job_id,model,out in planned:
        if not out:
            task=asyncio.create_task(run_job(job_id,model,request.text.strip(),ref['path'],effective_transcript))
            TASKS[job_id]=task
            task.add_done_callback(lambda _,jid=job_id:TASKS.pop(jid,None))
    return {'id':batch_id,'jobs':[{'id':j,'model_id':m,'status':'complete' if out else 'pending','cached':bool(out)} for j,m,out in planned]}

@app.get('/api/generations/{batch_id}')
def batch(batch_id:str):
    with db() as c:
        b=c.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
        if not b: raise HTTPException(404,'Batch not found')
        jobs=c.execute('SELECT id,model_id,status,error,output,created,updated FROM jobs WHERE batch_id=? ORDER BY created,id',(batch_id,)).fetchall()
    return {'id':batch_id,'text':b['text'],'reference_id':b['reference_id'],'jobs':[dict(j)|{'audio_url':f'/api/recordings/{j["id"]}/audio' if j['output'] else None,'output':None} for j in jobs]}

@app.get('/api/recordings/{job_id}/audio')
def recording(job_id:str):
    with db() as c: row=c.execute("SELECT output FROM jobs WHERE id=? AND status='complete'",(job_id,)).fetchone()
    if not row or not row['output'] or not Path(row['output']).exists():raise HTTPException(404,'Recording not available')
    return FileResponse(row['output'],media_type='audio/wav',filename=f'{job_id}.wav')

@app.get('/api/experiments')
def experiments(limit:int=30):
    with db() as c: rows=c.execute('SELECT * FROM batches ORDER BY created DESC LIMIT ?', (min(max(limit,1),100),)).fetchall()
    return [dict(r) for r in rows]

@app.get('/api/evaluations/{job_id}')
def evaluations(job_id:str):
    with db() as c:
        exists=c.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not exists: raise HTTPException(404,'Unknown job')
        results=c.execute('SELECT name,value,evaluator FROM metrics WHERE job_id=?',(job_id,)).fetchall()
    return {'job_id':job_id,'metrics':[dict(r) for r in results],'status':'available' if results else 'not_evaluated'}

# Additional endpoints: persistent workspace, batch experiments, evaluation, exports.
import extend_backend  # noqa: E402,F401

@app.middleware('http')
async def protect_api(request, call_next):
    token=os.environ.get('API_TOKEN')
    if token and request.url.path.startswith('/api/') and request.url.path not in ('/api/health',):
        if request.headers.get('authorization') != 'Bearer '+token:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'Unauthorized'}, status_code=401)
    return await call_next(request)
