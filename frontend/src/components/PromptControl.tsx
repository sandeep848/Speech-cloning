import React from 'react';
import { API, downloadPrivate } from '../api';
import { Job } from '../types';

interface PromptControlProps {
  text: string;
  setText: (t: string) => void;
  language: string;
  busy: boolean;
  loginRequired: boolean;
  hasReference: boolean;
  eligible: string[];
  completedJobs: Job[];
  batchId: string;
  speechRate: string;
  setSpeechRate: (r: string) => void;
  temperatures: Record<string, string>;
  setTemperatures: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  onGenerate: () => void;
}

export function PromptControl({
  text,
  setText,
  language,
  busy,
  loginRequired,
  hasReference,
  eligible,
  completedJobs,
  batchId,
  speechRate,
  setSpeechRate,
  temperatures,
  setTemperatures,
  onGenerate,
}: PromptControlProps) {
  return (
    <>
      <div className="heading">
        <div>
          <h1>Voice comparison</h1>
          <p>One script. Multiple voices. Listen, compare, and choose.</p>
        </div>
        <div className="heading-actions">
          {completedJobs.length > 0 && batchId && (
            <a
              className="outline btn-export"
              href={API + '/api/exports/' + batchId}
              download={`voice_comparison_${batchId}.zip`}
              onClick={(event) =>
                downloadPrivate(
                  event,
                  '/api/exports/' + batchId,
                  `voice_comparison_${batchId}.zip`
                )
              }
              title="Download complete audio bundle and CSV metrics in a ZIP archive"
            >
              Export All
            </a>
          )}
          <button
            className="primary btn-large"
            onClick={onGenerate}
            disabled={
              busy ||
              loginRequired ||
              !hasReference ||
              !text.trim() ||
              !eligible.length
            }
          >
            {busy ? 'Synthesizing Audio…' : 'Generate Comparison'}
          </button>
        </div>
      </div>

      <details className="generation-settings">
        <summary>
          Generation settings <span>Speech rate &amp; temperature</span>
        </summary>
        <div className="generation-controls">
          <label>
            Speech rate
            <select
              value={speechRate}
              onChange={(e) => setSpeechRate(e.target.value)}
            >
              <option value="">Model default</option>
              <option value="0.8">0.8 ×</option>
              <option value="0.9">0.9 ×</option>
              <option value="1">1.0 ×</option>
              <option value="1.1">1.1 ×</option>
            </select>
          </label>
          {eligible
            .filter((model) => model !== 'cosyvoice3')
            .map((model) => (
              <label key={model}>
                {model === 'qwen3' ? 'Qwen3' : 'XTTS-v2'} temperature
                <input
                  type="number"
                  min="0.1"
                  max="1.5"
                  step="0.05"
                  placeholder="Model default"
                  value={temperatures[model] || ''}
                  onChange={(e) =>
                    setTemperatures((current) => ({
                      ...current,
                      [model]: e.target.value,
                    }))
                  }
                />
              </label>
            ))}
          {eligible.includes('cosyvoice3') && (
            <small>
              CosyVoice uses its native sampling settings; temperature adjustment is
              unavailable.
            </small>
          )}
          <button
            onClick={() => {
              setSpeechRate('');
              setTemperatures({});
            }}
          >
            Reset controls
          </button>
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
    </>
  );
}
