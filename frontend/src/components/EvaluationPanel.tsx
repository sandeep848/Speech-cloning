import { Evaluation, Job, MetricDef, Model } from '../types';

interface EvaluationPanelProps {
  jobs: Job[];
  models: Model[];
  metrics: Record<string, Evaluation>;
  selectedMetricModel: string;
  setSelectedMetricModel: (m: string) => void;
  metricGroup: string;
  setMetricGroup: (g: string) => void;
  visibleMetrics: MetricDef[];
  getLeader: (def: MetricDef) => string | null;
  getModelMetric: (jobId: string | undefined, key: string) => number | null;
  onSwitchToCompare: () => void;
  onCalculateMetrics: (jobId: string) => void;
}

export function EvaluationPanel({
  jobs,
  models,
  metrics,
  selectedMetricModel,
  setSelectedMetricModel,
  metricGroup,
  setMetricGroup,
  visibleMetrics,
  getLeader,
  getModelMetric,
  onSwitchToCompare,
  onCalculateMetrics,
}: EvaluationPanelProps) {
  return (
    <>
      <div className="heading">
        <div>
          <h1>Compare the details</h1>
          <p>
            Start with the essentials. Explore accuracy, speed, quality, or signal
            diagnostics when you need more detail.
          </p>
        </div>
      </div>

      {!jobs.length ? (
        <div className="panel empty-state">
          <h3>No Voice Generations Yet</h3>
          <p>Switch to the Compare tab and generate speech to calculate the benchmark matrix.</p>
          <button className="primary" onClick={onSwitchToCompare}>
            Go to Compare Voices
          </button>
        </div>
      ) : (
        <div className="metrics-wrapper">
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
            <label>
              Metric focus
              <select
                value={metricGroup}
                onChange={(e) => setMetricGroup(e.target.value)}
              >
                {[
                  'Overview',
                  'Accuracy',
                  'Speed & Latency',
                  'Acoustic Quality',
                  'Signal diagnostics',
                  'All metrics',
                ].map((group) => (
                  <option key={group}>{group}</option>
                ))}
              </select>
            </label>
            <p>
              {visibleMetrics.length} metrics · Same script and settings give the
              fairest comparison.
            </p>
          </div>

          {selectedMetricModel === 'all' ? (
            <div className="panel table-panel">
              <div className="table-top">
                <h2>Model Competition Matrix</h2>
                <span className="table-caption">
                  Automated scores support listening; they do not replace it.
                </span>
              </div>
              <p className="table-scroll-hint">
                Swipe the table to see every model and metric.
              </p>
              <div
                className="table-responsive"
                role="region"
                tabIndex={0}
                aria-label="Model comparison metrics; scroll horizontally to view all columns"
              >
                <table className="benchmark-table">
                  <thead>
                    <tr>
                      <th>Metric Field</th>
                      {jobs.map((j) => (
                        <th key={j.id}>
                          {models.find((m) => m.id === j.model_id)?.name}
                        </th>
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
                                <div className="val-text">{def.format(val)}</div>
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
            (() => {
              const selJob = jobs.find((j) => j.model_id === selectedMetricModel);
              const selModel = models.find((m) => m.id === selectedMetricModel);
              const evalData = selJob ? metrics[selJob.id] : null;

              return (
                <div className="panel table-panel">
                  <div className="table-top">
                    <h2>
                      {selModel?.name || selectedMetricModel} — Detailed Health Report
                    </h2>
                    {selJob?.status === 'complete' &&
                      ['not_evaluated', 'failed', 'interrupted'].includes(
                        evalData?.status || ''
                      ) && (
                        <button
                          className="outline"
                          onClick={() => onCalculateMetrics(selJob.id)}
                        >
                          Calculate Model Metrics
                        </button>
                      )}
                  </div>

                  <p className="table-scroll-hint">
                    Swipe the table to see every metric detail.
                  </p>
                  <div
                    className="table-responsive"
                    role="region"
                    tabIndex={0}
                    aria-label="Detailed model metrics; scroll horizontally to view all columns"
                  >
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
                              <td className="metric-benchmark-cell">
                                {def.benchmark}
                              </td>
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
  );
}
