"""Additional production API endpoints and evaluation orchestration."""
import csv, io, json, os, time, uuid, zipfile, threading
from pathlib import Path
from typing import Optional
from fastapi import HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import backend as core

with core.db() as conn:
    conn.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS human_ratings (job_id TEXT,rater TEXT,category TEXT,rating INTEGER,PRIMARY KEY(job_id,rater,category))')
    conn.execute('CREATE TABLE IF NOT EXISTS evaluation_errors (job_id TEXT,name TEXT,reason TEXT,PRIMARY KEY(job_id,name))')
    conn.execute('CREATE TABLE IF NOT EXISTS batch_runs (id TEXT PRIMARY KEY,params TEXT,created REAL)')

class BatchRequest(BaseModel):
    reference_id:str
    models:list[str]=Field(min_length=1,max_length=3)
    texts:list[str]=Field(min_length=1,max_length=100)
    repetitions:int=Field(default=1,ge=1,le=20)
    reference_text:str=''

class RatingRequest(BaseModel):
    job_id:str
    rater:str=Field(min_length=1,max_length=100)
    category:str=Field(pattern='^(naturalness|similarity|intelligibility)$')
    rating:int=Field(ge=1,le=5)

class LayoutRequest(BaseModel):
    active:list[str]

@core.app.get('/api/workspace')
def workspace():
    with core.db() as c:
        row=c.execute("SELECT value FROM settings WHERE key='layout'").fetchone()
    return json.loads(row['value']) if row else {'active':list(core.MODEL_IDS)}

@core.app.patch('/api/workspace')
def save_workspace(body:LayoutRequest):
    if len(body.active)!=len(set(body.active)) or any(m not in core.MODEL_IDS for m in body.active):raise HTTPException(422,'Invalid model layout')
    with core.db() as c:c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('layout',json.dumps(body.model_dump())))
    return body

@core.app.post('/api/ratings')
def add_rating(body:RatingRequest):
    with core.db() as c:
        if not c.execute('SELECT id FROM jobs WHERE id=?',(body.job_id,)).fetchone():raise HTTPException(404,'Recording not found')
        c.execute('INSERT OR REPLACE INTO human_ratings VALUES (?,?,?,?)',(body.job_id,body.rater,body.category,body.rating))
    return {'status':'recorded'}

@core.app.get('/api/ratings/{job_id}')
def ratings(job_id:str):
    with core.db() as c:rows=c.execute('SELECT rater,category,rating FROM human_ratings WHERE job_id=?',(job_id,)).fetchall()
    return [dict(x) for x in rows]

@core.app.get('/api/agreement')
def agreement(category:str='naturalness'):
    with core.db() as c:rows=c.execute('SELECT job_id,rater,rating FROM human_ratings WHERE category=?',(category,)).fetchall()
    raters=sorted(set(x['rater'] for x in rows)); by={(x['job_id'],x['rater']):x['rating'] for x in rows}
    if len(raters)<2:return {'status':'insufficient_raters','kappa':None}
    a,b=raters[:2]; common=sorted(set(x['job_id'] for x in rows if x['rater']==a)&set(x['job_id'] for x in rows if x['rater']==b))
    if len(common)<2:return {'status':'insufficient_shared_items','kappa':None}
    try:
        from sklearn.metrics import cohen_kappa_score
        scores=[by[(j,a)] for j in common];other=[by[(j,b)] for j in common]
        return {'status':'completed','kappa':float(cohen_kappa_score(scores,other)),'weighted_kappa':float(cohen_kappa_score(scores,other,weights='quadratic')),'raters':[a,b],'items':len(common)}
    except Exception as e:return {'status':'unavailable','reason':str(e),'kappa':None}

@core.app.post('/api/batches',status_code=202)
async def batch_experiment(body:BatchRequest):
    if len(body.models)!=len(set(body.models)) or any(x not in core.MODEL_IDS for x in body.models):raise HTTPException(422,'Unknown model')
    if any(not x.strip() or len(x)>2000 for x in body.texts):raise HTTPException(422,'Invalid script')
    with core.db() as c:
        if not c.execute('SELECT id FROM references_audio WHERE id=?',(body.reference_id,)).fetchone():raise HTTPException(404,'Reference not found')
    parent=str(uuid.uuid4())
    with core.db() as c:c.execute('INSERT INTO batch_runs VALUES (?,?,?)',(parent,body.model_dump_json(),time.time()))
    # Reuse the same generation API, cache and dispatch logic.
    generated=[]
    for repeat in range(body.repetitions):
        for text in body.texts:
            result=await core.generate(core.GenerateRequest(text=text,reference_id=body.reference_id,models=body.models,reference_text=body.reference_text))
            generated.append(result['id'])
    return {'id':parent,'generation_ids':generated,'total_jobs':len(generated)*len(body.models)}

