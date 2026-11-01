import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');
let sessionToken = '';
let sessionEpoch = 0;
const crossSiteApi = !!API && new URL(API, window.location.href).origin !== window.location.origin;
function sessionHeaders(): Record<string, string> { return sessionToken ? { Authorization: 'Session ' + sessionToken } : {}; }

interface Model {
  id: string;
  name: string;
  ready: boolean;
  configured: boolean;
  loaded?: boolean;
  detail?: string;
}

interface Job {
  id: string;
  model_id: string;
  status: string;
  error?: string;
  audio_url?: string;
  runtime_seconds?: number | null;
  started_at?: number | null;
}

interface Evaluation {
  status: string;
  error?: string;
  errors?: { name: string; reason: string }[];
  metrics: { name: string; value: number; evaluator?: string }[];
}

const defaults: Model[] = [
  { id: 'qwen3', name: 'Qwen3-TTS (1.7B)', ready: false, configured: false },
  { id: 'cosyvoice3', name: 'CosyVoice 3 (0.5B)', ready: false, configured: false },
  { id: 'xtts-v2', name: 'XTTS-v2', ready: false, configured: false },
];

interface MetricDef {
  key: string;
  label: string;
  benchmark: string;
  category: 'Accuracy' | 'Speed & Latency' | 'Acoustic Quality' | 'Signal diagnostics';
  higherIsBetter?: boolean;
  format: (v: number) => string;
  getHealth?: (v: number) => { status: 'excellent' | 'good' | 'warning'; text: string };
}

const METRIC_DEFS: MetricDef[] = [
  { key: 'script_wpm', label: 'Script pace', category: 'Signal diagnostics',
    benchmark: 'Script words ÷ total audio minutes, including pauses. Not ASR-verified.',
    format: v => v.toFixed(1) + ' words/min' },
  { key: 'quiet_frame_pct', label: 'Quiet frames', category: 'Signal diagnostics',
    benchmark: '20 ms frames below −40 dBFS RMS; includes quiet speech and pauses.',
    format: v => v.toFixed(1) + '%' },
  { key: 'near_clip_pct', label: 'Near-clipping samples', category: 'Signal diagnostics',
    benchmark: 'Samples at ≥ 99.9% full scale. A warning indicator, not proof of distortion.',
    format: v => v.toFixed(3) + '%' },
  {
    key: 'cer',
    label: 'Character Error Rate (CER)',
    benchmark: 'Good: < 1.5% | OK: 1.5%–4.0% | Bad: > 4.0%',
    category: 'Accuracy',
    higherIsBetter: false,
    format: (v) => (v * 100).toFixed(2) + '%',
    getHealth: (v) =>
      v < 0.015
        ? { status: 'excellent', text: 'GOOD' }
        : v <= 0.04
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'wer',
    label: 'Word Error Rate (WER)',
    benchmark: 'Good: < 3.0% | OK: 3.0%–8.0% | Bad: > 8.0%',
    category: 'Accuracy',
    higherIsBetter: false,
    format: (v) => (v * 100).toFixed(2) + '%',
    getHealth: (v) =>
      v < 0.03
        ? { status: 'excellent', text: 'GOOD' }
        : v <= 0.08
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'rtf',
    label: 'Real-Time Factor (RTF)',
    benchmark: 'Good: < 0.80 | OK: 0.80–1.00 | Bad: > 1.00',
    category: 'Speed & Latency',
    higherIsBetter: false,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v < 0.80
        ? { status: 'excellent', text: 'GOOD' }
        : v <= 1.0
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'rtfx',
    label: 'Inverse RTF (RTFx)',
    benchmark: 'Good: > 1.25x | OK: 1.00x–1.25x | Bad: < 1.00x',
    category: 'Speed & Latency',
    higherIsBetter: true,
    format: (v) => v.toFixed(2) + 'x',
    getHealth: (v) =>
      v >= 1.25
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 1.0
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'first_audio_ms',
    label: 'Time to First Audio Chunk',
    benchmark: 'Measured completed audio chunk; excludes model loading',
    category: 'Speed & Latency',
    higherIsBetter: false,
    format: (v) => v.toFixed(0) + ' ms',
    getHealth: (v) =>
      v < 350
        ? { status: 'excellent', text: 'GOOD' }
        : v <= 500
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'ecapa_similarity',
    label: 'Speaker Similarity',
    benchmark: 'Good: > 0.75 | OK: 0.65–0.75 | Bad: < 0.65',
    category: 'Acoustic Quality',
    higherIsBetter: true,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v >= 0.75
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 0.65
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'scoreq',
    label: 'ScoreQ predicted quality',
    benchmark: 'Good: > 4.20 | OK: 3.80–4.20 | Bad: < 3.80',
    category: 'Acoustic Quality',
    higherIsBetter: true,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v >= 4.20
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 3.80
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'dnsmos_ovrl',
    label: 'DNSMOS (OVRL)',
    benchmark: 'Good: > 3.60 | OK: 3.20–3.60 | Bad: < 3.20',
    category: 'Acoustic Quality',
    higherIsBetter: true,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v >= 3.60
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 3.20
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'dnsmos_sig',
    label: 'DNSMOS Signal (SIG)',
    benchmark: 'Good: > 3.80 | OK: 3.30–3.80 | Bad: < 3.30',
    category: 'Acoustic Quality',
    higherIsBetter: true,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v >= 3.80
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 3.30
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'dnsmos_bak',
    label: 'DNSMOS Background (BAK)',
    benchmark: 'Good: > 4.00 | OK: 3.50–4.00 | Bad: < 3.50',
    category: 'Acoustic Quality',
    higherIsBetter: true,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v >= 4.00
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 3.50
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
];

const PRESET_SCRIPTS_DE = [
  {
    name: 'Daily Routine (Standard Benchmark)',
    text: 'Guten Tag! Mein Name ist Thomas und ich wohne in Berlin. Jeden Tag stehe ich um sieben Uhr auf. Ich trinke einen Kaffee und esse ein Brötchen mit Käse zum Frühstück. Danach fahre ich mit der U-Bahn zur Arbeit. Ich arbeite von acht bis siebzehn Uhr in einem Büro. Am Abend koche ich gerne ein Abendessen für meine Familie oder lese ein Buch. Am Wochenende treffe ich meine Freunde im Park oder gehe im Supermarkt einkaufen. Ich mag mein Leben in Deutschland sehr.',
  },
  {
    name: 'Short Greeting',
    text: 'Hallo, willkommen im Voice Comparison Studio! Heute testen wir modernste neuronale Sprachsynthese und Sprachklonung in Echtzeit.',
  },
  {
    name: 'Technical & Numbers',
    text: 'Im Jahr 1980 wurden über 65.000 Wohnungen gefördert. Die aktuellen Messwerte zeigen eine Latenz von 240 Millisekunden und einen Durchsatz von 1.85-facher Echtzeit.',
  },
  {
    name: 'Narrative Story',
    text: 'In einem kleinen Dorf, umgeben von dichten Wäldern und sanften Bergen, lebte einst ein weiser Erzähler. Seine Geschichten brachten Menschen aus allen Richtungen zusammen, um der Schönheit des Klangs zu lauschen.',
  },
];

const PRESET_SCRIPTS_EN = [
  {
    name: 'Daily Routine (Standard Benchmark)',
    text: "Good morning! My name is Thomas and I live in Berlin. Every day I wake up at seven o'clock. I drink a coffee and have bread with cheese for breakfast. After that, I take the train to work. I work from eight to five in an office. In the evening, I enjoy cooking dinner for my family or reading a book. On weekends, I meet friends in the park or go shopping at the supermarket. I really enjoy my life here.",
  },
  {
    name: 'Short Greeting',
    text: 'Hello and welcome to the Voice Comparison Studio! Today we are benchmarking state-of-the-art neural speech synthesis and voice cloning in real time.',
  },
  {
    name: 'Technical & Numbers',
    text: 'In the year 1980, over 65,000 apartments were subsidized. The latest measurements show a latency of 240 milliseconds and a throughput of 1.85 times real-time.',
  },
  {
    name: 'Narrative Story',
    text: 'In a small village surrounded by dense forests and gentle mountains, there once lived a wise storyteller. His tales brought people from all directions together to listen to the beauty of sound.',
  },
];

async function req(path: string, options: RequestInit = {}) {
  const epoch = sessionEpoch;
  const response = await fetch(API + path, { ...options, headers: { 'X-Studio-CSRF': '1', ...sessionHeaders(), ...options.headers }, credentials: 'include' });
  const body = await response.json().catch(() => ({ detail: response.statusText }));
  if (epoch !== sessionEpoch) throw new Error('Session changed; previous request discarded');
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/session') {
      window.dispatchEvent(new Event('studio-login'));
    }
    const error = new Error(
      typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    ) as Error & { status: number };
    error.status = response.status;
    throw error;
  }
  return body;
}

