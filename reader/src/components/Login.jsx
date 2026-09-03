import React, { useState, useEffect } from 'react';
import { login, setupAccount, getAuthStatus } from '../api';
import './Login.css';

function Login({ onAuth }) {
  // 'checking' until the server tells us whether an account exists. Rendering a
  // login form on a fresh instance strands the user: there is nothing to log in
  // to, and no other route creates the first account.
  const [mode, setMode] = useState('checking');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAuthStatus()
      .then(data => {
        if (cancelled) return;
        setMode(data.setup_required ? 'setup' : 'login');
      })
      .catch(() => {
        // Offline or unreachable: fall back to the login form, which reports
        // its own connection error when submitted.
        if (!cancelled) setMode('login');
      });
    return () => { cancelled = true; };
  }, []);

  const isSetup = mode === 'setup';

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!username.trim() || !password.trim()) {
      setError('Username and password are required');
      return;
    }
    if (isSetup && password !== confirmPassword) {
      setError('The two passwords do not match');
      return;
    }

    setLoading(true);
    try {
      if (isSetup) {
        await setupAccount(username.trim(), password);
      } else {
        await login(username.trim(), password);
      }
      onAuth();
    } catch (err) {
      setError(err.message || (isSetup ? 'Could not create the account' : 'Login failed'));
    } finally {
      setLoading(false);
    }
  };

  if (mode === 'checking') {
    return (
      <div className="login-page">
        <div className="login-card">
          <div className="login-brand">
            <span className="material-symbols-outlined login-logo">newsmode</span>
            ReadabilityRSS Reader
          </div>
          <div className="login-title">Connecting…</div>
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <span className="material-symbols-outlined login-logo">newsmode</span>
          ReadabilityRSS Reader
        </div>

        <div className="login-title">
          {isSetup ? 'Create your account' : 'Reader Log In'}
        </div>

        {isSetup && (
          <div className="login-hint">
            This instance has no account yet. The details you choose here become the
            only login, so pick a strong password — until you finish, anyone who can
            reach this address can use it.
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
              <label className="login-label">Confirm password</label>
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
            {loading
              ? (isSetup ? 'Creating account…' : 'Logging in…')
              : (isSetup ? 'Create account' : 'Log In')}
          </button>
        </form>
      </div>
    </div>
  );
}

export default Login;
