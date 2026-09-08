import React, { useState } from 'react';
import './Login.css';
import { API_URL } from '../api';

// NOTE: If reCAPTCHA is ever enabled, frontend client script (e.g. in index.html)
// and backend RECAPTCHA_SECRET_KEY must be configured together.
function Login({ mode, onAuth }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const isSetup = mode === 'setup';

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!username.trim() || !password.trim()) {
      setError('Username and password are required');
      return;
    }

    if (isSetup && password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    setLoading(true);
    try {
      const endpoint = isSetup ? `${API_URL}/auth/setup` : `${API_URL}/auth/login`;
      let resp;
      try {
        resp = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: username.trim(),
            password,
            recaptcha_token: '',
          }),
        });
      } catch {
        throw new Error(`Unable to reach login server at ${API_URL}`);
      }

      if (!resp.ok) {
        const data = await resp.json();
        throw new Error(data.detail || 'Authentication failed');
      }

      const data = await resp.json();
      onAuth(data.token);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <img className="login-logo" src={`${process.env.PUBLIC_URL}/favicon-192.png`} alt="" />
          ReadabilityRSS
        </div>

        <div className="login-title">
          {isSetup ? 'Create Account' : 'Log In'}
        </div>

        {isSetup && (
          <div className="login-subtitle">
            Set up your username and password to get started.
          </div>
        )}

        {error && (
          <div className="login-error">{error}</div>
        )}

        <form onSubmit={handleSubmit} className="login-form">
          <label className="login-label">Username</label>
          <input
            type="text"
            value={username}
            onChange={e => setUsername(e.target.value)}
            className="login-input"
            autoComplete="username"
            autoFocus
          />

          <label className="login-label">Password</label>
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            className="login-input"
            autoComplete={isSetup ? 'new-password' : 'current-password'}
          />

          {isSetup && (
            <>
              <label className="login-label">Confirm Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                className="login-input"
                autoComplete="new-password"
              />
            </>
          )}

          <button type="submit" className="login-btn" disabled={loading}>
            {loading ? (isSetup ? 'Creating…' : 'Logging in…') : (isSetup ? 'Create Account' : 'Log In')}
          </button>
        </form>
      </div>
    </div>
  );
}

export default Login;
