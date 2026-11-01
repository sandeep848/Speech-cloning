import React from 'react';
import { Job, Model } from '../types';
import { AudioPlayer } from './AudioPlayer';

interface RatingSectionProps {
  completedJobs: Job[];
  models: Model[];
  revealed: boolean;
  setRevealed: React.Dispatch<React.SetStateAction<boolean>>;
  ratingJob: string;
  setRatingJob: (j: string) => void;
  rating: number;
  setRating: (r: number) => void;
  agreement: string;
  onSaveRating: () => void;
  onSwitchToCompare: () => void;
}

export function RatingSection({
  completedJobs,
  models,
  revealed,
  setRevealed,
  ratingJob,
  setRatingJob,
  rating,
  setRating,
  agreement,
  onSaveRating,
  onSwitchToCompare,
}: RatingSectionProps) {
  return (
    <div className="evaluation-view">
      <div className="heading">
        <div>
          <h1>Subjective Listening Ratings</h1>
          <p>
            Compare recordings and rate voice quality for internal review. Model names
            can be hidden; this view is not a standardized blind A/B/X study.
          </p>
        </div>
      </div>

      <div className="panel blind-panel">
        <div className="panel-header-row">
          <div>
            <h2>Subjective Listening Ratings</h2>
            <p>
              Assess voice naturalness and articulation on a 1–5 scale. Names can be
              hidden, while the clip order follows the current comparison.
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
            <p>
              Generate speech in the Voice Comparison tab first to submit listening
              ratings.
            </p>
            <button className="primary" onClick={onSwitchToCompare}>
              Go to Compare Voices
            </button>
          </div>
        ) : (
          <>
            <div className="blind-grid">
              {completedJobs.map((j, idx) => {
                const letters = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon'];
                const alias = `Voice ${letters[idx] || idx + 1}`;
                const realName =
                  models.find((m) => m.id === j.model_id)?.name || j.model_id;
                return (
                  <div className="blind-voice-card" key={j.id}>
                    <div className="blind-card-header">
                      <span className="blind-tag">{alias}</span>
                      {revealed && (
                        <strong className="revealed-name">{realName}</strong>
                      )}
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
                    const realName =
                      models.find((m) => m.id === j.model_id)?.name || j.model_id;
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
                onClick={onSaveRating}
              >
                Submit Score
              </button>
            </div>

            {agreement && <div className="agreement-box">{agreement}</div>}
          </>
        )}
      </div>
    </div>
  );
}