const post = (path: string, body: unknown) =>
  req(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

function savedActive(): string[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem('voice.active') || 'null');
    if (
      Array.isArray(saved) &&
      saved.every((x) => typeof x === 'string' && defaults.some((m) => m.id === x))
    ) {
      return [...new Set(saved)];
    }
  } catch {
    /* fallback to defaults */
  }
  return defaults.map((m) => m.id);
}

interface ReferenceInfo {
  id: string;
  filename: string;
  transcript?: string;
  duration_s?: number;
  sample_rate?: number;
  channels?: number;
}

async function privateBlob(path: string, signal?: AbortSignal) {
  const epoch = sessionEpoch;
  const response = await fetch(API + path, { headers: sessionHeaders(), credentials: 'include', signal });
  if (epoch !== sessionEpoch) throw new Error('Session changed; previous request discarded');
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('studio-login'));
    throw new Error('Private download failed (' + response.status + ')');
  }
  const blob = await response.blob();
  if (epoch !== sessionEpoch) throw new Error('Session changed');
  return blob;
}

async function downloadPrivate(event: React.MouseEvent<HTMLAnchorElement>, path: string, filename: string) {
  if (!sessionToken) return;
  event.preventDefault();
  try {
    const blob = await privateBlob(path);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = filename; document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { window.dispatchEvent(new CustomEvent('studio-download-error', { detail: String(error) })); }
}

function AudioPlayer({ path, title }: { path: string; title?: string }) {
  const [source, setSource] = useState(sessionToken ? '' : API + path);
  useEffect(() => {
    if (!sessionToken) { setSource(API + path); return; }
    const abort = new AbortController(); let objectUrl = '';
    setSource('');
    privateBlob(path, abort.signal).then(blob => { if (abort.signal.aborted) return; objectUrl = URL.createObjectURL(blob); setSource(objectUrl); }).catch(error => {
      if (!abort.signal.aborted) window.dispatchEvent(new CustomEvent('studio-download-error', { detail: String(error) }));
    });
    return () => { abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [path]);
  return <div className="audio-player-box">
    {title && <span className="audio-player-label">{title}</span>}
    <audio controls preload="metadata" crossOrigin="use-credentials" src={source || undefined} />
  </div>;
}

function App() {
  const [tab, setTab] = useState<'Compare' | 'Metrics' | 'Evaluation'>('Compare');
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [language, setLanguage] = useState<'German' | 'English'>('German');
  const [models, setModels] = useState<Model[]>(defaults);
  const [active, setActive] = useState<string[]>(savedActive);
  const [reference, setReference] = useState<ReferenceInfo | null>(null);
  const [savedReferences, setSavedReferences] = useState<{id:string;filename:string}[]>([]);
  const [revealed, setRevealed] = useState(false);
  const [text, setText] = useState('');
  const [speechRate, setSpeechRate] = useState('');
  const [temperatures, setTemperatures] = useState<Record<string, string>>({});
  const [batchId, setBatchId] = useState(() => localStorage.getItem('voice.batch') || '');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [metrics, setMetrics] = useState<Record<string, Evaluation>>({});
  const [notice, setNotice] = useState('');
  useEffect(() => {
    const listener = (event: Event) => setNotice((event as CustomEvent<string>).detail);
    window.addEventListener('studio-download-error', listener);
    return () => window.removeEventListener('studio-download-error', listener);
  }, []);

  const [loginRequired, setLoginRequired] = useState(false);
  const [token, setToken] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [authMode, setAuthMode] = useState('token');
  const [authenticated, setAuthenticated] = useState(false);
  const authenticatedRef = useRef(false);
  useEffect(() => { req('/api/session').then(s => { setAuthMode(s.auth_mode || 'token'); authenticatedRef.current = !!s.authenticated && s.principal !== 'development'; setAuthenticated(authenticatedRef.current); }).catch(() => {}); }, []);
  const [sessionVersion, setSessionVersion] = useState(0);
  const [rating, setRating] = useState(5);
  const [raterName, setRaterName] = useState('');
  const [ratingJob, setRatingJob] = useState('');
  const [agreement, setAgreement] = useState('');
  const [metricGroup, setMetricGroup] = useState('Overview');
  const visibleMetrics = METRIC_DEFS.filter(def => metricGroup === 'All metrics' ||
    (metricGroup === 'Overview' ? ['wer', 'ecapa_similarity', 'scoreq', 'rtfx'].includes(def.key) : def.category === metricGroup));
  const [selectedMetricModel, setSelectedMetricModel] = useState<string>('all');
  const dragged = useRef('');
  const [clock, setClock] = useState(Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const busy = submitting || jobs.some((j) => ['pending', 'running'].includes(j.status));
  const eligible = active.filter((id) => models.find((m) => m.id === id)?.configured);

  useEffect(() => {
    const listener = () => {
      if (authenticatedRef.current) sessionEpoch += 1;
      authenticatedRef.current = false; sessionToken = '';
      setAuthenticated(false); setLoginRequired(true);
      setText(''); setReference(null); setJobs([]); setBatchId(''); setMetrics({}); setSavedReferences([]);
      setAgreement(''); setRatingJob(''); setRaterName(''); localStorage.removeItem('voice.batch');
    };
    window.addEventListener('studio-login', listener);
    return () => window.removeEventListener('studio-login', listener);
  }, []);

  useEffect(() => {
    localStorage.setItem('voice.active', JSON.stringify(active));
  }, [active]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [profile, available] = await Promise.all([
          req('/api/reference-profile?language=' + language),
          req('/api/models'),
        ]);
        if (cancelled) return;
        if (!profile || !('reference' in profile) || !Array.isArray(available) ||
            !available.every(model => model && typeof model.id === 'string' && typeof model.name === 'string' && typeof model.configured === 'boolean')) {
          throw new Error('Backend API response is incompatible with this frontend');
        }
        setReference(profile.reference);
        setModels(available);
      } catch (error) {
        if (!cancelled) setNotice(String(error));
      }
    };
    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionVersion, language]);

  useEffect(() => {
    if (!batchId) return;
    localStorage.setItem('voice.batch', batchId);
    let cancelled = false;
    let timer: number;
    const poll = async () => {
      try {
        const result = await req('/api/generations/' + batchId);
        if (cancelled) return;
        if (!result || !Array.isArray(result.jobs) || typeof result.text !== 'string') {
          throw new Error('Backend returned an invalid generation status');
        }
        setJobs(result.jobs);
        setText(result.text);
        if (result.jobs.some((j: Job) => ['pending', 'running'].includes(j.status))) {
          timer = window.setTimeout(poll, 1500);
        }
      } catch (error) {
        if (cancelled) return;
        if ((error as Error & { status: number }).status === 404) {
          setBatchId('');
          setJobs([]);
          localStorage.removeItem('voice.batch');
        } else if ((error as Error & { status: number }).status !== 401) {
          timer = window.setTimeout(poll, 4000);
        }
        setNotice(String(error));
      }
    };
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [batchId, sessionVersion]);

  useEffect(() => {
    if (tab !== 'Metrics' || !jobs.length) return;
    let cancelled = false;
    let timer: number;
    const refresh = async () => {
      const results = await Promise.all(
        jobs.map(
          async (j) =>
            [
              j.id,
              await req('/api/evaluations/' + j.id).catch(() => ({
                status: 'unavailable',
                metrics: [],
              })),
            ] as const
        )
      );
      if (cancelled) return;
      setMetrics(Object.fromEntries(results));
      if (results.some(([, r]) => ['pending', 'running'].includes(r.status)) || busy) {
        timer = window.setTimeout(refresh, 2000);
      }
    };
    refresh();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [tab, jobs, sessionVersion, busy]);

  async function generate() {
    if (!text.trim() || !reference || !eligible.length) return;
    setSubmitting(true);
    setNotice('');
    try {
      const result = await post('/api/generations', {
        text,
        models: eligible,
        language,
        reference_id: reference.id,
        speech_rate: speechRate ? Number(speechRate) : null,
        model_settings: Object.fromEntries(eligible.filter(model => model !== "cosyvoice3" && temperatures[model]).map(model => [model, { temperature: Number(temperatures[model]) }])),
      });
      setJobs(result.jobs);
      setMetrics({});
      setBatchId(result.id);
      if (eligible.length !== active.length) {
        setNotice('Unavailable models were skipped. See configuration details.');
      }
    } catch (error) {
      setNotice(String(error));
    } finally {
      setSubmitting(false);
    }
  }

  function move(id: string, target: 'library' | 'comparison') {
    if (!defaults.some((m) => m.id === id)) return;
    setActive((a) =>
      target === 'library' ? a.filter((x) => x !== id) : a.includes(id) ? a : [...a, id]
    );
  }

  function drop(target: 'library' | 'comparison') {
    move(dragged.current, target);
    dragged.current = '';
  }

  function reorder(target: string) {
    const id = dragged.current;
    if (!id || id === target) return;
    setActive((a) => {
      const next = a.filter((x) => x !== id);
      next.splice(next.indexOf(target), 0, id);
      return next;
    });
    dragged.current = '';
  }

  function shiftModel(id: string, direction: number) {
    setActive((current) => {
      const from = current.indexOf(id);
      const to = from + direction;
      if (from < 0 || to < 0 || to >= current.length) return current;
      const next = [...current];
      [next[from], next[to]] = [next[to], next[from]];
      return next;
    });
  }

  function card(id: string) {
    const model = models.find((m) => m.id === id) || {
      id,
      name: id,
      ready: false,
      configured: false,
    };
    const job = jobs.find((j) => j.model_id === id);
    return (
      <article
        className="voice-card"
        key={id}
        draggable={window.matchMedia('(pointer: fine)').matches}
        onDragStart={() => {
          dragged.current = id;
        }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.stopPropagation();
          reorder(id);
        }}
      >
        <header>
          <span className="handle" aria-hidden="true">
            ⋮⋮
          </span>
          <strong>{model.name}</strong>
          <div className="card-actions">
            <button className="order-btn" aria-label={`Move ${model.name} earlier`}
              disabled={active.indexOf(id) === 0} onClick={() => shiftModel(id, -1)}>↑</button>
            <button className="order-btn" aria-label={`Move ${model.name} later`}
              disabled={active.indexOf(id) === active.length - 1} onClick={() => shiftModel(id, 1)}>↓</button>
            <button className="text-btn" aria-label={`Remove ${model.name}`}
              onClick={() => move(id, 'library')}>Remove</button>
          </div>
        </header>

        {job?.status === 'complete' && job.audio_url ? (
          <AudioPlayer path={job.audio_url} />
        ) : (
          <div className="empty-track">
            <div className="track-line" />
            <span>
              {job?.status === 'running'
                ? 'Synthesizing voice…'
                : job?.status === 'pending'
                ? 'Queued in execution pipeline'
                : job?.status === 'failed'
                ? 'Generation failed'
                : model.configured
                ? 'Ready to generate'
                : 'Setup required'}
            </span>
          </div>
        )}

        {model.detail && <p className="model-detail">{model.detail}</p>}

        <footer>
          <span
            className={
              'status-pill ' +
              (job?.status === 'complete'
                ? 'status-success'
                : job?.status === 'running'
                ? 'status-running'
                : job?.status === 'failed'
                ? 'status-error'
                : model.configured
                ? 'status-ready'
                : 'status-neutral')
            }
          >
            {job?.status === 'complete'
              ? 'Complete'
              : job?.status === 'running'
              ? 'Synthesizing…'
              : job?.status === 'pending'
              ? 'Pending'
              : job?.status === 'failed'
              ? 'Failed'
              : model.configured
              ? 'Ready'
              : 'Unavailable'}
          </span>

          {job?.status === 'running' && job.started_at && (
            <span className="timing-stat">
              Elapsed: {Math.max(0, clock / 1000 - job.started_at).toFixed(1)}s
            </span>
          )}
          {job?.runtime_seconds != null && (
            <span className="timing-stat">Latency: {job.runtime_seconds.toFixed(2)}s</span>
          )}
          {job?.status === 'complete' && job.audio_url && (
            <a
              className="btn-card-download"
              href={API + job.audio_url}
              download={`${model.name.replace(/\s+/g, '_')}.wav`}
              onClick={event => downloadPrivate(event, job.audio_url!, `${model.name.replace(/\s+/g, '_')}.wav`)}
              title={`Download ${model.name} WAV recording`}
            >
              Download WAV
            </a>
          )}
          {job?.error && <span className="error">{job.error}</span>}
        </footer>
      </article>
    );
  }

  async function saveRating() {
    if (!raterName.trim()) { setNotice('Enter a reviewer identifier before saving a rating.'); return; }
    try {
      await post('/api/ratings', {
        job_id: ratingJob,
        rater: raterName.trim(),
        category: 'naturalness',
        rating,
      });
      const result = await req('/api/agreement');
      setAgreement(
        result.kappa == null
          ? 'Agreement needs at least two raters with varied scores on shared recordings.'
          : `Cohen’s kappa: ${result.kappa.toFixed(3)} (${result.items} recordings, ${result.raters.join(
              ', '
            )})`
      );
      setNotice('Rating successfully saved.');
    } catch (error) {
      setNotice(String(error));
    }
  }

  function getModelMetric(jobId: string | undefined, key: string): number | null {
    if (!jobId || !metrics[jobId]) return null;
    const m = metrics[jobId].metrics.find((item) => item.name === key);
    return m ? m.value : null;
  }

  const completedJobs = jobs.filter((j) => j.status === 'complete');
  const getLeader = (def: MetricDef) => {
    if (completedJobs.length < 2 || def.higherIsBetter === undefined) return null;
    let bestJob: Job | null = null;
    let bestVal: number | null = null;
    for (const j of completedJobs) {
      const v = getModelMetric(j.id, def.key);
      if (v === null) continue;
      if (bestVal === null) {
        bestVal = v;
        bestJob = j;
      } else if (def.higherIsBetter && v > bestVal) {
        bestVal = v;
        bestJob = j;
      } else if (!def.higherIsBetter && def.higherIsBetter !== undefined && v < bestVal) {
        bestVal = v;
        bestJob = j;
      }
    }
    return bestJob ? models.find((m) => m.id === bestJob!.model_id)?.name : null;
  };

  return (
    <div className="app">
      <aside className={libraryOpen ? 'library-open' : ''} aria-label="Model library and speech presets">
        <div className="brand">
          <div className="brand-copy">
            <div className="brand-logo">
              <span className="brand-pill">VCS</span>
            </div>
            <div className="brand-title">Voice Comparison Studio</div>
            <div className="brand-subtitle">Unified Neural TTS Evaluation</div>
          </div>
          <button className="sidebar-toggle" type="button"
            aria-expanded={libraryOpen} aria-controls="sidebar-content"
            onClick={() => setLibraryOpen((open) => !open)}>
            {libraryOpen ? 'Close library' : 'Models & presets'}
          </button>
        </div>

        <div id="sidebar-content" className="sidebar-content">
          <div className="side-title">AVAILABLE ENGINES</div>
          <section
            className="library"
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => drop('library')}
          >
            {models
              .filter((m) => !active.includes(m.id))
              .map((m) => (
                <button
                  className="library-item"
                  key={m.id}
                  draggable={window.matchMedia('(pointer: fine)').matches}
                  onDragStart={() => {
                    dragged.current = m.id;
                  }}
                  onClick={() => move(m.id, 'comparison')}
                >
                  <span>{m.name}</span>
                  <span className="add-pill">+ Add</span>
                </button>
              ))}
            {!models.some((m) => !active.includes(m.id)) && (
              <div className="drop-hint all-active">All engines in active comparison.</div>
            )}
            <div className="drop-hint">Tap Add or Remove to select engines. Use arrows to change their order.</div>
          </section>

          <div className="side-title" style={{ marginTop: '24px' }}>
            SPEECH PRESETS
          </div>
          <div className="preset-list">
            {(language === 'English' ? PRESET_SCRIPTS_EN : PRESET_SCRIPTS_DE).map((p, idx) => (
              <button
                key={idx}
                className="preset-btn"
                onClick={() => { setText(p.text); setLibraryOpen(false); }}
                title="Click to load preset text"
              >
                <span className="preset-chip">SCRIPT</span> {p.name}
              </button>
            ))}
          </div>

          <p className="sidebar-note">
            Supports Qwen3-TTS, CosyVoice 3, and XTTS-v2 side-by-side with natural speech timing.
          </p>
        </div>
      </aside>

      <div className="main">
        <nav aria-label="Studio navigation">
          <div className="nav-tabs">
            {(['Compare', 'Metrics', 'Evaluation'] as const).map((t) => (
              <button
                key={t}
                className={tab === t ? 'selected' : ''}
                aria-current={tab === t ? 'page' : undefined}
                onClick={() => setTab(t)}
              >
                {t === 'Compare'
                  ? 'Voice Comparison'
                  : t === 'Metrics'
                  ? 'Benchmark Metrics'
                  : 'Listening Evaluation'}
              </button>
            ))}
          </div>
          <div className="nav-right">
            <div className="language-toggle" role="group" aria-label="Language selection">
              <button
                type="button"
                className={`lang-btn ${language === 'German' ? 'active' : ''}`}
                onClick={() => setLanguage('German')}
              >
                German
              </button>
              <button
                type="button"
                className={`lang-btn ${language === 'English' ? 'active' : ''}`}
                onClick={() => setLanguage('English')}
              >
                English
              </button>
            </div>
            {authenticated && <button onClick={async () => {
              try {
                await req('/api/session', { method: 'DELETE' });
                if (authenticatedRef.current) sessionEpoch += 1;
      authenticatedRef.current = false; sessionToken = ''; setAuthenticated(false); setLoginRequired(true);
                setText(''); setAgreement(''); setRatingJob(''); setRaterName(''); localStorage.removeItem('voice.batch');
                setSessionVersion(v => v + 1);
                setReference(null); setJobs([]); setBatchId(''); setMetrics({}); setSavedReferences([]);
              } catch (error) { setNotice(String(error)); }
            }}>Sign out</button>}
            <span className="app-status">
              <span className="status-dot" aria-hidden="true"></span>
              {models.filter((m) => m.configured).length} / 3 engines active
            </span>
          </div>
        </nav>

        <main>
          {authenticated && <details className="panel data-settings">
            <summary>Manage saved data</summary>
            <p>Delete completed generations before removing references used by them.</p>
            <button disabled={!batchId || busy} onClick={async () => {
              try {
                await req('/api/generations/' + batchId, {method:'DELETE'});
                setBatchId(''); setJobs([]); setMetrics({}); setText('');
                localStorage.removeItem('voice.batch'); setNotice('Generation deleted.');
              } catch (error) { setNotice(String(error)); }
            }}>Delete current generation</button>
            <button onClick={async () => {
              try { setSavedReferences(await req('/api/references')); }
              catch (error) { setNotice(String(error)); }
            }}>Show uploaded references</button>
            {savedReferences.map(item => <div key={item.id}>
              <span>{item.filename} ({item.id.slice(0,8)}) </span>
              <button onClick={async () => {
                try {
                  await req('/api/references/' + item.id, {method:'DELETE'});
                  setSavedReferences(items => items.filter(x => x.id !== item.id));
                  if (reference?.id === item.id) { setReference(null); setSessionVersion(v => v + 1); }
                  setNotice('Reference deleted.');
                } catch (error) { setNotice(String(error)); }
              }}>Delete reference</button>
            </div>)}
          </details>}
          {tab === 'Evaluation' && <label className="reviewer-name">Reviewer identifier
            <input value={raterName} onChange={e => setRaterName(e.target.value)} maxLength={100} placeholder="Use a different identifier for each reviewer" />
          </label>}
          {notice && (
            <div className="notice" role="alert">
              <span>{notice}</span>
              <button onClick={() => setNotice('')}>Dismiss</button>
            </div>
          )}

          {loginRequired && (
            <form
              className="panel login"
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  const session = await post('/api/session', { ...(authMode === 'accounts' ? { username, password } : { token }), transport: crossSiteApi ? 'bearer' : 'cookie' });
                  sessionEpoch += 1; authenticatedRef.current = true; sessionToken = session.session_token || '';
                  setText(''); setAgreement(''); setRatingJob(''); setRaterName(''); localStorage.removeItem('voice.batch');
                  setAuthenticated(true);
                  setReference(null); setJobs([]); setBatchId(''); setMetrics({}); setSavedReferences([]);
                  setPassword('');
                  setToken('');
                  setLoginRequired(false);
                  setNotice('');
                  setSessionVersion((v) => v + 1);
                } catch (error) {
                  setNotice(String(error));
                }
              }}
            >
              <h2>Studio Authorization Required</h2>
              {authMode === 'accounts' ? <>
                <label>Username<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} maxLength={100} required /></label>
                <label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} maxLength={1000} required /></label>
              </> : <label>
                Access Token
                <input
                  type="password"
                  autoComplete="current-password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                />
              </label>}
              <button className="primary" type="submit">
                Connect Session
              </button>
            </form>
          )}

          {tab === 'Compare' && (
            <>
              <div className="heading">
                <div>
                  <h1>Voice comparison</h1>
                  <p>
                    One script. Multiple voices. Listen, compare, and choose.
                  </p>
                </div>
                <div className="heading-actions">
                  {completedJobs.length > 0 && batchId && (
                    <a
                      className="outline btn-export"
                      href={API + '/api/exports/' + batchId}
                      download={`voice_comparison_${batchId}.zip`}
                      onClick={event => downloadPrivate(event, "/api/exports/" + batchId, `voice_comparison_${batchId}.zip`)}
                      title="Download complete audio bundle and CSV metrics in a ZIP archive"
                    >
                      Export All
                    </a>
                  )}
                  <button
                    className="primary btn-large"
                    onClick={generate}
                    disabled={busy || loginRequired || !reference || !text.trim() || !eligible.length}
                  >
                    {busy ? 'Synthesizing Audio…' : 'Generate Comparison'}
                  </button>
                </div>
              </div>

              <details className="generation-settings"><summary>Generation settings <span>Speech rate &amp; temperature</span></summary><div className="generation-controls">
                <label>Speech rate
                  <select value={speechRate} onChange={e => setSpeechRate(e.target.value)}>
                    <option value="">Model default</option>
                    <option value="0.8">0.8 ×</option><option value="0.9">0.9 ×</option>
                    <option value="1">1.0 ×</option><option value="1.1">1.1 ×</option>
                  </select>
                </label>
                {eligible.filter(model => model !== 'cosyvoice3').map(model => <label key={model}>
                  {model === 'qwen3' ? 'Qwen3' : 'XTTS-v2'} temperature
                  <input type="number" min="0.1" max="1.5" step="0.05" placeholder="Model default"
                    value={temperatures[model] || ''}
                    onChange={e => setTemperatures(current => ({ ...current, [model]: e.target.value }))} />
                </label>)}
                {eligible.includes('cosyvoice3') && <small>CosyVoice uses its native sampling settings; temperature adjustment is unavailable.</small>}
                <button onClick={() => { setSpeechRate(''); setTemperatures({}); }}>Reset controls</button>
              </div>
              </details>
              <div className="script-container">
                <div className="script-header">
                  <label className="input-label" htmlFor="speech">
                    Input Speech Script
                  </label>
                  <span className="char-count">{text.length} characters</span>
                </div>
                <textarea
                  id="speech"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={2000}
                  placeholder={
                    language === 'English'
                      ? 'Enter English speech text to clone and benchmark across all TTS models simultaneously…'
                      : 'Enter German speech text to clone and benchmark across all TTS models simultaneously…'
                  }
                />
              </div>

              <div
                className="grid"
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => drop('comparison')}
              >
                <article className="voice-card reference">
                  <header>
                    <span className="badge-ref">REFERENCE</span>
                    <strong>{language} Speaker Profile</strong>
                  </header>
                  {reference ? (
                    <AudioPlayer
                      path={'/api/references/' + reference.id + '/audio'}
                      title={`Ground-Truth ${language} Voice Anchor`}
                    />
                  ) : (
                    <p className="quiet">
                      Place{' '}
                      {language === 'English'
                        ? 'reference_en.wav and reference_en.txt'
                        : 'reference.wav and reference.txt'}{' '}
                      in backend/reference/
                    </p>
                  )}
                  <footer>
                    <span
                      className={
                        'status-pill ' + (reference ? 'status-ready' : 'status-neutral')
                      }
                    >
                      {reference
                        ? `${language} Reference Active`
                        : `${language} Reference Missing`}
                    </span>
                  </footer>
                </article>

                {active.map(card)}
                {!active.length && (
                  <div className="empty-workspace">
                    Open Models &amp; presets to add engines to the comparison.
                  </div>
                )}
              </div>
            </>
          )}

          {tab === 'Metrics' && (
            <>
              <div className="heading">
                <div>
                  <h1>Compare the details</h1>
                  <p>
                    Start with the essentials. Explore accuracy, speed, quality, or signal diagnostics when you need more detail.
                  </p>
                </div>
              </div>

              {!jobs.length ? (
                <div className="panel empty-state">
                  <h3>No Voice Generations Yet</h3>
                  <p>Switch to the Compare tab and generate speech to calculate the benchmark matrix.</p>
                  <button className="primary" onClick={() => setTab('Compare')}>
                    Go to Compare Voices
                  </button>
                </div>
              ) : (
                <div className="metrics-wrapper">
                  {/* View Selector Tabs */}
                  <div className="metrics-view-tabs">
                    <button
                      className={selectedMetricModel === 'all' ? 'active' : ''}
                      onClick={() => setSelectedMetricModel('all')}
                    >
                      All models
                    </button>
                    {jobs.map((job) => {
                      const m = models.find((mod) => mod.id === job.model_id);
                      return (
                        <button
                          key={job.id}
                          className={selectedMetricModel === job.model_id ? 'active' : ''}
                          onClick={() => setSelectedMetricModel(job.model_id)}
                        >
                          {m?.name || job.model_id} Health Scorecard
                        </button>
                      );
                    })}
                  </div>

                  <div className="metric-toolbar">
                    <label>Metric focus
                      <select value={metricGroup} onChange={e => setMetricGroup(e.target.value)}>
                        {['Overview', 'Accuracy', 'Speed & Latency', 'Acoustic Quality', 'Signal diagnostics', 'All metrics'].map(group =>
                          <option key={group}>{group}</option>)}
                      </select>
                    </label>
                    <p>{visibleMetrics.length} metrics · Same script and settings give the fairest comparison.</p>
                  </div>
                  {/* View 1: Multi-Model Tabular Format */}
                  {selectedMetricModel === 'all' ? (
                    <div className="panel table-panel">
                      <div className="table-top">
                        <h2>Model Competition Matrix</h2>
                        <span className="table-caption">
                          Automated scores support listening; they do not replace it.
                        </span>
                      </div>
                      <p className="table-scroll-hint">Swipe the table to see every model and metric.</p>
                      <div className="table-responsive" role="region" tabIndex={0}
                        aria-label="Model comparison metrics; scroll horizontally to view all columns">
                        <table className="benchmark-table">
                          <thead>
                            <tr>
                              <th>Metric Field</th>
                              {jobs.map((j) => (
                                <th key={j.id}>{models.find((m) => m.id === j.model_id)?.name}</th>
                              ))}
                              <th>Leader</th>
                              <th>Interpretation</th>
                            </tr>
                          </thead>
                          <tbody>
                            {visibleMetrics.map((def) => {
                              const leaderName = getLeader(def);
                              return (
                                <tr key={def.key}>
                                  <td className="metric-title-cell">
                                    <strong>{def.label}</strong>
                                    <span className="metric-cat">{def.category}</span>
                                  </td>
                                  {jobs.map((j) => {
                                    const val = getModelMetric(j.id, def.key);
                                    if (val === null) {
                                      return (
                                        <td key={j.id} className="val-pending">
                                          {metrics[j.id]?.status === 'running'
                                            ? 'Calculating…'
                                            : '—'}
                                        </td>
                                      );
                                    }
                                    return (
                                      <td key={j.id} className="val-cell">
                                        <div className="val-text">
                                          {def.format(val)}
                                        </div>
                                      </td>
                                    );
                                  })}
                                  <td className="leader-cell">
                                    {leaderName ? (
                                      <span className="leader-badge">{leaderName}</span>
                                    ) : (
                                      '—'
                                    )}
                                  </td>
                                  <td className="metric-benchmark-cell">{def.benchmark}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : (
                    /* View 2: Detailed Tabular Format for Selected Model */
                    (() => {
                      const selJob = jobs.find((j) => j.model_id === selectedMetricModel);
                      const selModel = models.find((m) => m.id === selectedMetricModel);
                      const evalData = selJob ? metrics[selJob.id] : null;

                      return (
                        <div className="panel table-panel">
                          <div className="table-top">
                            <h2>{selModel?.name || selectedMetricModel} — Detailed Health Report</h2>
                            {selJob?.status === 'complete' &&
                              ['not_evaluated', 'failed', 'interrupted'].includes(evalData?.status || '') && (
                                <button
                                  className="outline"
                                  onClick={async () => {
                                    try {
                                      await post('/api/evaluations/' + selJob.id, {});
                                      setSessionVersion((v) => v + 1);
                                    } catch (err) {
                                      setNotice(String(err));
                                    }
                                  }}
                                >
                                  Calculate Model Metrics
                                </button>
                              )}
                          </div>

                          <p className="table-scroll-hint">Swipe the table to see every metric detail.</p>
                          <div className="table-responsive" role="region" tabIndex={0}
                            aria-label="Detailed model metrics; scroll horizontally to view all columns">
                            <table className="benchmark-table individual-table">
                              <thead>
                                <tr>
                                  <th>Metric Field</th>
                                  <th>Your Value</th>
                                  <th>Interpretation</th>
                                  <th>Heuristic assessment</th>
                                </tr>
                              </thead>
                              <tbody>
                                {visibleMetrics.map((def) => {
                                  const val = getModelMetric(selJob?.id, def.key);
                                  const health = val !== null ? def.getHealth?.(val) : null;
                                  return (
                                    <tr key={def.key}>
                                      <td className="metric-title-cell">
                                        <strong>{def.label}</strong>
                                        <span className="metric-cat">{def.category}</span>
                                      </td>
                                      <td className="metric-value-cell">
                                        {val !== null ? (
                                          <strong>{def.format(val)}</strong>
                                        ) : (
                                          <span className="val-pending">
                                            {evalData?.status === 'running'
                                              ? 'Measuring…'
                                              : 'Unavailable'}
                                          </span>
                                        )}
                                      </td>
                                      <td className="metric-benchmark-cell">{def.benchmark}</td>
                                      <td>
                                        {health ? (
                                          <span className={'health-pill ' + health.status}>
                                            {health.text}
                                          </span>
                                        ) : (
                                          '—'
                                        )}
                                      </td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>

                          {evalData?.error && <p className="error">{evalData.error}</p>}
                          {evalData?.errors?.map((err) => (
                            <p className="error" key={err.name}>
                              {err.name}: {err.reason}
                            </p>
                          ))}
                        </div>
                      );
                    })()
                  )}
                </div>
              )}
            </>
          )}

          {tab === 'Evaluation' && (
            <div className="evaluation-view">
              <div className="heading">
                <div>
                  <h1>Subjective Listening Ratings</h1>
                  <p>
                    Compare recordings and rate voice quality for internal review. Model names can be hidden; this view is not a standardized blind A/B/X study.
                  </p>
                </div>
              </div>

              {/* Blind A/B/X Evaluation Section */}
              <div className="panel blind-panel">
                <div className="panel-header-row">
                  <div>
                    <h2>Subjective Listening Ratings</h2>
                    <p>
                      Assess voice naturalness and articulation on a 1–5 scale. Names can be hidden, while the clip order follows the current comparison.
                    </p>
                  </div>
                  {completedJobs.length > 0 && (
                    <button
                      className="outline"
                      onClick={() => setRevealed((r) => !r)}
                    >
                      {revealed ? 'Hide Model Names' : 'Reveal Model Names'}
                    </button>
                  )}
                </div>

                {!completedJobs.length ? (
                  <div className="empty-state" style={{ padding: '32px 16px' }}>
                    <h3>No Completed Voice Generations Available</h3>
                    <p>Generate speech in the Voice Comparison tab first to submit listening ratings.</p>
                    <button className="primary" onClick={() => setTab('Compare')}>
                      Go to Compare Voices
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="blind-grid">
                      {completedJobs.map((j, idx) => {
                        const letters = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon'];
                        const alias = `Voice ${letters[idx] || idx + 1}`;
                        const realName = models.find((m) => m.id === j.model_id)?.name || j.model_id;
                        return (
                          <div className="blind-voice-card" key={j.id}>
                            <div className="blind-card-header">
                              <span className="blind-tag">{alias}</span>
                              {revealed && <strong className="revealed-name">{realName}</strong>}
                            </div>
                            {j.audio_url && <AudioPlayer path={j.audio_url} />}
                          </div>
                        );
                      })}
                    </div>

                    <div className="rating-inputs-row" style={{ marginTop: '20px' }}>
                      <label>
                        Recording Engine
                        <select
                          value={ratingJob}
                          onChange={(e) => setRatingJob(e.target.value)}
                        >
                          <option value="">Select a generated voice</option>
                          {completedJobs.map((j, idx) => {
                            const letters = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon'];
                            const alias = `Voice ${letters[idx] || idx + 1}`;
                            const realName = models.find((m) => m.id === j.model_id)?.name || j.model_id;
                            return (
                              <option key={j.id} value={j.id}>
                                {revealed ? `${alias} (${realName})` : alias}
                              </option>
                            );
                          })}
                        </select>
                      </label>
                      <label>
                        MOS Score
                        <select
                          value={rating}
                          onChange={(e) => setRating(Number(e.target.value))}
                        >
                          <option value={5}>5 - Excellent (Near Human)</option>
                          <option value={4}>4 - Good (Natural Speech)</option>
                          <option value={3}>3 - Fair (Minor Artifacts)</option>
                          <option value={2}>2 - Poor (Noticeable Flaws)</option>
                          <option value={1}>1 - Bad (Severely Degraded)</option>
                        </select>
                      </label>
                      <button
                        className="primary"
                        disabled={!ratingJob}
                        onClick={saveRating}
                      >
                        Submit Score
                      </button>
                    </div>

                    {agreement && <div className="agreement-box">{agreement}</div>}
                  </>
                )}
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
