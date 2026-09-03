import React, { useState, useEffect } from 'react';
import { Capacitor } from '@capacitor/core';
import { getSettings, saveSettings, clearAllData } from '../db';
import { notifyServiceWorkerCachingPreference } from '../offlinePreferences';
import { applyLocalCachePolicy } from '../sync';
import { API_URL , getStoredSortMode, SORT_KEY } from '../api';
import { APP_SHELL_CACHE, IMAGES_CACHE, FAVICONS_CACHE } from '../constants/caches';
import '../styles/settings.css';
import CategoryOrderModal from './CategoryOrderModal';

export default function Settings({ onClose, version, showInstallButton = false, onInstallApp, installPending = false, categories = [], aiEnabled = true }) {
  const [showCategoryModal, setShowCategoryModal] = useState(false);
  const [settings, setSettings] = useState({
    syncInterval: 6,
    retentionDays: 3,
    maxStorageMB: 2000,
    syncOnStartup: localStorage.getItem('reader_sync_on_startup') !== 'false',
    showReadArticles: localStorage.getItem('reader_show_read') === 'true',
    hideEmptySources: localStorage.getItem('reader_hide_empty_sources') === 'true',
    offlineCaching: localStorage.getItem('offlineCaching') === 'true',
    adjacentPrefetch: localStorage.getItem('adjacentPrefetch') !== 'false',
    sortMode: getStoredSortMode()
  });

  const [initialSettings, setInitialSettings] = useState({});
  const [usage, setUsage] = useState(0);
  const [saveState, setSaveState] = useState('idle'); // 'idle' | 'saving' | 'saved'
  const [saveStatusMsg, setSaveStatusMsg] = useState('');
  const [clearState, setClearState] = useState('idle'); // 'idle' | 'confirm' | 'clearing' | 'cleared'

  useEffect(() => {
    getSettings().then(s => {
      const initial = { ...settings, ...s };
      setSettings(initial);
      setInitialSettings(initial);
    });

    if (navigator.storage && navigator.storage.estimate) {
      navigator.storage.estimate().then(estimate => {
        setUsage((estimate.usage / (1024 * 1024)).toFixed(1));
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleChange = (k, v) => {
    setSettings(prev => ({ ...prev, [k]: parseFloat(v) }));
  };

  const handleToggle = (k) => {
    setSettings(prev => ({ ...prev, [k]: !prev[k] }));
  };

  const handleSave = async () => {
    setSaveState('saving');
    setSaveStatusMsg('');
    try {
      const coreSettings = {
        syncInterval: settings.syncInterval,
        retentionDays: settings.retentionDays,
        maxStorageMB: settings.maxStorageMB
      };
      await saveSettings(coreSettings);

      localStorage.setItem('reader_sync_on_startup', String(settings.syncOnStartup));
      localStorage.setItem('reader_show_read', String(settings.showReadArticles));
      localStorage.setItem('reader_hide_empty_sources', String(settings.hideEmptySources));
      localStorage.setItem('adjacentPrefetch', String(settings.adjacentPrefetch));
      localStorage.setItem(SORT_KEY, settings.sortMode);

      // Handle offline caching toggle — no reload, apply immediately
      const cachingChanged = settings.offlineCaching !== (localStorage.getItem('offlineCaching') === 'true');
      if (cachingChanged) {
        localStorage.setItem('offlineCaching', String(settings.offlineCaching));
        if (!settings.offlineCaching) {
          // Switch to live mode immediately; finish clearing local data in the background.
          setSaveStatusMsg('Clearing cached data...');

          notifyServiceWorkerCachingPreference(false).catch(err => {
            console.warn('Failed to notify service worker while disabling caching', err);
          });

          Promise.resolve().then(async () => {
            try {
              await clearAllData();
              if (typeof caches !== 'undefined') {
                await Promise.allSettled([
                  caches.delete(IMAGES_CACHE),
                  caches.delete(FAVICONS_CACHE),
                  caches.delete(APP_SHELL_CACHE),
                ]);
              }
              setUsage(0);
            } catch (err) {
              console.warn('Background cache clear failed', err);
            } finally {
              setSaveStatusMsg('');
            }
          });

          setSaveState('saved');
          setTimeout(() => {
            setSaveState('idle');
            onClose();
          }, 1500);
          return;
        }

        await notifyServiceWorkerCachingPreference(true);
      }

      // Show contextual status message during policy enforcement
      const retentionDecreased = settings.retentionDays < initialSettings.retentionDays;
      const storageDecreased = settings.maxStorageMB < initialSettings.maxStorageMB;

      if (retentionDecreased) {
        setSaveStatusMsg(
          `Removing articles from day ${settings.retentionDays + 1} to day ${initialSettings.retentionDays}...`
        );
      } else if (storageDecreased) {
        const fmt = mb => mb >= 1000 ? `${(mb / 1000).toFixed(0)} GB` : `${mb} MB`;
        setSaveStatusMsg(`Reducing storage from ${fmt(initialSettings.maxStorageMB)} to ${fmt(settings.maxStorageMB)}...`);
      }

      await applyLocalCachePolicy(coreSettings);
      setSaveStatusMsg('');

      setSaveState('saved');
      // Articles already in state were fetched in the old order; the sort is applied
      // server-side, so a reload is what actually reorders the feed.
      const sortChanged = settings.sortMode !== initialSettings.sortMode;
      setTimeout(() => {
        setSaveState('idle');
        onClose();
        if (sortChanged) window.location.reload();
      }, 1500);
    } catch (err) {
      console.error('Save failed', err);
      setSaveState('idle');
      setSaveStatusMsg('');
    }
  };

  const handleClearConfirm = async () => {
    setClearState('clearing');
    await clearAllData();
    if (typeof caches !== 'undefined') {
      await Promise.all([
        caches.delete(IMAGES_CACHE),
        caches.delete(FAVICONS_CACHE),
        caches.delete(APP_SHELL_CACHE),
      ]);
    }
    setUsage(0);
    setClearState('cleared');
    setTimeout(() => setClearState('idle'), 2000);
  };

  const saveLabel = saveState === 'saving' ? 'Saving...' : saveState === 'saved' ? '✓ Saved' : 'SAVE CHANGES';

  return (
    <div className="settings-container">
      <div className="settings-header">
        <h2>Settings</h2>
        <button onClick={onClose}><span className="material-symbols-outlined">close</span></button>
      </div>

      {!Capacitor.isNativePlatform() && (
        <div className="settings-section settings-manage-section">
          <a href="/manage/" className="settings-manage-link">
            <span className="material-symbols-outlined">rss_feed</span>
            Feed Management
          </a>
        </div>
      )}

      {/* ── Display ── */}
      <div className="settings-section">
        <h3>Feed order</h3>
        {[
          ['smart', 'Personalised', 'Ranks by how well an article matches your topic, region and type preferences, and spreads sources so no single feed dominates the page. Nothing is ever hidden.'],
          ['latest', 'Latest first', 'Straight chronological order, newest at the top.'],
          ['random', 'Random shuffle', 'Shuffles the page. Useful for rediscovering older articles.'],
        ].map(([value, label, help]) => {
          const isDisabled = value === 'smart' && !aiEnabled;
          return (
            <label
              key={value}
              className={`settings-option${isDisabled ? ' is-disabled' : ''}`}
              style={{ marginTop: value === 'smart' ? 0 : 12 }}
            >
              <input
                type="radio"
                name="sortMode"
                value={value}
                disabled={isDisabled}
                checked={settings.sortMode === value}
                onChange={() => setSettings(s => ({ ...s, sortMode: value }))}
              />
              <span>
                {label}
                <span className="settings-option-help">
                  {isDisabled
                    ? 'Enable AI features in the management options to use this. The feed is showing Latest first meanwhile.'
                    : help}
                </span>
              </span>
            </label>
          );
        })}
      </div>

      <div className="settings-section">
        <h3>Display</h3>
        <label className="settings-option">
          <input
            type="checkbox"
            checked={settings.showReadArticles}
            onChange={() => handleToggle('showReadArticles')}
          />
          <span>
            Show read articles in feed
            <span className="settings-option-help">Display already-read articles in pane 2.</span>
          </span>
        </label>
        <label className="settings-option" style={{ marginTop: 12 }}>
          <input
            type="checkbox"
            checked={settings.hideEmptySources}
            onChange={() => handleToggle('hideEmptySources')}
          />
          <span>
            Hide sources with no unread
            <span className="settings-option-help">Sources with zero unread articles are hidden from the sidebar.</span>
          </span>
        </label>

        <div style={{ marginTop: 16 }}>
          <button
            className="btn-primary"
            onClick={() => setShowCategoryModal(true)}
            style={{ width: '100%', cursor: 'pointer', backgroundColor: 'var(--bg-active, #f0f0f0)', color: 'var(--text-main, #333)', border: '1px solid var(--border-color, #ddd)' }}
          >
            Edit Categories Order
          </button>
          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-muted)' }}>
            Rearrange the order of feed categories in the sidebar.
          </div>
        </div>

        {showInstallButton && (
          <div style={{ marginTop: 16 }}>
            <button
              className="btn-primary"
              onClick={onInstallApp}
              disabled={installPending}
              style={{ width: '100%', cursor: installPending ? 'default' : 'pointer' }}
            >
              {installPending ? 'Opening install prompt...' : 'Install App'}
            </button>
            <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-muted)' }}>
              Install ReadabilityRSS on this phone for a cleaner full-screen app experience.
            </div>
          </div>
        )}
      </div>

      {/* ── Offline Reading (Experimental) ── */}
      <div className="settings-section">
        <h3>Offline Reading (Experimental)</h3>
        <label style={{ flexDirection: 'row', alignItems: 'center', gap: 12, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={settings.offlineCaching}
            onChange={() => handleToggle('offlineCaching')}
            style={{ width: 18, height: 18, cursor: 'pointer' }}
          />
          <span>
            Enable offline caching (Experimental)
            <span style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
              {settings.offlineCaching
                ? 'On — articles are cached for offline reading.'
                : 'Off — live mode, always fetches fresh data from server.'}
            </span>
          </span>
        </label>

        {settings.offlineCaching && (
          <>
            {/* Sync Schedule */}
            <div className="settings-subgroup">
              <div className="settings-subheading">Sync Schedule</div>
              <label>
                Background Sync Interval
                <select value={settings.syncInterval} onChange={e => handleChange('syncInterval', e.target.value)}>
                  <option value="1">Every 1 Hour</option>
                  <option value="2">Every 2 Hours</option>
                  <option value="4">Every 4 Hours</option>
                  <option value="6">Every 6 Hours</option>
                  <option value="12">Every 12 Hours</option>
                  <option value="0">Manual Only</option>
                </select>
              </label>
              <div style={{ marginTop: -4, marginBottom: 12, fontSize: 12, color: 'var(--text-muted)' }}>
                Also performs a fixed daily sync at 7:00 a.m. local time.
              </div>
              <label style={{ flexDirection: 'row', alignItems: 'center', gap: 12, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={settings.syncOnStartup}
                  onChange={() => handleToggle('syncOnStartup')}
                  style={{ width: 18, height: 18, cursor: 'pointer' }}
                />
                <span>
                  Sync on app start
                  <span style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                    Runs a sync when the PWA opens, if the device is online.
                  </span>
                </span>
              </label>
            </div>

            {/* Pre-fetch */}
            <div className="settings-subgroup">
              <label style={{ flexDirection: 'row', alignItems: 'center', gap: 12, cursor: 'pointer', margin: 0 }}>
                <input
                  type="checkbox"
                  checked={settings.adjacentPrefetch}
                  onChange={() => handleToggle('adjacentPrefetch')}
                  style={{ width: 18, height: 18, cursor: 'pointer' }}
                />
                <span>
                  Pre-fetch adjacent articles
                  <span style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                    Loads nearby articles in the background when you open one, for faster navigation.
                  </span>
                </span>
              </label>
            </div>

            {/* Storage & Retention */}
            <div className="settings-subgroup">
              <div className="settings-subheading">Storage &amp; Retention</div>
              <label>
                Keep articles for
                <select value={settings.retentionDays} onChange={e => handleChange('retentionDays', e.target.value)}>
                  <option value="1">1 Day</option>
                  <option value="3">3 Days</option>
                  <option value="7">7 Days</option>
                  <option value="14">14 Days</option>
                  <option value="30">30 Days</option>
                </select>
              </label>

              <label>
                Max offline storage
                <select value={settings.maxStorageMB} onChange={e => handleChange('maxStorageMB', e.target.value)}>
                  <option value="50">50 MB</option>
                  <option value="200">200 MB</option>
                  <option value="500">500 MB</option>
                  <option value="1000">1 GB</option>
                  <option value="2000">2 GB</option>
                  <option value="999999">Unlimited</option>
                </select>
              </label>

              <div className="usage-meter">
                Estimated usage: {usage} MB
              </div>
            </div>
          </>
        )}

        {/* Outside the toggle: cached data must stay clearable after caching is turned off. */}
        <div style={{ marginTop: 10 }}>
          {clearState === 'idle' && (
            <button
              onClick={() => setClearState('confirm')}
              className="btn-danger"
              style={{ cursor: 'pointer' }}
            >
              Clear All Cached Data
            </button>
          )}
          {clearState === 'confirm' && (
            <div className="clear-confirm">
              <span className="clear-confirm-warning">⚠ Delete all offline data?</span>
              <div className="clear-confirm-actions">
                <button className="btn-danger-sm" onClick={handleClearConfirm}>Yes, Clear</button>
                <button className="btn-cancel-sm" onClick={() => setClearState('idle')}>Cancel</button>
              </div>
            </div>
          )}
          {clearState === 'clearing' && (
            <button className="btn-danger" disabled style={{ cursor: 'default', opacity: 0.7 }}>
              Clearing...
            </button>
          )}
          {clearState === 'cleared' && (
            <button className="btn-success" disabled style={{ cursor: 'default' }}>
              ✓ Cleared
            </button>
          )}
        </div>
      </div>

      {/* ── Footer ── */}
      <div className="settings-footer">
        <button
          className={saveState === 'saved' ? 'btn-success' : 'btn-primary'}
          onClick={saveState === 'idle' ? handleSave : undefined}
          disabled={saveState !== 'idle'}
          style={{ cursor: saveState === 'idle' ? 'pointer' : 'default' }}
        >
          {saveLabel}
        </button>
        {saveState === 'saving' && saveStatusMsg && (
          <p className="save-status-msg">{saveStatusMsg}</p>
        )}
      </div>

      <div style={{ marginTop: '20px', padding: '20px', textAlign: 'center', fontSize: '11px', color: 'var(--text-muted)', opacity: 0.6 }}>
        SYSTEM_VERSION: {version || 'Unknown'}
        <br />
        BACKEND: {API_URL || window.location.origin}
        <br />
        Built with{' '}
        <a href="https://claude.ai" target="_blank" rel="noopener noreferrer" style={{ color: 'inherit', textDecoration: 'underline' }}>Claude</a>,{' '}
        <a href="https://gemini.google.com" target="_blank" rel="noopener noreferrer" style={{ color: 'inherit', textDecoration: 'underline' }}>Google Gemini</a> &{' '}
        <a href="https://chatgpt.com" target="_blank" rel="noopener noreferrer" style={{ color: 'inherit', textDecoration: 'underline' }}>ChatGPT</a>
      </div>
      {showCategoryModal && (
        <CategoryOrderModal
          categories={categories}
          onClose={() => setShowCategoryModal(false)}
        />
      )}
    </div>
  );
}
