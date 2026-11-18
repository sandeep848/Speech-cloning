export const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

export async function req(path: string, options: RequestInit = {}) {
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

export const post = (path: string, body: unknown) =>
  req(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

