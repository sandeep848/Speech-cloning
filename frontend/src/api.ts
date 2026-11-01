import React from 'react';

export const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

let sessionToken = '';
let sessionEpoch = 0;

export const crossSiteApi =
  !!API && new URL(API, window.location.href).origin !== window.location.origin;

export function getSessionToken(): string {
  return sessionToken;
}

export function setSessionToken(token: string): void {
  sessionToken = token;
  sessionEpoch++;
}

export function clearSessionToken(): void {
  sessionToken = '';
  sessionEpoch++;
}

export function sessionHeaders(): Record<string, string> {
  return sessionToken ? { Authorization: 'Session ' + sessionToken } : {};
}

export async function req<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const epoch = sessionEpoch;
  const response = await fetch(API + path, {
    ...options,
    headers: {
      'X-Studio-CSRF': '1',
      ...sessionHeaders(),
      ...options.headers,
    },
    credentials: 'include',
  });
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
  return body as T;
}

export const post = <T = any>(path: string, body: unknown): Promise<T> =>
  req<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

export async function privateBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const epoch = sessionEpoch;
  const response = await fetch(API + path, {
    headers: sessionHeaders(),
    credentials: 'include',
    signal,
  });
  if (epoch !== sessionEpoch) throw new Error('Session changed; previous request discarded');
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('studio-login'));
    throw new Error('Private download failed (' + response.status + ')');
  }
  const blob = await response.blob();
  if (epoch !== sessionEpoch) throw new Error('Session changed');
  return blob;
}

export async function downloadPrivate(
  event: React.MouseEvent<HTMLAnchorElement>,
  path: string,
  filename: string
): Promise<void> {
  if (!sessionToken) return;
  event.preventDefault();
  try {
    const blob = await privateBlob(path);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    window.dispatchEvent(
      new CustomEvent('studio-download-error', { detail: String(error) })
    );
  }
}
