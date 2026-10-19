import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');
type Model = {id:string; name:string; ready:boolean; configured:boolean; loaded?:boolean; detail?:string};
type Job = {id:string; model_id:string; status:string; error?:string; audio_url?:string; runtime_seconds?:number|null};
type Evaluation = {status:string; error?:string; errors?:{name:string; reason:string}[]; metrics:{name:string; value:number}[]};
const defaults:Model[] = ['qwen3','cosyvoice3','xtts-v2'].map((id,i)=>({id,name:['Qwen3-TTS','CosyVoice 3','XTTS-v2'][i],ready:false,configured:false}));
const labels:Record<string,string> = {wer:'Word error rate',cer:'Character error rate',duration_s:'Audio duration',latency_s:'Generation time',rtf:'Real-time factor',ecapa_similarity:'Speaker similarity',scoreq:'ScoreQ quality'};

async function req(path:string, options:RequestInit = {}) {
  const response = await fetch(API+path, {...options,credentials:'include'});
  const body = await response.json().catch(()=>({detail:response.statusText}));
  if (!response.ok) {
    if (response.status===401 && path!=='/api/session') window.dispatchEvent(new Event('studio-login'));
    const error = new Error(typeof body.detail==='string' ? body.detail : JSON.stringify(body.detail)) as Error & {status:number};
    error.status=response.status; throw error;
  }
  return body;
}
const post = (path:string, body:unknown) => req(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function savedActive():string[] {
  try {
    const saved:unknown=JSON.parse(localStorage.getItem('voice.active') || 'null');
    if (Array.isArray(saved) && saved.every(x=>typeof x==='string' && defaults.some(m=>m.id===x))) return [...new Set(saved)];
  } catch { /* Use defaults when browser storage is invalid. */ }
  return defaults.map(m=>m.id);
}
function Audio({path}:{path:string}) {
  return <audio controls preload="metadata" crossOrigin="use-credentials" src={API+path}/>;
}
function App() {
  const [tab,setTab]=useState('Compare');
  const [models,setModels]=useState<Model[]>(defaults);
  const [active,setActive]=useState<string[]>(savedActive);
  const [reference,setReference]=useState<{id:string;filename:string}|null>(null);
  const [referenceText,setReferenceText]=useState('');
  const [text,setText]=useState('');
  const [batchId,setBatchId]=useState(()=>localStorage.getItem('voice.batch') || '');
  const [jobs,setJobs]=useState<Job[]>([]);
  const [submitting,setSubmitting]=useState(false);
  const [metrics,setMetrics]=useState<Record<string,Evaluation>>({});
  const [notice,setNotice]=useState('');
  const [loginRequired,setLoginRequired]=useState(false);
  const [token,setToken]=useState('');
  const [sessionVersion,setSessionVersion]=useState(0);
  const [rater,setRater]=useState('');
  const [rating,setRating]=useState(5);
  const [ratingJob,setRatingJob]=useState('');
  const [agreement,setAgreement]=useState('');
  const dragged=useRef('');
  const busy=submitting || jobs.some(j=>['pending','running'].includes(j.status));
  const eligible=active.filter(id=>models.find(m=>m.id===id)?.configured);

  useEffect(()=>{const listener=()=>setLoginRequired(true);window.addEventListener('studio-login',listener);return()=>window.removeEventListener('studio-login',listener)},[]);
  useEffect(()=>{localStorage.setItem('voice.active',JSON.stringify(active))},[active]);
  useEffect(()=>{
    let cancelled=false;
    const load=async()=>{
      try {
        const [profile,available]=await Promise.all([req('/api/reference-profile'),req('/api/models')]);
        if(cancelled)return;
        setReference(profile.reference);setReferenceText(profile.transcript);setModels(available);
      } catch(error) {if(!cancelled)setNotice(String(error))}
    };
    load();const timer=window.setInterval(load,15000);
    return()=>{cancelled=true;clearInterval(timer)};
  },[sessionVersion]);
  useEffect(()=>{
    if(!batchId)return;
    localStorage.setItem('voice.batch',batchId);
    let cancelled=false; let timer:number;
    const poll=async()=>{
      try {
        const result=await req('/api/generations/'+batchId);
        if(cancelled)return;
        setJobs(result.jobs);setText(result.text);
        if(result.jobs.some((j:Job)=>['pending','running'].includes(j.status))) timer=window.setTimeout(poll,1500);
      }catch(error){
        if(cancelled)return;
        if((error as Error & {status:number}).status===404){setBatchId('');setJobs([]);localStorage.removeItem('voice.batch')}
        else if((error as Error & {status:number}).status!==401)timer=window.setTimeout(poll,4000);
        setNotice(String(error));
      }
    };
    poll();return()=>{cancelled=true;clearTimeout(timer)};
  },[batchId,sessionVersion]);
  useEffect(()=>{
    if(tab!=='Metrics' || !jobs.length)return;
    let cancelled=false;let timer:number;
    const refresh=async()=>{
      const results=await Promise.all(jobs.map(async j=>[j.id,await req('/api/evaluations/'+j.id).catch(()=>({status:'unavailable',metrics:[]}))] as const));
      if(cancelled)return;
      setMetrics(Object.fromEntries(results));
      if(results.some(([,r])=>['pending','running'].includes(r.status)) || busy)timer=window.setTimeout(refresh,2000);
    };
    refresh();return()=>{cancelled=true;clearTimeout(timer)};
  },[tab,jobs,sessionVersion,busy]);

  async function generate(){
    if(!text.trim() || !reference || !eligible.length)return;
    setSubmitting(true);setNotice('');
    try {
      const result=await post('/api/generations',{text,models:eligible});
      setJobs(result.jobs);setMetrics({});setBatchId(result.id);
      if(eligible.length!==active.length)setNotice('Unavailable models were skipped. Their cards show the required setup.');
    }catch(error){setNotice(String(error))}finally{setSubmitting(false)}
  }
  function move(id:string,target:'library'|'comparison'){
    if(!defaults.some(m=>m.id===id))return;
    setActive(a=>target==='library'?a.filter(x=>x!==id):a.includes(id)?a:[...a,id]);
  }
  function drop(target:'library'|'comparison'){move(dragged.current,target);dragged.current=''}
  function reorder(target:string){
    const id=dragged.current;
    if(!id || id===target)return;
    setActive(a=>{const next=a.filter(x=>x!==id);next.splice(next.indexOf(target),0,id);return next});dragged.current='';
  }
  function card(id:string){
    const model=models.find(m=>m.id===id)!;const job=jobs.find(j=>j.model_id===id);
    return <article className="voice-card" key={id} draggable onDragStart={()=>{dragged.current=id}} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.stopPropagation();reorder(id)}}>
      <header><span className="handle" aria-hidden="true">⋮</span><strong>{model.name}</strong><button className="text-btn" onClick={()=>move(id,'library')}>Remove</button></header>
      {job?.status==='complete' && job.audio_url?<Audio path={job.audio_url}/>:<div className="empty-track"><div className="track-line"/><span>{job?.status==='running'?'Generating…':job?.status==='pending'?'Queued for this model':job?.status==='failed'?'Generation failed':model.configured?'Ready to generate':'Setup required'}</span></div>}
      {model.detail && <p className="model-detail">{model.detail}</p>}
      <footer><span className={'status '+(job?.status==='failed'?'error':model.configured?'ready':'')}>{job?.status || (model.configured?'Available':'Unavailable')}</span>{job?.runtime_seconds!=null && <span>{job.runtime_seconds.toFixed(2)} s</span>}{job?.error && <span className="error">{job.error}</span>}</footer>
    </article>;
  }
  async function saveRating(){
    try {
      await post('/api/ratings',{job_id:ratingJob,rater:rater.trim(),category:'naturalness',rating});
      const result=await req('/api/agreement');
      setAgreement(result.kappa==null?'Agreement needs at least two raters with varied scores on shared recordings.':`Cohen’s kappa: ${result.kappa.toFixed(3)} (${result.items} recordings, ${result.raters.join(', ')})`);
      setNotice('Rating saved.');
    }catch(error){setNotice(String(error))}
  }
  return <div className="app">
    <aside><div className="brand">Voice Comparison Studio</div><div className="side-title">MODEL LIBRARY</div><section className="library" onDragOver={e=>e.preventDefault()} onDrop={()=>drop('library')}>
      {models.filter(m=>!active.includes(m.id)).map(m=><button className="library-item" key={m.id} draggable onDragStart={()=>{dragged.current=m.id}} onClick={()=>move(m.id,'comparison')}>{m.name}<span>Add +</span></button>)}<div className="drop-hint">Drag models here to remove them from comparison.</div>
    </section><p className="sidebar-note">Move models without reloading the page. Saved audio stays available.</p></aside>
    <div className="main"><nav><div className="nav-tabs">{['Compare','Metrics'].map(t=><button key={t} className={tab===t?'selected':''} onClick={()=>setTab(t)}>{t}</button>)}</div><span className="app-status">{models.filter(m=>m.configured).length} / 3 models available</span></nav>
    <main>{notice && <div className="notice" role="alert">{notice}<button onClick={()=>setNotice('')}>Dismiss</button></div>}
    {loginRequired && <form className="panel login" onSubmit={async e=>{e.preventDefault();try{await post('/api/session',{token});setToken('');setLoginRequired(false);setNotice('');setSessionVersion(v=>v+1)}catch(error){setNotice(String(error))}}}><h2>Connect to your studio</h2><label>Access token <input type="password" autoComplete="current-password" value={token} onChange={e=>setToken(e.target.value)}/></label><button className="outline" type="submit">Connect</button></form>}
    {tab==='Compare'?<>
      <div className="heading"><div><h1>Compare voices</h1><p>Selected available models generate simultaneously.</p></div><button className="primary" onClick={generate} disabled={busy || loginRequired || !reference || !text.trim() || !eligible.length}>{busy?'Generating…':'Generate'}</button></div>
      <label className="input-label" htmlFor="speech">Text to generate</label><textarea id="speech" value={text} onChange={e=>setText(e.target.value)} maxLength={2000} placeholder="Enter the speech you want all models to generate."/>
      <div className="grid" onDragOver={e=>e.preventDefault()} onDrop={()=>drop('comparison')}>
        <article className="voice-card reference"><header><strong>Original voice</strong><span className="quiet">Server reference</span></header>{reference?<Audio path={'/api/references/'+reference.id+'/audio'}/>:<p>Place reference.wav and reference.txt in backend/reference/, then restart the server.</p>}<p className="reference-transcript">{referenceText}</p></article>
        {active.map(card)}{!active.length && <div className="empty-workspace">Add a model from the library.</div>}
      </div>
    </>:<>
      <div className="heading"><div><h1>Metrics & analysis</h1><p>Measured results for generated voices. Error rates are lower when better; speaker similarity is higher when better.</p></div></div>
      {!jobs.length?<div className="panel">Generate audio to see measurements.</div>:<div className="panel metrics-panel"><table><thead><tr><th>Model</th><th>Evaluation</th><th>Measurements</th></tr></thead><tbody>{jobs.map(job=>{
        const evaluation=metrics[job.id];return <tr key={job.id}><td>{models.find(m=>m.id===job.model_id)?.name}</td><td>{job.status!=='complete'?job.status:evaluation?.status || 'Loading…'}{job.status==='complete' && ['not_evaluated','failed','interrupted'].includes(evaluation?.status) && <button className="outline" onClick={async()=>{try{await post('/api/evaluations/'+job.id,{});setSessionVersion(v=>v+1)}catch(error){setNotice(String(error))}}}>Evaluate</button>}</td><td>{evaluation?.metrics.map(metric=><div key={metric.name}>{labels[metric.name]||metric.name}: <strong>{['wer','cer'].includes(metric.name)?(metric.value*100).toFixed(2)+'%':metric.value.toFixed(3)+(['duration_s','latency_s'].includes(metric.name)?' s':'')}</strong></div>)}{(job.error || evaluation?.error) && <p className="error">{job.error || evaluation?.error}</p>}{evaluation?.errors?.map(error=><p className="error" key={error.name}>{error.name}: {error.reason}</p>)}</td></tr>;
      })}</tbody></table><div className="rating-section"><h3>Human evaluation</h3><p>Rate naturalness from 1 (poor) to 5 (excellent).</p><label>Recording <select value={ratingJob} onChange={e=>setRatingJob(e.target.value)}><option value="">Select a generated voice</option>{jobs.filter(j=>j.status==='complete').map(j=><option key={j.id} value={j.id}>{models.find(m=>m.id===j.model_id)?.name}</option>)}</select></label><label>Rater <input value={rater} maxLength={100} onChange={e=>setRater(e.target.value)}/></label><label>Score <select value={rating} onChange={e=>setRating(Number(e.target.value))}>{[1,2,3,4,5].map(n=><option key={n}>{n}</option>)}</select></label><button className="outline" disabled={!ratingJob || !rater.trim()} onClick={saveRating}>Save rating</button>{agreement && <p>{agreement}</p>}</div></div>}
    </>}
    </main></div>
  </div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
