import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

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
  category: 'Accuracy' | 'Speed & Latency' | 'Acoustic Quality';
  higherIsBetter?: boolean;
  format: (v: number) => string;
  getHealth: (v: number) => { status: 'excellent' | 'good' | 'warning'; text: string };
}

const METRIC_DEFS: MetricDef[] = [
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
    label: 'ScoreQ Quality (MOS)',
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
  const response = await fetch(API + path, { ...options, credentials: 'include' });
  const body = await response.json().catch(() => ({ detail: response.statusText }));
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

function AudioPlayer({ path, title }: { path: string; title?: string }) {
  return (
    <div className="audio-player-box">
      {title && <span className="audio-player-label">{title}</span>}
      <audio controls preload="metadata" crossOrigin="use-credentials" src={API + path} />
    </div>
  );
}

function App() {
  const [tab, setTab] = useState<'Compare' | 'Metrics' | 'Evaluation'>('Compare');
  const [language, setLanguage] = useState<'German' | 'English'>('German');
  const [models, setModels] = useState<Model[]>(defaults);
  const [active, setActive] = useState<string[]>(savedActive);
  const [reference, setReference] = useState<ReferenceInfo | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [text, setText] = useState('');
  const [batchId, setBatchId] = useState(() => localStorage.getItem('voice.batch') || '');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [metrics, setMetrics] = useState<Record<string, Evaluation>>({});
  const [notice, setNotice] = useState('');
  const [loginRequired, setLoginRequired] = useState(false);
  const [token, setToken] = useState('');
  const [sessionVersion, setSessionVersion] = useState(0);
  const [rating, setRating] = useState(5);
  const [ratingJob, setRatingJob] = useState('');
  const [agreement, setAgreement] = useState('');
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
    const listener = () => setLoginRequired(true);
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
        draggable
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
          <button className="text-btn" onClick={() => move(id, 'library')}>
            Remove
          </button>
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
    try {
      await post('/api/ratings', {
        job_id: ratingJob,
        rater: 'Evaluator',
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
    if (completedJobs.length < 2) return null;
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
      <aside>
        <div className="brand">
          <div className="brand-logo">
            <span className="brand-pill">VCS</span>
          </div>
          <div className="brand-title">Voice Comparison Studio</div>
          <div className="brand-subtitle">Unified Neural TTS Evaluation</div>
        </div>

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
                draggable
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
          <div className="drop-hint">Drag engines here to temporarily park them.</div>
        </section>

        <div className="side-title" style={{ marginTop: '24px' }}>
          SPEECH PRESETS
        </div>
        <div className="preset-list">
          {(language === 'English' ? PRESET_SCRIPTS_EN : PRESET_SCRIPTS_DE).map((p, idx) => (
            <button
              key={idx}
              className="preset-btn"
              onClick={() => setText(p.text)}
              title="Click to load preset text"
            >
              <span className="preset-chip">SCRIPT</span> {p.name}
            </button>
          ))}
        </div>

        <p className="sidebar-note">
          Supports Qwen3-TTS, CosyVoice 3, and XTTS-v2 side-by-side with natural speech timing.
        </p>
      </aside>

      <div className="main">
        <nav>
          <div className="nav-tabs">
            {(['Compare', 'Metrics', 'Evaluation'] as const).map((t) => (
              <button
                key={t}
                className={tab === t ? 'selected' : ''}
                onClick={() => setTab(t)}
              >
                {t === 'Compare'
                  ? 'Voice Comparison'
                  : t === 'Metrics'
                  ? 'Benchmark Metrics'
                  : 'Blind Evaluation'}
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
            <span className="app-status">
              <span className="status-dot" aria-hidden="true"></span>
              {models.filter((m) => m.configured).length} / 3 engines active
            </span>
          </div>
        </nav>

        <main>
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
                  await post('/api/session', { token });
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
              <label>
                Access Token
                <input
                  type="password"
                  autoComplete="current-password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                />
              </label>
              <button className="primary" type="submit">
                Connect Session
              </button>
            </form>
          )}

          {tab === 'Compare' && (
            <>
              <div className="heading">
                <div>
                  <h1>Unified Voice Comparison</h1>
                  <p>
                    Compare all active models with natural speech durations and pacing.
                  </p>
                </div>
                <div className="heading-actions">
                  {completedJobs.length > 0 && batchId && (
                    <a
                      className="outline btn-export"
                      href={API + '/api/exports/' + batchId}
                      download={`voice_comparison_${batchId}.zip`}
                      title="Download complete audio bundle and CSV metrics in a ZIP archive"
                    >
                      Export All (.zip)
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
                  maxLength={2500}
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
                    Add models from the left sidebar library to compare.
                  </div>
                )}
              </div>
            </>
          )}

          {tab === 'Metrics' && (
            <>
              <div className="heading">
                <div>
                  <h1>Advanced Evaluation & Benchmark Matrix</h1>
                  <p>
                    Standardized speech metrics including Character Error Rate (CER), Word Error Rate
                    (WER), Real-Time Factor (RTF), Speaker Similarity (ECAPA),
                    ScoreQ MOS, and DNSMOS.
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
                  {/* Model Competition Leaderboard */}
                  {completedJobs.length > 0 && (
                    <div className="competition-banner">
                      <div className="comp-item">
                        <span className="comp-badge">MOS QUALITY</span>
                        <strong>
                          {(() => {
                            const scores = completedJobs.map((j) => ({
                              name: models.find((m) => m.id === j.model_id)?.name,
                              val: getModelMetric(j.id, 'scoreq') || 0,
                            }));
                            scores.sort((a, b) => b.val - a.val);
                            return scores[0]?.val > 0 ? `${scores[0].name} (${scores[0].val.toFixed(2)})` : 'Pending';
                          })()}
                        </strong>
                      </div>
                      <div className="comp-item">
                        <span className="comp-badge">SPEAKER SIMILARITY</span>
                        <strong>
                          {(() => {
                            const scores = completedJobs.map((j) => ({
                              name: models.find((m) => m.id === j.model_id)?.name,
                              val: getModelMetric(j.id, 'ecapa_similarity') || 0,
                            }));
                            scores.sort((a, b) => b.val - a.val);
                            return scores[0]?.val > 0 ? `${scores[0].name} (${scores[0].val.toFixed(3)})` : 'Pending';
                          })()}
                        </strong>
                      </div>
                      <div className="comp-item">
                        <span className="comp-badge">HIGHEST SPEED</span>
                        <strong>
                          {(() => {
                            const scores = completedJobs.map((j) => ({
                              name: models.find((m) => m.id === j.model_id)?.name,
                              val: getModelMetric(j.id, 'rtfx') || 0,
                            }));
                            scores.sort((a, b) => b.val - a.val);
                            return scores[0]?.val > 0 ? `${scores[0].name} (${scores[0].val.toFixed(1)}x RT)` : 'Pending';
                          })()}
                        </strong>
                      </div>
                      <div className="comp-item">
                        <span className="comp-badge">ACCURACY (WER)</span>
                        <strong>
                          {(() => {
                            const scores = completedJobs.map((j) => ({
                              name: models.find((m) => m.id === j.model_id)?.name,
                              val: getModelMetric(j.id, 'wer') ?? 999,
                            }));
                            scores.sort((a, b) => a.val - b.val);
                            return scores[0]?.val < 100 ? `${scores[0].name} (${(scores[0].val * 100).toFixed(1)}%)` : 'Pending';
                          })()}
                        </strong>
                      </div>
                    </div>
                  )}

                  {/* View Selector Tabs */}
                  <div className="metrics-view-tabs">
                    <button
                      className={selectedMetricModel === 'all' ? 'active' : ''}
                      onClick={() => setSelectedMetricModel('all')}
                    >
                      Multi-Model Benchmark Matrix
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

                  {/* View 1: Multi-Model Tabular Format */}
                  {selectedMetricModel === 'all' ? (
                    <div className="panel table-panel">
                      <div className="table-top">
                        <h2>Model Competition Matrix</h2>
                        <span className="table-caption">
                          Higher values better for ScoreQ, Similarity, RTFx. Lower better for CER, WER, RTF.
                        </span>
                      </div>
                      <div className="table-responsive">
                        <table className="benchmark-table">
                          <thead>
                            <tr>
                              <th>Metric Field</th>
                              <th>Target / Health Benchmark</th>
                              {jobs.map((j) => (
                                <th key={j.id}>{models.find((m) => m.id === j.model_id)?.name}</th>
                              ))}
                              <th>Leader</th>
                            </tr>
                          </thead>
                          <tbody>
                            {METRIC_DEFS.map((def) => {
                              const leaderName = getLeader(def);
                              return (
                                <tr key={def.key}>
                                  <td className="metric-title-cell">
                                    <strong>{def.label}</strong>
                                    <span className="metric-cat">{def.category}</span>
                                  </td>
                                  <td className="metric-benchmark-cell">{def.benchmark}</td>
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

                          <div className="table-responsive">
                            <table className="benchmark-table individual-table">
                              <thead>
                                <tr>
                                  <th>Metric Field</th>
                                  <th>Your Value</th>
                                  <th>Target / Health Benchmark</th>
                                  <th>Health Assessment</th>
                                </tr>
                              </thead>
                              <tbody>
                                {METRIC_DEFS.map((def) => {
                                  const val = getModelMetric(selJob?.id, def.key);
                                  const health = val !== null ? def.getHealth(val) : null;
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
                  <h1>Blind Subjective MOS Evaluation</h1>
                  <p>
                    Conduct unbiased blind A/B/X listening tests without model brand bias.
                    Voices are randomized and anonymized to eliminate model preconceptions.
                  </p>
                </div>
              </div>

              {/* Blind A/B/X Evaluation Section */}
              <div className="panel blind-panel">
                <div className="panel-header-row">
                  <div>
                    <h2>Blind Subjective MOS Evaluation</h2>
                    <p>
                      Assess voice naturalness and articulation on a 1–5 scale. Voices are randomized
                      and anonymized to eliminate model preconceptions.
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
                    <p>Generate speech in the Voice Comparison tab first to run a blind listening test.</p>
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
