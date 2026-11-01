import React from 'react';
import { API, downloadPrivate } from '../api';
import { Job, Model } from '../types';
import { AudioPlayer } from './AudioPlayer';

interface ComparisonCardProps {
  id: string;
  model: Model;
  job?: Job;
  clock: number;
  isFirst: boolean;
  isLast: boolean;
  onShift: (id: string, delta: number) => void;
  onRemove: (id: string) => void;
  onReorder: (id: string) => void;
  onDragStart: (id: string) => void;
}

export function ComparisonCard({
  id,
  model,
  job,
  clock,
  isFirst,
  isLast,
  onShift,
  onRemove,
  onReorder,
  onDragStart,
}: ComparisonCardProps) {
  return (
    <article
      className="voice-card"
      key={id}
      draggable={window.matchMedia('(pointer: fine)').matches}
      onDragStart={() => onDragStart(id)}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.stopPropagation();
        onReorder(id);
      }}
    >
      <header>
        <span className="handle" aria-hidden="true">
          ⋮⋮
        </span>
        <strong>{model.name}</strong>
        <div className="card-actions">
          <button
            className="order-btn"
            aria-label={`Move ${model.name} earlier`}
            disabled={isFirst}
            onClick={() => onShift(id, -1)}
          >
            ↑
          </button>
          <button
            className="order-btn"
            aria-label={`Move ${model.name} later`}
            disabled={isLast}
            onClick={() => onShift(id, 1)}
          >
            ↓
          </button>
          <button
            className="text-btn"
            aria-label={`Remove ${model.name}`}
            onClick={() => onRemove(id)}
          >
            Remove
          </button>
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
              ? model.ready ? 'Ready to generate' : 'Model will load on first generation'
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
              ? model.ready ? 'status-ready' : 'status-neutral'
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
            ? model.ready ? 'Loaded' : model.state === 'loading' ? 'Loading' : 'Available'
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
            onClick={(event) =>
              downloadPrivate(
                event,
                job.audio_url!,
                `${model.name.replace(/\s+/g, '_')}.wav`
              )
            }
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
