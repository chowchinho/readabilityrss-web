import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import App from './App';
import { isOfflineCachingEnabled, notifyServiceWorkerCachingPreference } from './offlinePreferences';
import { API_URL } from './api';
import { IMAGES_CACHE, FAVICONS_CACHE } from './constants/caches';
import './styles/variables.css';
import './styles/global.css';

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    const offlineCachingEnabled = isOfflineCachingEnabled();

    // Register SW and notify caching preference (existing logic)
    navigator.serviceWorker.register('/service-worker.js')
      .then(() => {
        notifyServiceWorkerCachingPreference(offlineCachingEnabled);

        if (!offlineCachingEnabled && 'caches' in window) {
          caches.delete(IMAGES_CACHE);
          caches.delete(FAVICONS_CACHE);
        }
      })
      .catch(err => {
        console.log('Service worker registration failed: ', err);
      });

    // Use .ready to guarantee an active SW before posting messages or registering sync
    navigator.serviceWorker.ready.then((registration) => {
      if (registration.active) {
        registration.active.postMessage({ type: 'SET_API_URL', url: API_URL });
      }

      if (!offlineCachingEnabled) return;

      if ('periodicSync' in registration) {
        navigator.permissions.query({ name: 'periodic-background-sync' })
          .then((status) => {
            if (status.state === 'granted') {
              return registration.periodicSync.register('reader-periodic-sync', {
                minInterval: 6 * 60 * 60 * 1000,
              });
            }
          })
          .catch(err => console.warn('[SW] periodicSync registration failed:', err));
      }

      if ('sync' in registration) {
        registration.sync.register('reader-sync-retry')
          .catch(err => console.warn('[SW] sync registration failed:', err));
      }
    }).catch(err => console.warn('[SW] serviceWorker.ready failed:', err));
  });
}

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('Reader ErrorBoundary caught error:', error, errorInfo);
  }

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100vh',
          padding: '24px',
          fontFamily: 'system-ui, -apple-system, sans-serif',
          textAlign: 'center',
          backgroundColor: 'var(--bg-primary, #fcfbf9)',
          color: 'var(--text-primary, #1c1510)',
          boxSizing: 'border-box',
        }}>
          <h2 style={{ marginBottom: '12px', fontSize: '1.25rem', fontWeight: 600 }}>
            Something went wrong
          </h2>
          <p style={{ marginBottom: '20px', color: 'var(--text-secondary, #706357)', maxWidth: '400px', fontSize: '0.9rem', lineHeight: 1.5 }}>
            An unexpected error occurred while rendering the reader.
          </p>
          <button
            onClick={this.handleReload}
            style={{
              padding: '10px 20px',
              fontSize: '0.9rem',
              fontWeight: 500,
              color: '#ffffff',
              backgroundColor: 'var(--accent, #c97d2e)',
              border: 'none',
              borderRadius: '6px',
              cursor: 'pointer',
            }}
          >
            Reload Reader
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

window.addEventListener('error', (event) => {
  if (document.getElementById('fatal-error-overlay')) return;
  const el = document.createElement('div');
  el.id = 'fatal-error-overlay';
  el.style = "position:fixed;top:0;left:0;width:100vw;height:100vh;background:black;color:#ff5555;z-index:9999;padding:20px;overflow:auto;font-family:monospace;white-space:pre-wrap;";
  el.textContent = `FATAL RUNTIME CRASH:\n\n${event.message}\n\n${event.error?.stack || 'No stack trace available.'}`;
  document.body.appendChild(el);
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="/:feedSlug" element={<App />} />
          <Route path="/:feedSlug/:articleSlug" element={<App />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  </React.StrictMode>
);
