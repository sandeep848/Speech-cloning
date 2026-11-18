import React from 'react';
import { API } from '../api/client';

export function AudioPlayer({ path, title }: { path: string; title?: string }) {
  return (
    <div className="audio-player-box">
      {title && <span className="audio-player-label">{title}</span>}
      <audio controls preload="metadata" crossOrigin="use-credentials" src={API + path} />
    </div>
  );
}

