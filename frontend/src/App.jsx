import React, { useState, useCallback, useEffect } from 'react';
import './App.css';
import { parseURL, getAuthStatus, setToken, clearToken, setOnAuthFailure } from './api';
import URLInput from './components/URLInput';
import OriginalHTML from './components/OriginalHTML';
import FieldsPreview from './components/FieldsPreview';
import Dashboard from './components/Dashboard';
import Categories from './components/Categories';
import Options from './components/Options';
import ActivityLog from './components/ActivityLog';
import Login from './components/Login';
import BulkImport from './components/BulkImport';

function viewFromHash(hash) {
  switch (hash) {
    case '#parser':
      return 'parser';
    case '#categories':
      return 'categories';
    case '#options':
      return 'options';
    case '#import':
      return 'import';
    default:
      return 'dashboard';
  }
}

function hashFromView(view) {
  return view === 'dashboard' ? '' : `#${view}`;
}

function App() {
  const SYSTEM_VERSION = "2026-09-08 13:08 UTC";
  // Auth state
  const [authState, setAuthState] = useState('loading'); // 'loading' | 'setup' | 'login' | 'authenticated'

  const [activeView, setActiveView] = useState(() => viewFromHash(window.location.hash));
  const [dashboardKey, setDashboardKey] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);
  const [overrides, setOverrides] = useState({});
  const [iframeElement, setIframeElement] = useState(null);
  const [pickerActive, setPickerActive] = useState(false);

  useEffect(() => {
    setOnAuthFailure(() => {
      setAuthState('login');
    });
  }, []);

  useEffect(() => {
    const handleHashChange = () => {
      setActiveView(viewFromHash(window.location.hash));
    };

    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  // Check auth on mount
  useEffect(() => {
    getAuthStatus()
      .then(data => {
        if (data.setup_required) {
          setAuthState('setup');
        } else if (data.authenticated) {
          setAuthState('authenticated');
        } else {
          setAuthState('login');
        }
      })
      .catch(() => {
        setAuthState('login');
      });
  }, []);

  const handleAuth = useCallback((token) => {
    setToken(token);
    setAuthState('authenticated');
  }, []);

  const handleLogout = useCallback(() => {
    clearToken();
    setAuthState('login');
  }, []);

  const handleParse = useCallback(async (url) => {
    setLoading(true);
    setError(null);
    setResults(null);
    setIframeElement(null);
    setPickerActive(false);

    try {
      const result = await parseURL(url);

      const displayData = {
        title: result.title,
        description: result.description,
        original_html: result.original_html,
        language: result.language,
        pub_date: result.pub_date,
        main_image: result.main_image,
        url: url,
        images: result.images || [],
      };

      setResults(displayData);
    } catch (err) {
      setError(err.message || 'An error occurred while parsing the URL');
    } finally {
      setLoading(false);
    }
  }, []);

  const handleOverride = useCallback(async (newOverrides) => {
    if (!results) return;

    setLoading(true);
    setError(null);
    const updatedOverrides = { ...overrides, ...newOverrides };

    try {
      const result = await parseURL(results.url, updatedOverrides);
      setOverrides(updatedOverrides);
      setResults({
        ...result,
        images: results.images,
      });
    } catch (err) {
      setError(err.message || 'An error occurred while applying overrides');
    } finally {
      setLoading(false);
    }
  }, [results, overrides]);

  const handleIframeRef = useCallback((element) => {
    setIframeElement(element);
  }, []);

  const handlePickerStateChange = useCallback((active) => {
    setPickerActive(active);
  }, []);

  const navTo = (view) => {
    window.location.hash = hashFromView(view);
    setActiveView(view);
    if (view === 'dashboard') setDashboardKey(k => k + 1);
  };

  // Show loading spinner while checking auth
  if (authState === 'loading') {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-page)' }}>
        <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-sans)', fontSize: '13px' }}>Loading…</span>
      </div>
    );
  }

  // Show login/setup page
  if (authState === 'setup' || authState === 'login') {
    return <Login mode={authState} onAuth={handleAuth} />;
  }

  return (
    <>
      <header className="nav">
        <div className="nav-inner">
          <button className="nav-brand" onClick={() => navTo('dashboard')}>
            <span className="material-symbols-outlined nav-logo">newsmode</span>
            ReadabilityRSS
            {window.location.hostname === 'localhost' && <span className="dev-badge">LOCAL</span>}
          </button>
          <nav className="nav-links">
            <button
              onClick={() => navTo('dashboard')}
              className={`nav-button ${activeView === 'dashboard' ? 'active' : ''}`}
            >
              dashboard
            </button>
            <button
              onClick={() => navTo('parser')}
              className={`nav-button ${activeView === 'parser' ? 'active' : ''}`}
            >
              parser
            </button>
            <button
              onClick={() => navTo('categories')}
              className={`nav-button ${activeView === 'categories' ? 'active' : ''}`}
            >
              categories
            </button>
            <button
              onClick={() => navTo('options')}
              className={`nav-button ${activeView === 'options' ? 'active' : ''}`}
            >
              options
            </button>
            <a
              href="/"
              className="nav-button nav-button-reader"
            >
              ← back to reader
            </a>
          </nav>
          <span className="nav-spacer" />
          <span className="nav-version">{SYSTEM_VERSION}</span>
          <button
            className={`nav-log-btn ${drawerOpen ? 'active' : ''}`}
            onClick={() => setDrawerOpen(o => !o)}
          >
            [log]
          </button>
        </div>
      </header>

      <div className="App">
        {activeView === 'dashboard' && (
          <Dashboard key={dashboardKey} />
        )}
        {activeView === 'categories' && <Categories />}
        {activeView === 'options' && <Options onLogout={handleLogout} />}
        {activeView === 'import' && (
          <BulkImport onDone={() => { window.location.hash = ''; navTo('dashboard'); }} />
        )}

        <div style={{ display: activeView === 'parser' ? 'block' : 'none' }}>
          <URLInput onParse={handleParse} loading={loading} />

          {error && (
            <div className="error-message">
              {error}
            </div>
          )}

          {results && (
            <div className="layout">
              <OriginalHTML
                data={results}
                onIframeRef={handleIframeRef}
                highlight={pickerActive}
              />
              <FieldsPreview
                data={results}
                onOverride={handleOverride}
                iframeElement={iframeElement}
                onPickerStateChange={handlePickerStateChange}
                parseOverrides={overrides}
              />
            </div>
          )}
        </div>
      </div>

      {drawerOpen && (
        <div className="drawer-overlay" onClick={() => setDrawerOpen(false)}>
          <div className="drawer" onClick={e => e.stopPropagation()}>
            <div className="drawer-header">
              <span className="drawer-title">ACTIVITY LOG</span>
              <button className="drawer-close" onClick={() => setDrawerOpen(false)}>✕</button>
            </div>
            <div className="drawer-body">
              <ActivityLog />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default App;
