import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

import {
  API,
  clearSessionToken,
  downloadPrivate,
  post,
  req,
  setSessionToken,
} from './api';
import { AuthModal } from './components/AuthModal';
import { AudioPlayer } from './components/AudioPlayer';
import { ComparisonCard } from './components/ComparisonCard';
import { EvaluationPanel } from './components/EvaluationPanel';
import { PromptControl } from './components/PromptControl';
import { RatingSection } from './components/RatingSection';
import {
  DEFAULTS,
  Evaluation,
  Job,
  METRIC_DEFS,
  MetricDef,
  Model,
  PRESET_SCRIPTS_DE,
  PRESET_SCRIPTS_EN,
  ReferenceInfo,
} from './types';

function savedActive(): string[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem('voice.active') || 'null');
    if (
      Array.isArray(saved) &&
      saved.every((x) => typeof x === 'string' && DEFAULTS.some((m) => m.id === x))
    ) {
      return [...new Set(saved)];
    }
  } catch {
    /* fallback to defaults */
  }
  return DEFAULTS.map((m) => m.id);
}

function App() {
  const [tab, setTab] = useState<'Compare' | 'Metrics' | 'Evaluation'>('Compare');
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [language, setLanguage] = useState<'German' | 'English'>('German');
  const [models, setModels] = useState<Model[]>(DEFAULTS);
  const [active, setActive] = useState<string[]>(savedActive);
  const [reference, setReference] = useState<ReferenceInfo | null>(null);
  const [savedReferences, setSavedReferences] = useState<{ id: string; filename: string }[]>([]);
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
  const [authMode, setAuthMode] = useState('token');
  const [authenticated, setAuthenticated] = useState(false);
  const authenticatedRef = useRef(false);

  useEffect(() => {
    req('/api/session')
      .then((s) => {
        setAuthMode(s.auth_mode || 'token');
        authenticatedRef.current = !!s.authenticated && s.principal !== 'development';
        setAuthenticated(authenticatedRef.current);
      })
      .catch(() => {});
  }, []);

  const [sessionVersion, setSessionVersion] = useState(0);
  const [rating, setRating] = useState(5);
  const [raterName, setRaterName] = useState('');
  const [ratingJob, setRatingJob] = useState('');
  const [agreement, setAgreement] = useState('');
  const [metricGroup, setMetricGroup] = useState('Overview');

  const visibleMetrics = METRIC_DEFS.filter(
    (def) =>
      metricGroup === 'All metrics' ||
      (metricGroup === 'Overview'
        ? ['wer', 'ecapa_similarity', 'scoreq', 'rtfx'].includes(def.key)
        : def.category === metricGroup)
  );

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
      clearSessionToken();
      authenticatedRef.current = false;
      setAuthenticated(false);
      setLoginRequired(true);
      setText('');
      setReference(null);
      setJobs([]);
      setBatchId('');
      setMetrics({});
      setAgreement('');
      setRatingJob('');
      setRaterName('');
      localStorage.removeItem('voice.batch');
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
        if (
          !profile ||
          !('reference' in profile) ||
          !Array.isArray(available) ||
          !available.every(
            (model) =>
              model &&
              typeof model.id === 'string' &&
              typeof model.name === 'string' &&
              typeof model.configured === 'boolean'
          )
        ) {
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
        model_settings: Object.fromEntries(
          eligible
            .filter((model) => model !== 'cosyvoice3' && temperatures[model])
            .map((model) => [model, { temperature: Number(temperatures[model]) }])
        ),
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
    if (!DEFAULTS.some((m) => m.id === id)) return;
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

  async function saveRating() {
    if (!raterName.trim()) {
      setNotice('Enter a reviewer identifier before saving a rating.');
      return;
    }
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
    return bestJob ? models.find((m) => m.id === bestJob!.model_id)?.name || null : null;
  };

  return (
    <div className="app">
      <aside
        className={libraryOpen ? 'library-open' : ''}
        aria-label="Model library and speech presets"
      >
        <div className="brand">
          <div className="brand-copy">
            <div className="brand-logo">
              <span className="brand-pill">VCS</span>
            </div>
            <div className="brand-title">Voice Comparison Studio</div>
            <div className="brand-subtitle">Unified Neural TTS Evaluation</div>
          </div>
          <button
            className="sidebar-toggle"
            type="button"
            aria-expanded={libraryOpen}
            aria-controls="sidebar-content"
            onClick={() => setLibraryOpen((open) => !open)}
          >
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
              <div className="drop-hint all-active">
                All engines in active comparison.
              </div>
            )}
            <div className="drop-hint">
              Tap Add or Remove to select engines. Use arrows to change their order.
            </div>
          </section>

          <div className="side-title" style={{ marginTop: '24px' }}>
            SPEECH PRESETS
          </div>
          <div className="preset-list">
            {(language === 'English' ? PRESET_SCRIPTS_EN : PRESET_SCRIPTS_DE).map(
              (p, idx) => (
                <button
                  key={idx}
                  className="preset-btn"
                  onClick={() => {
                    setText(p.text);
                    setLibraryOpen(false);
                  }}
                  title="Click to load preset text"
                >
                  <span className="preset-chip">SCRIPT</span> {p.name}
                </button>
              )
            )}
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
            {authenticated && (
              <button
                onClick={async () => {
                  try {
                    await req('/api/session', { method: 'DELETE' });
                  } catch {
                    /* ignore */
                  }
                  clearSessionToken();
                  authenticatedRef.current = false;
                  setAuthenticated(false);
                  setLoginRequired(true);
                  setText('');
                  setAgreement('');
                  setRatingJob('');
                  setRaterName('');
                  localStorage.removeItem('voice.batch');
                  setSessionVersion((v) => v + 1);
                  setReference(null);
                  setJobs([]);
                  setBatchId('');
                  setMetrics({});
                  setSavedReferences([]);
                }}
              >
                Sign out
              </button>
            )}
            <span className="app-status">
              <span className="status-dot" aria-hidden="true"></span>
              {models.filter((m) => m.configured).length} / 3 engines active
            </span>
          </div>
        </nav>

        <main>
          {authenticated && (
            <details className="panel data-settings">
              <summary>Manage saved data</summary>
              <p>Delete completed generations before removing references used by them.</p>
              <button
                disabled={!batchId || busy}
                onClick={async () => {
                  try {
                    await req('/api/generations/' + batchId, { method: 'DELETE' });
                    setBatchId('');
                    setJobs([]);
                    setMetrics({});
                    setText('');
                    localStorage.removeItem('voice.batch');
                    setNotice('Generation deleted.');
                  } catch (error) {
                    setNotice(String(error));
                  }
                }}
              >
                Delete current generation
              </button>
              <button
                onClick={async () => {
                  try {
                    setSavedReferences(await req('/api/references'));
                  } catch (error) {
                    setNotice(String(error));
                  }
                }}
              >
                Show uploaded references
              </button>
              {savedReferences.map((item) => (
                <div key={item.id}>
                  <span>
                    {item.filename} ({item.id.slice(0, 8)}){' '}
                  </span>
                  <button
                    onClick={async () => {
                      try {
                        await req('/api/references/' + item.id, { method: 'DELETE' });
                        setSavedReferences((items) => items.filter((x) => x.id !== item.id));
                        if (reference?.id === item.id) {
                          setReference(null);
                          setSessionVersion((v) => v + 1);
                        }
                        setNotice('Reference deleted.');
                      } catch (error) {
                        setNotice(String(error));
                      }
                    }}
                  >
                    Delete reference
                  </button>
                </div>
              ))}
            </details>
          )}

          {tab === 'Evaluation' && (
            <label className="reviewer-name">
              Reviewer identifier
              <input
                value={raterName}
                onChange={(e) => setRaterName(e.target.value)}
                maxLength={100}
                placeholder="Use a different identifier for each reviewer"
              />
            </label>
          )}

          {notice && (
            <div className="notice" role="alert">
              <span>{notice}</span>
              <button onClick={() => setNotice('')}>Dismiss</button>
            </div>
          )}

          {loginRequired && (
            <AuthModal
              authMode={authMode}
              onSuccess={() => {
                authenticatedRef.current = true;
                setAuthenticated(true);
                setLoginRequired(false);
                setNotice('');
                setSessionVersion((v) => v + 1);
              }}
              onError={(msg) => setNotice(msg)}
            />
          )}

          {tab === 'Compare' && (
            <>
              <PromptControl
                text={text}
                setText={setText}
                language={language}
                busy={busy}
                loginRequired={loginRequired}
                hasReference={!!reference}
                eligible={eligible}
                completedJobs={completedJobs}
                batchId={batchId}
                speechRate={speechRate}
                setSpeechRate={setSpeechRate}
                temperatures={temperatures}
                setTemperatures={setTemperatures}
                onGenerate={generate}
              />

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
                        'status-pill ' +
                        (reference ? 'status-ready' : 'status-neutral')
                      }
                    >
                      {reference
                        ? `${language} Reference Active`
                        : `${language} Reference Missing`}
                    </span>
                  </footer>
                </article>

                {active.map((id, index) => {
                  const model = models.find((m) => m.id === id) || {
                    id,
                    name: id,
                    ready: false,
                    configured: false,
                  };
                  const job = jobs.find((j) => j.model_id === id);
                  return (
                    <ComparisonCard
                      key={id}
                      id={id}
                      model={model}
                      job={job}
                      clock={clock}
                      isFirst={index === 0}
                      isLast={index === active.length - 1}
                      onShift={shiftModel}
                      onRemove={(removeId) => move(removeId, 'library')}
                      onReorder={reorder}
                      onDragStart={(dragId) => {
                        dragged.current = dragId;
                      }}
                    />
                  );
                })}
                {!active.length && (
                  <div className="empty-workspace">
                    Open Models &amp; presets to add engines to the comparison.
                  </div>
                )}
              </div>
            </>
          )}

          {tab === 'Metrics' && (
            <EvaluationPanel
              jobs={jobs}
              models={models}
              metrics={metrics}
              selectedMetricModel={selectedMetricModel}
              setSelectedMetricModel={setSelectedMetricModel}
              metricGroup={metricGroup}
              setMetricGroup={setMetricGroup}
              visibleMetrics={visibleMetrics}
              getLeader={getLeader}
              getModelMetric={getModelMetric}
              onSwitchToCompare={() => setTab('Compare')}
              onCalculateMetrics={async (jobId) => {
                try {
                  await post('/api/evaluations/' + jobId, {});
                  setSessionVersion((v) => v + 1);
                } catch (err) {
                  setNotice(String(err));
                }
              }}
            />
          )}

          {tab === 'Evaluation' && (
            <RatingSection
              completedJobs={completedJobs}
              models={models}
              revealed={revealed}
              setRevealed={setRevealed}
              ratingJob={ratingJob}
              setRatingJob={setRatingJob}
              rating={rating}
              setRating={setRating}
              agreement={agreement}
              onSaveRating={saveRating}
              onSwitchToCompare={() => setTab('Compare')}
            />
          )}
        </main>
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
