import React, { useState } from 'react';
import { crossSiteApi, post, setSessionToken } from '../api';

interface AuthModalProps {
  authMode: string;
  onSuccess: () => void;
  onError: (msg: string) => void;
}

export function AuthModal({ authMode, onSuccess, onError }: AuthModalProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const session = await post('/api/session', {
        ...(authMode === 'accounts' ? { username, password } : { token }),
        transport: crossSiteApi ? 'bearer' : 'cookie',
      });
      setSessionToken(session.session_token || '');
      setUsername('');
      setPassword('');
      setToken('');
      onSuccess();
    } catch (error) {
      onError(String(error));
    }
  };

  return (
    <form className="panel login" onSubmit={handleSubmit}>
      <h2>Studio Authorization Required</h2>
      {authMode === 'accounts' ? (
        <>
          <label>
            Username
            <input
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              maxLength={100}
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              maxLength={1000}
              required
            />
          </label>
        </>
      ) : (
        <label>
          Access Token
          <input
            type="password"
            autoComplete="current-password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </label>
      )}
      <button className="primary" type="submit">
        Connect Session
      </button>
    </form>
  );
}
