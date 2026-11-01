import React, { useEffect, useState } from 'react';
import { API, getSessionToken, privateBlob } from '../api';

export function AudioPlayer({ path, title }: { path: string; title?: string }) {
  const [source, setSource] = useState(getSessionToken() ? '' : API + path);

  useEffect(() => {
    if (!getSessionToken()) {
      setSource(API + path);
      return;
    }
    const abort = new AbortController();
    let objectUrl = '';
    setSource('');
    privateBlob(path, abort.signal)
      .then((blob) => {
        if (abort.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch((error) => {
        if (!abort.signal.aborted) {
          window.dispatchEvent(
            new CustomEvent('studio-download-error', { detail: String(error) })
          );
        }
      });
    return () => {
      abort.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);

  return (
    <div className="audio-player-box">
      {title && <span className="audio-player-label">{title}</span>}
      <audio
        controls
        preload="metadata"
        crossOrigin="use-credentials"
        src={source || undefined}
      />
    </div>
  );
}