@core.app.get('/api/batches/{batch_id}')
def batch_status(batch_id:str):
    with core.db() as c:
        b=c.execute('SELECT * FROM batch_runs WHERE id=?',(batch_id,)).fetchone()
        if not b:raise HTTPException(404,'Batch not found')
    return {'id':batch_id,'created':b['created'],'configuration':json.loads(b['params'])}

@core.app.get('/api/exports/{batch_id}')
def export_batch(batch_id:str):
    with core.db() as c:
        rows=c.execute('SELECT jobs.*,batches.text,batches.reference_id FROM jobs JOIN batches ON jobs.batch_id=batches.id WHERE batch_id=?',(batch_id,)).fetchall()
        if not rows:raise HTTPException(404,'Experiment not found')
        metrics=c.execute('SELECT * FROM metrics WHERE job_id IN (SELECT id FROM jobs WHERE batch_id=?)',(batch_id,)).fetchall()
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        out=io.StringIO();writer=csv.writer(out)
        writer.writerow(['job_id','model_id','status','text','reference_id','error'])
        for r in rows:
            writer.writerow([r['id'],r['model_id'],r['status'],r['text'],r['reference_id'],r['error'] or ''])
            if r['output'] and Path(r['output']).is_file():archive.write(r['output'],f"audio/{r['model_id']}_{r['id']}.wav")
        archive.writestr('jobs.csv',out.getvalue())
        out=io.StringIO();writer=csv.writer(out);writer.writerow(['job_id','metric','value','evaluator'])
        for m in metrics:writer.writerow([m['job_id'],m['name'],m['value'],m['evaluator']])
        archive.writestr('metrics.csv',out.getvalue())
        archive.writestr('metadata.json',json.dumps({'batch_id':batch_id,'generated_at':time.time()}))
    buf.seek(0)
    return StreamingResponse(buf,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="voice-experiment-{batch_id}.zip"'})

_evaluator=None
_evaluator_lock=threading.Lock()
def evaluate_job(job_id:str):
    global _evaluator
    with core.db() as c:
        row=c.execute('SELECT jobs.output,jobs.created,jobs.updated,batches.text,references_audio.path reference FROM jobs JOIN batches ON batches.id=jobs.batch_id JOIN references_audio ON batches.reference_id=references_audio.id WHERE jobs.id=? AND jobs.status=?',(job_id,'complete')).fetchone()
    if not row or not row['output']:return
    with _evaluator_lock:
        if _evaluator is None:
            from evaluator import Evaluator
            _evaluator=Evaluator()
        metrics=_evaluator.evaluate(row['output'],row['reference'],row['text'],max(0,row['updated']-row['created']))
    with core.db() as c:
        for name,value,method in metrics:c.execute('INSERT OR REPLACE INTO metrics VALUES (?,?,?,?)',(job_id,name,float(value),method))
        for name,reason in _evaluator.errors.items():c.execute('INSERT OR REPLACE INTO evaluation_errors VALUES (?,?,?)',(job_id,name,reason[:400]))

@core.app.post('/api/evaluations/{job_id}',status_code=202)
async def start_evaluation(job_id:str):
    with core.db() as c:
        row=c.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row:raise HTTPException(404,'Unknown job')
    if row['status']!='complete':raise HTTPException(409,'Generation not complete')
    import asyncio
    asyncio.create_task(asyncio.to_thread(evaluate_job,job_id))
    return {'status':'scheduled','job_id':job_id}

@core.app.get('/api/evaluation-errors/{job_id}')
def evaluation_errors(job_id:str):
    with core.db() as c:rows=c.execute('SELECT name,reason FROM evaluation_errors WHERE job_id=?',(job_id,)).fetchall()
    return [dict(x) for x in rows]
