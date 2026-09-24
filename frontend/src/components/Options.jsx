import React, { useState, useEffect, useCallback } from 'react';
import {
  getFeverAuth,
  setFeverAuth,
  deleteFeverAuth,
  getFeverEndpoint,
  getAuthStatus,
  updateCredentials,
  logout,
  getSystemSettings,
  updateSystemSettings,
  getStorageStats,
  getLabelWeights,
  getTranslationUsageSummary,
  getGlossary,
  saveGlossaryOverride,
  deleteGlossaryOverride,
  previewGlossary
} from '../api';
import TabIcon from './TabIcon';
import TranslationStatus from './TranslationStatus';
import './Options.css';

const ARTICLE_STOPS = [
  { value: 10, label: '10' },
  { value: 20, label: '20' },
  { value: 30, label: '30' },
  { value: 50, label: '50' },
  { value: 100, label: '100' },
  { value: 200, label: '200' },
];

const REFRESH_STOPS = [
  { value: 0.5, label: '30m' },
  { value: 1.0, label: '1h' },
  { value: 2.0, label: '2h' },
  { value: 4.0, label: '4h' },
  { value: 6.0, label: '6h' },
  { value: 12.0, label: '12h' },
  { value: 24.0, label: '24h' },
];

const IMAGE_DIMENSION_STOPS = [
  { value: 800, label: '800px' },
  { value: 1000, label: '1000px' },
  { value: 1200, label: '1200px' },
];

const JPEG_QUALITY_STOPS = [
  { value: 50, label: '50' },
  { value: 60, label: '60' },
  { value: 70, label: '70' },
  { value: 80, label: '80' },
  { value: 85, label: '85' },
];

// Mean encoded size relative to the 1200px/q70 baseline, measured by
// re-encoding a 271-image sample of the live cache. Regenerate with
// scripts/measure_image_sizes.py if the cache composition changes a lot.
const SIZE_MULTIPLIERS = {
  800: { 50: 0.533, 60: 0.593, 70: 0.681, 80: 0.820, 85: 0.925 },
  1000: { 50: 0.679, 60: 0.753, 70: 0.858, 80: 1.025, 85: 1.145 },
  1200: { 50: 0.863, 60: 0.924, 70: 1.000, 80: 1.122, 85: 1.177 },
};

function formatBytes(bytes) {
  if (!bytes || bytes < 1024) return `${Math.round(bytes || 0)} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[i]}`;
}

const TARGET_LANGUAGES = [
  { code: 'zh-TW', name: 'Traditional Chinese (繁體中文)' },
  { code: 'zh-CN', name: 'Simplified Chinese (简体中文)' },
  { code: 'en', name: 'English' },
  { code: 'es', name: 'Spanish (Español)' },
  { code: 'fr', name: 'French (Français)' },
  { code: 'de', name: 'German (Deutsch)' },
  { code: 'ja', name: 'Japanese (日本語)' },
  { code: 'ko', name: 'Korean (한국어)' },
  { code: 'pt', name: 'Portuguese (Português)' },
  { code: 'ru', name: 'Russian (Русский)' },
  { code: 'ar', name: 'Arabic (العربية)' },
  { code: 'hi', name: 'Hindi (हिन्दी)' },
];

function PresetStopSlider({ label, desc, value, stops, badgeFormatter, onChange }) {
  const currentIndex = Math.max(0, stops.findIndex(s => s.value === value));

  return (
    <div className="options-slider-group">
      <div className="options-slider-header">
        <div className="options-slider-header-info">
          <div className="options-row-label">{label}</div>
          <div className="options-row-desc">{desc}</div>
        </div>
        <div className="options-slider-badge">{badgeFormatter(value)}</div>
      </div>
      <div className="options-slider-container">
        <input
          type="range"
          min="0"
          max={stops.length - 1}
          step="1"
          value={currentIndex}
          onChange={e => onChange(stops[parseInt(e.target.value, 10)].value)}
          className="options-range-input"
          style={{
            background: `linear-gradient(to right, var(--accent) 0%, var(--accent) ${(currentIndex / (stops.length - 1)) * 100}%, var(--border) ${(currentIndex / (stops.length - 1)) * 100}%, var(--border) 100%)`
          }}
        />
        <div className="options-slider-stops">
          {stops.map((stop, idx) => (
            <span
              key={stop.value}
              className={`options-slider-stop-label ${idx === currentIndex ? 'active' : ''}`}
              onClick={() => onChange(stop.value)}
            >
              {stop.label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

const AXIS_TITLES = {
  topic: 'Topics',
  type: 'Types',
  region: 'Regions',
  secondary: 'Specific labels',
};

function LabelWeights() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getLabelWeights().then(setData).catch((e) => setError(String(e)));
  }, []);

  if (error) return <div className="options-error">Could not load label weights: {error}</div>;
  if (!data) return <div className="options-loading">Loading label weights…</div>;

  return (
    <div className="label-weights" style={{ marginTop: '16px' }}>
      {Object.entries(AXIS_TITLES).map(([axis, title]) => {
        const rows = (data.axes && data.axes[axis]) || [];
        if (!rows.length) return null;
        return (
          <div key={axis} className="label-weights-axis">
            <h4>{title}</h4>
            <table>
              <thead>
                <tr>
                  <th>Label</th>
                  <th>Declared</th>
                  <th>Votes</th>
                  <th>From votes</th>
                  <th>From behaviour</th>
                  <th>Effective</th>
                  <th>Seen</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.label}
                    className={[r.contested ? 'is-contested' : '',
                                r.unseen ? 'is-unseen' : ''].filter(Boolean).join(' ')}
                  >
                    <td>{r.label}</td>
                    <td>{r.declared.toFixed(1)}</td>
                    <td>{r.votes}</td>
                    <td>{r.explicit >= 0 ? '+' : ''}{r.explicit.toFixed(2)}</td>
                    <td>{r.behavioural >= 0 ? '+' : ''}{r.behavioural.toFixed(2)}</td>
                    <td><strong>{r.effective.toFixed(2)}</strong></td>
                    <td
                      title={axis === 'topic'
                        ? `Times shown in the last ${data.impression_window_days ?? 7} days`
                        : 'Impressions are recorded against the topic only'}
                    >
                      {axis === 'topic' ? (r.impressions ?? 0) : ''}
                    </td>
                    <td>
                      {r.contested && (
                        <span title="Many votes that keep cancelling out — this label is probably too broad and wants splitting">
                          contested
                        </span>
                      )}
                      {r.unseen && (
                        <span title="Never shown in this window. Every label keeps a guaranteed share of the page, so this should not happen — the exploration floor is not working, and you cannot vote on what you are never shown.">
                          never shown
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

function Options({ onLogout }) {
  const [activeTab, setActiveTab] = useState('feed');
  const [opmlCopied, setOpmlCopied] = useState(false);

  // Translation usage tab
  const [usage, setUsage] = useState(null);
  const [usageDays, setUsageDays] = useState(30);
  const [usageError, setUsageError] = useState('');

  // Glossary tab
  const [glossary, setGlossary] = useState(null);
  const [glossaryFilter, setGlossaryFilter] = useState('');
  const [newTw, setNewTw] = useState('');
  const [newHk, setNewHk] = useState('');
  const [glossaryError, setGlossaryError] = useState('');
  const [glossaryBusy, setGlossaryBusy] = useState(false);
  const [sampleText, setSampleText] = useState('這款軟體的品質不錯，網路連線也很快，錢包放得下。');
  const [sampleResult, setSampleResult] = useState(null);

  // Security state
  const [authUsername, setAuthUsername] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [securitySaving, setSecuritySaving] = useState(false);
  const [securityError, setSecurityError] = useState('');
  const [securitySuccess, setSecuritySuccess] = useState('');

  // Fever state
  const [feverEnabled, setFeverEnabled] = useState(false);
  const [feverUsername, setFeverUsername] = useState('');
  const [feverSavedUsername, setFeverSavedUsername] = useState('');
  const [feverPassword, setFeverPassword] = useState('');
  const [feverEndpoint, setFeverEndpoint] = useState('');
  const [feverLoading, setFeverLoading] = useState(true);
  const [feverSaving, setFeverSaving] = useState(false);

  // System settings state
  const [maxArticles, setMaxArticles] = useState(50);
  const [refreshInterval, setRefreshInterval] = useState(1.0);
  const [targetLanguage, setTargetLanguage] = useState('zh-TW');
  const [defaultTranslator, setDefaultTranslator] = useState('qwen');
  const [aiEnabled, setAiEnabled] = useState(true);
  const [deepseekEnabled, setDeepseekEnabled] = useState(false);
  const [deepseekApiKey, setDeepseekApiKey] = useState('');
  const [qwenEnabled, setQwenEnabled] = useState(false);
  const [qwenApiKey, setQwenApiKey] = useState('');
  const [deeplEnabled, setDeeplEnabled] = useState(false);
  const [deeplApiKey, setDeeplApiKey] = useState('');
  const [flaresolverrEnabled, setFlaresolverrEnabled] = useState(false);
  const [flaresolverrUrl, setFlaresolverrUrl] = useState('http://localhost:8191/v1');
  const [imageMaxDimension, setImageMaxDimension] = useState(1200);
  const [imageJpegQuality, setImageJpegQuality] = useState(70);
  const [stats, setStats] = useState(null);
  const [statsError, setStatsError] = useState(false);

  const [settingsLoading, setSettingsLoading] = useState(true);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsSuccess, setSettingsSuccess] = useState('');
  const [settingsError, setSettingsError] = useState('');
  const [feverToggling, setFeverToggling] = useState(false);

  useEffect(() => {
    getAuthStatus().then(data => {
      setAuthUsername(data.username || '');
      setNewUsername(data.username || '');
    }).catch(() => {});

    getSystemSettings()
      .then(data => {
        if (data.max_articles_per_feed) setMaxArticles(data.max_articles_per_feed);
        if (data.feed_refresh_interval_hours) setRefreshInterval(data.feed_refresh_interval_hours);
        if (data.target_language) setTargetLanguage(data.target_language);
        if (data.default_translator) setDefaultTranslator(data.default_translator);
        if (data.ai_enabled !== undefined) setAiEnabled(data.ai_enabled);
        if (data.deepseek_enabled !== undefined) setDeepseekEnabled(data.deepseek_enabled);
        if (data.deepseek_api_key !== undefined) setDeepseekApiKey(data.deepseek_api_key);
        if (data.qwen_enabled !== undefined) setQwenEnabled(data.qwen_enabled);
        if (data.qwen_api_key !== undefined) setQwenApiKey(data.qwen_api_key);
        if (data.deepl_enabled !== undefined) setDeeplEnabled(data.deepl_enabled);
        if (data.deepl_api_key !== undefined) setDeeplApiKey(data.deepl_api_key);
        if (data.flaresolverr_enabled !== undefined) setFlaresolverrEnabled(data.flaresolverr_enabled);
        if (data.flaresolverr_url) setFlaresolverrUrl(data.flaresolverr_url);
        if (data.image_max_dimension) setImageMaxDimension(data.image_max_dimension);
        if (data.image_jpeg_quality) setImageJpegQuality(data.image_jpeg_quality);
      })
      .catch(e => {
        console.error('Failed to load system settings', e);
        setSettingsError('Failed to load system settings');
      })
      .finally(() => setSettingsLoading(false));

    getStorageStats()
      .then(setStats)
      .catch(e => {
        console.error('Failed to load storage stats', e);
        setStatsError(true);
      });
  }, []);

  const fetchFeverStatus = useCallback(async () => {
    try {
      const [auth, endpoint] = await Promise.all([getFeverAuth(), getFeverEndpoint()]);
      setFeverEnabled(auth.enabled);
      setFeverSavedUsername(auth.username || '');
      if (auth.enabled) setFeverUsername(auth.username || '');
      setFeverEndpoint(endpoint.url || '');
    } catch (e) {
      console.error('Failed to fetch Fever status', e);
    } finally {
      setFeverLoading(false);
    }
  }, []);

  useEffect(() => { fetchFeverStatus(); }, [fetchFeverStatus]);

  const handleCopyOPML = () => {
    const backendBase = window.location.origin;
    const opmlUrl = `${backendBase}/feed/opml`;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(opmlUrl).then(() => {
        setOpmlCopied(true);
        setTimeout(() => setOpmlCopied(false), 2000);
      }).catch((err) => {
        console.warn('Failed to copy OPML URL:', err);
      });
    }
  };

  const handleCredentialsSave = async () => {
    setSecurityError('');
    setSecuritySuccess('');

    if (!currentPassword.trim()) {
      setSecurityError('Current password is required');
      return;
    }
    if (!newUsername.trim() || !newPassword.trim()) {
      setSecurityError('New username and password are required');
      return;
    }
    if (newPassword !== confirmPassword) {
      setSecurityError('New passwords do not match');
      return;
    }

    setSecuritySaving(true);
    try {
      await updateCredentials(currentPassword, newUsername.trim(), newPassword);
      setAuthUsername(newUsername.trim());
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setSecuritySuccess('Credentials updated');
      setTimeout(() => setSecuritySuccess(''), 3000);
    } catch (e) {
      setSecurityError(e.message);
    } finally {
      setSecuritySaving(false);
    }
  };

  const handleSettingsSave = async () => {
    setSettingsSaving(true);
    setSettingsError('');
    setSettingsSuccess('');
    try {
      await updateSystemSettings({
        max_articles_per_feed: maxArticles,
        feed_refresh_interval_hours: refreshInterval,
        target_language: targetLanguage,
        default_translator: defaultTranslator,
        deepseek_enabled: deepseekEnabled,
        deepseek_api_key: deepseekApiKey,
        qwen_enabled: qwenEnabled,
        qwen_api_key: qwenApiKey,
        deepl_enabled: deeplEnabled,
        deepl_api_key: deeplApiKey,
        flaresolverr_enabled: flaresolverrEnabled,
        flaresolverr_url: flaresolverrUrl,
        image_max_dimension: imageMaxDimension,
        image_jpeg_quality: imageJpegQuality,
        ai_enabled: aiEnabled,
      });
      setSettingsSuccess('Settings saved successfully');
      setTimeout(() => setSettingsSuccess(''), 3000);
    } catch (e) {
      setSettingsError(e.message);
    } finally {
      setSettingsSaving(false);
    }
  };

  // Projected steady-state image cache for the selected settings. Animated GIFs
  // are stored raw, so only the re-encoded share responds to dimension/quality.
  const projection = (() => {
    if (!stats || !stats.enabled_sources) return null;
    const slots = stats.enabled_sources * maxArticles;
    const multiplier = (SIZE_MULTIPLIERS[imageMaxDimension] || {})[imageJpegQuality] ?? 1;
    const encoded = slots * (stats.encoded_per_article || 0) * (stats.avg_encoded_bytes || 0) * multiplier;
    const gif = slots * (stats.gifs_per_article || 0) * (stats.avg_gif_bytes || 0);
    return { bytes: encoded + gif, gifBytes: gif, slots };
  })();

  const handleLogout = async () => {
    try {
      await logout();
    } catch (e) {
      // Ignore — clearToken already handled in api.js
    }
    onLogout();
  };

  const handleFeverToggle = async () => {
    if (feverToggling) return;
    setFeverToggling(true);
    try {
      if (feverEnabled) {
        await deleteFeverAuth();
        setFeverEnabled(false);
        setFeverSavedUsername('');
        setFeverUsername('');
        setFeverPassword('');
      } else {
        setFeverEnabled(true);
      }
    } catch (e) {
      console.error('Failed to toggle Fever auth', e);
    } finally {
      setFeverToggling(false);
    }
  };

  const handleFeverSave = async () => {
    if (!feverUsername.trim() || !feverPassword.trim()) return;
    setFeverSaving(true);
    try {
      await setFeverAuth(feverUsername.trim(), feverPassword.trim());
      setFeverSavedUsername(feverUsername.trim());
      setFeverPassword('');
    } catch (e) {
      console.error('Failed to save Fever credentials', e);
    } finally {
      setFeverSaving(false);
    }
  };

  const loadUsage = useCallback(async (days) => {
    setUsageError('');
    try {
      setUsage(await getTranslationUsageSummary(days));
    } catch (e) {
      setUsageError(e.message || 'Could not load usage');
    }
  }, []);

  const loadGlossary = useCallback(async () => {
    setGlossaryError('');
    try {
      setGlossary(await getGlossary());
    } catch (e) {
      setGlossaryError(e.message || 'Could not load the glossary');
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'usage') loadUsage(usageDays);
    if (activeTab === 'glossary') loadGlossary();
  }, [activeTab, usageDays, loadUsage, loadGlossary]);

  const addOverride = async (tw, hk, enabled = true) => {
    setGlossaryBusy(true);
    setGlossaryError('');
    try {
      await saveGlossaryOverride({ tw, hk, enabled });
      setNewTw('');
      setNewHk('');
      await loadGlossary();
    } catch (e) {
      setGlossaryError(e.message || 'Could not save');
    } finally {
      setGlossaryBusy(false);
    }
  };

  const removeOverride = async (tw) => {
    setGlossaryBusy(true);
    try {
      await deleteGlossaryOverride(tw);
      await loadGlossary();
    } catch (e) {
      setGlossaryError(e.message || 'Could not remove');
    } finally {
      setGlossaryBusy(false);
    }
  };

  const runPreview = async () => {
    try {
      setSampleResult(await previewGlossary(sampleText));
    } catch (e) {
      setGlossaryError(e.message || 'Could not run the sample');
    }
  };

  return (
    <div className="options-container">
      <h2>Options</h2>

      {/* Tab Navigation */}
      <div className="options-nav-tabs">
        <button
          className={`options-tab-btn ${activeTab === 'feed' ? 'active' : ''}`}
          onClick={() => setActiveTab('feed')}
        >
          <TabIcon name="feed" />Feed &amp; Refresh
        </button>
        <button
          className={`options-tab-btn ${activeTab === 'translation' ? 'active' : ''}`}
          onClick={() => setActiveTab('translation')}
        >
          <TabIcon name="translation" />Translation
        </button>
        <button
          className={`options-tab-btn ${activeTab === 'ai' ? 'active' : ''}`}
          onClick={() => setActiveTab('ai')}
        >
          <TabIcon name="ai" />AI
        </button>
        <button
          className={`options-tab-btn ${activeTab === 'usage' ? 'active' : ''}`}
          onClick={() => setActiveTab('usage')}
        >
          <TabIcon name="usage" />API Usage
        </button>
        <button
          className={`options-tab-btn ${activeTab === 'glossary' ? 'active' : ''}`}
          onClick={() => setActiveTab('glossary')}
        >
          <TabIcon name="glossary" />HK Glossary
        </button>
        <button
          className={`options-tab-btn ${activeTab === 'integrations' ? 'active' : ''}`}
          onClick={() => setActiveTab('integrations')}
        >
          <TabIcon name="integrations" />Integrations
        </button>
        <button
          className={`options-tab-btn ${activeTab === 'security' ? 'active' : ''}`}
          onClick={() => setActiveTab('security')}
        >
          <TabIcon name="account" />Account &amp; Export
        </button>
      </div>

      {settingsError && <div className="options-inline-error">{settingsError}</div>}
      {settingsSuccess && <div className="options-inline-success">{settingsSuccess}</div>}

      {/* ── TAB 1: Feed & Refresh ── */}
      {activeTab === 'feed' && (
        <div className="options-tab-panel">
          <div className="options-section">
            <div className="options-section-title">Current Usage</div>
            {stats ? (
              <div className="options-stat-row">
                <div className="options-stat">
                  <div className="options-stat-value">{stats.enabled_sources}</div>
                  <div className="options-stat-label">
                    Active feeds
                    {stats.total_sources !== stats.enabled_sources &&
                      ` (${stats.total_sources} total)`}
                  </div>
                </div>
                <div className="options-stat">
                  <div className="options-stat-value">{stats.total_articles.toLocaleString()}</div>
                  <div className="options-stat-label">Stored articles</div>
                </div>
                <div className="options-stat">
                  <div className="options-stat-value">{formatBytes(stats.image_cache_bytes)}</div>
                  <div className="options-stat-label">
                    Image cache ({stats.image_cache_files.toLocaleString()} files)
                  </div>
                </div>
                <div className="options-stat">
                  <div className="options-stat-value">{formatBytes(stats.database_bytes)}</div>
                  <div className="options-stat-label">Database</div>
                </div>
              </div>
            ) : statsError ? (
              <div className="options-row-desc" style={{ color: '#c0392b' }}>Failed to load storage stats.</div>
            ) : (
              <div className="options-row-desc">Measuring storage…</div>
            )}
          </div>

          <div className="options-section">
            <div className="options-section-title">Feed Limits &amp; Scheduling</div>

            {!settingsLoading && (
              <>
                <PresetStopSlider
                  label="Max Articles per Feed"
                  desc="Articles stored per source, and the number served in each RSS feed."
                  value={maxArticles}
                  stops={ARTICLE_STOPS}
                  badgeFormatter={val => `${val} Articles`}
                  onChange={setMaxArticles}
                />

                <PresetStopSlider
                  label="Background Refresh Interval"
                  desc="Frequency of automated background feed updates performed by the scheduler."
                  value={refreshInterval}
                  stops={REFRESH_STOPS}
                  badgeFormatter={val => val < 1 ? `${Math.round(val * 60)} Mins` : `Every ${val} Hour${val > 1 ? 's' : ''}`}
                  onChange={setRefreshInterval}
                />

                <div className="options-section-title" style={{ marginTop: '24px' }}>
                  Cached Image Quality
                </div>

                <PresetStopSlider
                  label="Max Image Dimension"
                  desc="Longest edge of cached images. Larger images are scaled down; smaller ones are left alone."
                  value={imageMaxDimension}
                  stops={IMAGE_DIMENSION_STOPS}
                  badgeFormatter={val => `${val} px`}
                  onChange={setImageMaxDimension}
                />

                <PresetStopSlider
                  label="JPEG Quality"
                  desc="Lower saves space. Raising this above 70 will not recover detail already lost in images cached earlier — it only makes new files bigger."
                  value={imageJpegQuality}
                  stops={JPEG_QUALITY_STOPS}
                  badgeFormatter={val => `Quality ${val}`}
                  onChange={setImageJpegQuality}
                />

                {projection && (
                  <div className="options-projection">
                    <div className="options-projection-main">
                      Projected image cache: <strong>{formatBytes(projection.bytes)}</strong>
                      <span className="options-projection-vs">
                        now {formatBytes(stats.image_cache_bytes)}
                      </span>
                    </div>
                    <div className="options-row-desc">
                      At steady state — {stats.enabled_sources} active feeds × {maxArticles} articles
                      = {projection.slots.toLocaleString()} articles. Existing cached images keep
                      their current size until their articles are pruned and re-fetched.
                    </div>
                    {projection.gifBytes > 0 && (
                      <div className="options-row-desc">
                        Includes ~{formatBytes(projection.gifBytes)} of animated GIFs, which are
                        stored as-is and are unaffected by the settings above.
                      </div>
                    )}
                  </div>
                )}

                <div style={{ marginTop: '16px' }}>
                  <button
                    onClick={handleSettingsSave}
                    className="btn"
                    disabled={settingsSaving}
                  >
                    {settingsSaving ? 'Saving…' : 'Save Settings'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ── TAB: AI ── */}
      {activeTab === 'ai' && (
        <div className="options-tab-panel">
          <div className="options-section">
            {!settingsLoading && (
              <div className="options-card-group" style={{ marginBottom: '24px' }}>
                <div className="options-section-header">
                  <div>
                    <div className="options-row-label">AI Features</div>
                    <div className="options-row-desc">
                      Article tagging, personalised feed order, and the thumbs up/down feedback
                      controls in the reader. Translation is not affected by this switch.
                    </div>
                  </div>
                  <button
                    className={`options-toggle ${aiEnabled ? 'active' : ''}`}
                    onClick={() => setAiEnabled(v => !v)}
                    role="switch"
                    aria-checked={aiEnabled}
                  >
                    <span className="options-toggle-track">
                      <span className="options-toggle-thumb" />
                    </span>
                  </button>
                </div>

                {!aiEnabled ? (
                  <div className="options-conditional-field">
                    <div className="options-row-desc">
                      Tagging is paused, the reader is served newest-first, and the feedback and
                      "why this is here" controls are hidden. Existing tags are kept — turning
                      this back on resumes where it left off.
                    </div>
                  </div>
                ) : (
                  <LabelWeights />
                )}
              </div>
            )}

            {!settingsLoading && (
              <button onClick={handleSettingsSave} className="btn" disabled={settingsSaving}>
                {settingsSaving ? 'Saving…' : 'Save Settings'}
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── TAB: Translation ── */}
      {activeTab === 'translation' && (
        <div className="options-tab-panel options-tab-panel--wide">
          <TranslationStatus />

          <div className="options-section">
            <div className="options-section-title">Translation &amp; Target Language</div>

            {!settingsLoading && (
              <>
                <div className="options-row options-row-vertical">
                  <div className="options-row-label">Translation Target Language</div>
                  <div className="options-row-desc">
                    Default target language applied when article translation is enabled on feed sources.
                  </div>
                  <select
                    value={targetLanguage}
                    onChange={e => setTargetLanguage(e.target.value)}
                    className="options-input"
                    style={{ maxWidth: '320px', marginTop: '6px', cursor: 'pointer' }}
                  >
                    {TARGET_LANGUAGES.map(lang => (
                      <option key={lang.code} value={lang.code}>
                        {lang.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="options-row options-row-vertical" style={{ marginTop: '16px' }}>
                  <div className="options-row-label">Default Translation Provider</div>
                  <div className="options-row-desc">
                    Used for on-demand article translation in the reader, and as the fallback for feeds without a specific translator.
                  </div>
                  <select
                    value={defaultTranslator}
                    onChange={e => setDefaultTranslator(e.target.value)}
                    className="options-input"
                    style={{ maxWidth: '320px', marginTop: '6px', cursor: 'pointer' }}
                  >
                    <option value="qwen">Qwen-MT-flash</option>
                    <option value="google">Google Translate</option>
                    <option value="deepl">DeepL</option>
                  </select>
                </div>

                {/* Qwen-MT Translation */}
                <div className="options-card-group" style={{ marginTop: '20px' }}>
                  <div className="options-section-header">
                    <div>
                      <div className="options-row-label">Qwen-MT-flash Translation</div>
                      <div className="options-row-desc">
                        Default translator. Purpose-built machine translation, batched per article,
                        with a Hong Kong vocabulary pass applied to the output.
                      </div>
                    </div>
                    <button
                      className={`options-toggle ${qwenEnabled ? 'active' : ''}`}
                      onClick={() => setQwenEnabled(v => !v)}
                      role="switch"
                      aria-checked={qwenEnabled}
                    >
                      <span className="options-toggle-track">
                        <span className="options-toggle-thumb" />
                      </span>
                    </button>
                  </div>

                  {qwenEnabled && (
                    <div className="options-conditional-field">
                      <div className="options-row-label">Qwen API Key</div>
                      <input
                        type="password"
                        placeholder="sk-..."
                        value={qwenApiKey}
                        onChange={e => setQwenApiKey(e.target.value)}
                        className="options-input"
                        autoComplete="off"
                        style={{ marginTop: '6px' }}
                      />
                    </div>
                  )}
                </div>

                {/* DeepSeek Translation */}
                <div className="options-card-group" style={{ marginTop: '20px' }}>
                  <div className="options-section-header">
                    <div>
                      <div className="options-row-label">DeepSeek Translation (LLM)</div>
                      <div className="options-row-desc">
                        Use DeepSeek AI for natural, high-quality article translation with HTML preservation.
                      </div>
                    </div>
                    <button
                      className={`options-toggle ${deepseekEnabled ? 'active' : ''}`}
                      onClick={() => setDeepseekEnabled(v => !v)}
                      role="switch"
                      aria-checked={deepseekEnabled}
                    >
                      <span className="options-toggle-track">
                        <span className="options-toggle-thumb" />
                      </span>
                    </button>
                  </div>

                  {deepseekEnabled && (
                    <div className="options-conditional-field">
                      <div className="options-row-label">DeepSeek API Key</div>
                      <input
                        type="password"
                        placeholder="sk-..."
                        value={deepseekApiKey}
                        onChange={e => setDeepseekApiKey(e.target.value)}
                        className="options-input"
                        autoComplete="off"
                        style={{ marginTop: '6px' }}
                      />
                    </div>
                  )}
                </div>

                {/* DeepL Translation */}
                <div className="options-card-group" style={{ marginTop: '16px' }}>
                  <div className="options-section-header">
                    <div>
                      <div className="options-row-label">DeepL Translation API</div>
                      <div className="options-row-desc">
                        Use official DeepL API key for high-accuracy translation.
                      </div>
                    </div>
                    <button
                      className={`options-toggle ${deeplEnabled ? 'active' : ''}`}
                      onClick={() => setDeeplEnabled(v => !v)}
                      role="switch"
                      aria-checked={deeplEnabled}
                    >
                      <span className="options-toggle-track">
                        <span className="options-toggle-thumb" />
                      </span>
                    </button>
                  </div>

                  {deeplEnabled && (
                    <div className="options-conditional-field">
                      <div className="options-row-label">DeepL API Key</div>
                      <input
                        type="password"
                        placeholder="DeepL Authentication Key"
                        value={deeplApiKey}
                        onChange={e => setDeeplApiKey(e.target.value)}
                        className="options-input"
                        autoComplete="off"
                        style={{ marginTop: '6px' }}
                      />
                    </div>
                  )}
                </div>

                <div style={{ marginTop: '20px' }}>
                  <button
                    onClick={handleSettingsSave}
                    className="btn"
                    disabled={settingsSaving}
                  >
                    {settingsSaving ? 'Saving…' : 'Save Settings'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ── TAB 3: Integrations ── */}
      {/* TAB: API Usage */}
      {activeTab === 'usage' && (
        <div className="options-tab-panel options-tab-panel--wide">
          <div className="options-panel-head">
            <div>
              <div className="options-row-label">Model API usage</div>
              <div className="options-row-desc">
                Every paid model call, split by the work it did. DeepSeek bills in CNY;
                Qwen-MT-flash bills in USD ($0.16 / $0.49 per 1M in / out) and is converted
                at 7.1 CNY so both sit in one column.
              </div>
            </div>
            <select
              className="options-input glossary-input"
              value={usageDays}
              onChange={e => setUsageDays(Number(e.target.value))}
            >
              <option value={7}>Last 7 days</option>
              <option value={14}>Last 14 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
          </div>

          {usageError && <div className="options-inline-error">{usageError}</div>}
          {!usage && !usageError && <div className="options-row-desc">Loading...</div>}

          {usage && (
            <>
              <div className="usage-total-row">
                <span className="usage-total-label">Total</span>
                <span className="usage-total-value">
                  {'\u00A5'}{usage.totals.reduce((a, t) => a + t.cost_cny, 0).toFixed(2)}
                </span>
                <span className="usage-total-split">
                  scheduled {'\u00A5'}
                  {usage.totals.filter(t => (t.kind || 'translation') === 'translation')
                    .reduce((a, t) => a + t.cost_cny, 0).toFixed(2)}
                  {' \u00B7 '}on-demand {'\u00A5'}
                  {usage.totals.filter(t => t.kind === 'ondemand')
                    .reduce((a, t) => a + t.cost_cny, 0).toFixed(2)}
                  {' \u00B7 '}tagging {'\u00A5'}
                  {usage.totals.filter(t => t.kind === 'tagging')
                    .reduce((a, t) => a + t.cost_cny, 0).toFixed(2)}
                </span>
              </div>

              {usage.totals.length === 0 && (
                <div className="options-row-desc">No model calls in this window yet.</div>
              )}

              {['translation', 'ondemand', 'tagging'].map(kind => {
                const rows = usage.totals.filter(t => (t.kind || 'translation') === kind);
                if (rows.length === 0) return null;
                const unit = kind === 'tagging' ? 'batches' : 'articles';
                const heading =
                  kind === 'tagging'
                    ? 'Tagging'
                    : kind === 'ondemand'
                    ? 'On-demand Translation'
                    : 'Scheduled Translation';
                return (
                  <div className="usage-section" key={kind}>
                    <div className="options-row-label">
                      {heading}
                    </div>
                    <div className="usage-cards">
                      {rows.map(t => (
                        <div className="usage-card" key={t.provider + t.kind}>
                          <div className="usage-card-name">
                            {t.provider === 'qwen' ? 'Qwen-MT-flash' : 'DeepSeek'}
                          </div>
                          <div className="usage-card-cost">
                            {'\u00A5'}{t.cost_cny.toFixed(2)}
                          </div>
                          <div className="usage-card-meta">
                            {t.articles.toLocaleString()} {unit}
                            {' \u00B7 '}{(t.input_tokens / 1000).toFixed(0)}k in
                            {' \u00B7 '}{(t.output_tokens / 1000).toFixed(0)}k out
                          </div>
                          <div className="usage-card-meta">
                            {'\u00A5'}{(t.cost_cny / Math.max(t.articles, 1)).toFixed(4)} per {unit.replace(/e?s$/, '')}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}

              {usage.daily.length > 0 && (
                <div className="usage-section">
                  <div className="usage-legend-row">
                    <span className="options-row-label">Daily cost</span>
                    <span className="usage-legend">
                      <span className="usage-swatch" /> translation
                      <span className="usage-swatch usage-swatch--tagging" /> tagging
                    </span>
                  </div>
                  <div className="usage-bars">
                    {(() => {
                      const byDay = {};
                      usage.daily.forEach(d => {
                        const kind = d.kind || 'translation';
                        byDay[d.day] = byDay[d.day]
                          || { day: d.day, cost: 0, translation: 0, tagging: 0 };
                        byDay[d.day].cost += d.cost_cny;
                        byDay[d.day][kind] += d.cost_cny;
                      });
                      const days = Object.values(byDay);
                      const max = Math.max(...days.map(d => d.cost), 0.0001);
                      return days.map(d => (
                        <div
                          className="usage-bar-row"
                          key={d.day}
                          title={'translation \u00A5' + d.translation.toFixed(3)
                            + ' \u00B7 tagging \u00A5' + d.tagging.toFixed(3)}
                        >
                          <span className="usage-bar-day">{d.day.slice(5)}</span>
                          <span className="usage-bar-track">
                            <span
                              className="usage-bar-fill"
                              style={{ width: Math.max((d.translation / max) * 100, 0) + '%' }}
                            />
                            <span
                              className="usage-bar-fill usage-bar-fill--tagging"
                              style={{ width: Math.max((d.tagging / max) * 100, 0) + '%' }}
                            />
                          </span>
                          <span className="usage-bar-value">{'\u00A5'}{d.cost.toFixed(3)}</span>
                        </div>
                      ));
                    })()}
                  </div>
                </div>
              )}

              {usage.by_source.length > 0 && (
                <div className="usage-section">
                  <div className="options-row-label">By feed and work</div>
                  <table className="usage-table">
                    <thead>
                      <tr>
                        <th>Feed</th><th>Work</th><th>Provider</th>
                        <th className="num">Calls</th><th className="num">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {usage.by_source.map((r, i) => (
                        <tr key={r.source + '-' + r.provider + '-' + r.kind + '-' + i}>
                          <td>{r.source || (r.kind === 'tagging' ? 'all feeds' : '(deleted feed)')}</td>
                          <td className="sub">{r.kind === 'tagging' ? 'tagging' : 'translation'}</td>
                          <td>{r.provider === 'qwen' ? 'Qwen-MT-flash' : 'DeepSeek'}</td>
                          <td className="num">{r.articles.toLocaleString()}</td>
                          <td className="num">{'\u00A5'}{r.cost_cny.toFixed(3)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* TAB: HK Glossary */}
      {activeTab === 'glossary' && (
        <div className="options-tab-panel options-tab-panel--wide">
          <div className="options-panel-head">
            <div>
              <div className="options-row-label">Hong Kong vocabulary pass</div>
              <div className="options-row-desc">
                Applied to every Qwen-MT translation after it comes back. Your overrides beat
                the built-in table; disable a built-in with its Disable button.
              </div>
            </div>
            {glossary && (
              <span className="glossary-stat">
                {glossary.stats.active} active
                {' \u00B7 '}{glossary.stats.builtin} built-in
                {' \u00B7 '}{glossary.stats.overrides} overrides
              </span>
            )}
          </div>

          {glossaryError && <div className="options-inline-error">{glossaryError}</div>}

          <div className="glossary-add">
            <input
              className="options-input glossary-input"
              placeholder="Taiwan term"
              value={newTw}
              onChange={e => setNewTw(e.target.value)}
              maxLength={20}
            />
            <span className="glossary-arrow">{'\u2192'}</span>
            <input
              className="options-input glossary-input"
              placeholder="Hong Kong term"
              value={newHk}
              onChange={e => setNewHk(e.target.value)}
              maxLength={20}
            />
            <button
              className="options-btn"
              disabled={glossaryBusy || !newTw.trim() || !newHk.trim()}
              onClick={() => addOverride(newTw.trim(), newHk.trim(), true)}
            >
              Add override
            </button>
          </div>

          <div className="glossary-sample">
            <div className="options-row-label">Try it</div>
            <textarea
              className="options-input glossary-textarea"
              rows={2}
              value={sampleText}
              onChange={e => setSampleText(e.target.value)}
            />
            <button className="options-btn" onClick={runPreview}>Run the pass</button>
            {sampleResult && <div className="glossary-sample-out">{sampleResult.after}</div>}
          </div>

          {glossary && glossary.overrides.length > 0 && (
            <div className="usage-section">
              <div className="options-row-label">Your overrides</div>
              <table className="usage-table">
                <thead>
                  <tr><th>From</th><th>To</th><th>State</th><th></th></tr>
                </thead>
                <tbody>
                  {glossary.overrides.map(o => (
                    <tr key={o.tw}>
                      <td className="cjk">{o.tw}</td>
                      <td className="cjk">{o.hk || <em>(disabled)</em>}</td>
                      <td>{o.enabled ? 'active' : 'built-in off'}</td>
                      <td className="num">
                        <button
                          className="options-btn options-btn-quiet"
                          disabled={glossaryBusy}
                          onClick={() => removeOverride(o.tw)}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {glossary && (
            <div className="usage-section">
              <div className="options-panel-head">
                <div className="options-row-label">Built-in terms</div>
                <input
                  className="options-input glossary-input"
                  placeholder="Filter"
                  value={glossaryFilter}
                  onChange={e => setGlossaryFilter(e.target.value)}
                />
              </div>
              <table className="usage-table">
                <thead>
                  <tr><th>From</th><th>To</th><th>Category</th><th>Note</th><th></th></tr>
                </thead>
                <tbody>
                  {glossary.builtin
                    .filter(t => !glossaryFilter
                      || t.tw.includes(glossaryFilter)
                      || t.hk.includes(glossaryFilter)
                      || t.category.toLowerCase().includes(glossaryFilter.toLowerCase()))
                    .map(t => {
                      const override = glossary.overrides.find(o => o.tw === t.tw);
                      const off = override && !override.enabled;
                      return (
                        <tr key={t.tw} className={off ? 'glossary-off' : ''}>
                          <td className="cjk">{t.tw}</td>
                          <td className="cjk">
                            {override && override.enabled ? override.hk : t.hk}
                          </td>
                          <td className="sub">{t.category}</td>
                          <td className="sub">
                            {t.note || (t.count ? t.count + ' in corpus' : '')}
                          </td>
                          <td className="num">
                            <button
                              className="options-btn options-btn-quiet"
                              disabled={glossaryBusy}
                              onClick={() => (off ? removeOverride(t.tw) : addOverride(t.tw, '', false))}
                            >
                              {off ? 'Re-enable' : 'Disable'}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === 'integrations' && (
        <div className="options-tab-panel">
          {/* FlareSolverr */}
          <div className="options-section">
            <div className="options-section-title">Anti-Bot &amp; Render Bypass</div>

            {!settingsLoading && (
              <div className="options-card-group">
                <div className="options-section-header">
                  <div>
                    <div className="options-row-label">FlareSolverr Integration</div>
                    <div className="options-row-desc">
                      Bypass Cloudflare anti-bot challenges and render JS-heavy websites.
                    </div>
                  </div>
                  <button
                    className={`options-toggle ${flaresolverrEnabled ? 'active' : ''}`}
                    onClick={() => setFlaresolverrEnabled(v => !v)}
                    role="switch"
                    aria-checked={flaresolverrEnabled}
                  >
                    <span className="options-toggle-track">
                      <span className="options-toggle-thumb" />
                    </span>
                  </button>
                </div>

                {flaresolverrEnabled && (
                  <div className="options-conditional-field">
                    <div className="options-row-label">Server Address:Port</div>
                    <input
                      type="text"
                      placeholder="http://localhost:8191/v1"
                      value={flaresolverrUrl}
                      onChange={e => setFlaresolverrUrl(e.target.value)}
                      className="options-input"
                      autoComplete="off"
                      style={{ marginTop: '6px' }}
                    />
                  </div>
                )}
              </div>
            )}

            {!settingsLoading && (
              <div style={{ marginTop: '16px' }}>
                <button
                  onClick={handleSettingsSave}
                  className="btn"
                  disabled={settingsSaving}
                >
                  {settingsSaving ? 'Saving…' : 'Save Settings'}
                </button>
              </div>
            )}
          </div>

          {/* Fever API */}
          <div className="options-section">
            <div className="options-section-header">
              <div className="options-section-title">Fever API</div>
              {!feverLoading && (
                <button
                  className={`options-toggle ${feverEnabled ? 'active' : ''}`}
                  onClick={handleFeverToggle}
                  disabled={feverToggling}
                  role="switch"
                  aria-checked={feverEnabled}
                >
                  <span className="options-toggle-track">
                    <span className="options-toggle-thumb" />
                  </span>
                </button>
              )}
            </div>

            {!feverEnabled && !feverLoading && (
              <div className="options-row-desc" style={{ paddingTop: '4px' }}>
                Enable Fever API to sync with RSS reader apps like FocusReader, Reeder, or Unread.
              </div>
            )}

            {feverEnabled && !feverLoading && (
              <div className="options-fever-details">
                <div className="options-row">
                  <div className="options-row-info">
                    <div className="options-row-label">Status</div>
                  </div>
                  <div className="options-fever-status">
                    {feverSavedUsername ? (
                      <>
                        <span className="status-dot active" />
                        <span>Active — {feverSavedUsername}</span>
                      </>
                    ) : (
                      <>
                        <span className="status-dot pending" />
                        <span>Credentials not set</span>
                      </>
                    )}
                  </div>
                </div>

                <div className="options-row options-row-vertical">
                  <div className="options-row-label">Credentials</div>
                  <div className="options-fever-form">
                    <input
                      type="text"
                      placeholder="Username"
                      value={feverUsername}
                      onChange={e => setFeverUsername(e.target.value)}
                      className="options-input"
                      autoComplete="off"
                    />
                    <input
                      type="password"
                      placeholder={feverSavedUsername ? 'New password' : 'Password'}
                      value={feverPassword}
                      onChange={e => setFeverPassword(e.target.value)}
                      className="options-input"
                      autoComplete="new-password"
                    />
                    <button
                      onClick={handleFeverSave}
                      className="btn"
                      disabled={feverSaving || !feverUsername.trim() || !feverPassword.trim()}
                    >
                      {feverSaving ? 'Saving…' : 'Save'}
                    </button>
                  </div>
                </div>

                <div className="options-row options-row-vertical">
                  <div className="options-row-label">Endpoint URL</div>
                  <div className="options-row-desc options-endpoint-url">{feverEndpoint}</div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── TAB 4: Account & Security ── */}
      {activeTab === 'security' && (
        <div className="options-tab-panel">
          {/* Security */}
          <div className="options-section">
            <div className="options-section-title">Security</div>

            <div className="options-row">
              <div className="options-row-info">
                <div className="options-row-label">Logged in as</div>
                <div className="options-row-desc">{authUsername}</div>
              </div>
              <button onClick={handleLogout} className="btn">Log Out</button>
            </div>

            <div className="options-row options-row-vertical">
              <div className="options-row-label">Change Credentials</div>

              {securityError && <div className="options-inline-error">{securityError}</div>}
              {securitySuccess && <div className="options-inline-success">{securitySuccess}</div>}

              <div className="options-credentials-form">
                <input
                  type="password"
                  placeholder="Current password"
                  value={currentPassword}
                  onChange={e => setCurrentPassword(e.target.value)}
                  className="options-input"
                  autoComplete="current-password"
                />
                <input
                  type="text"
                  placeholder="New username"
                  value={newUsername}
                  onChange={e => setNewUsername(e.target.value)}
                  className="options-input"
                  autoComplete="off"
                />
                <input
                  type="password"
                  placeholder="New password"
                  value={newPassword}
                  onChange={e => setNewPassword(e.target.value)}
                  className="options-input"
                  autoComplete="new-password"
                />
                <input
                  type="password"
                  placeholder="Confirm new password"
                  value={confirmPassword}
                  onChange={e => setConfirmPassword(e.target.value)}
                  className="options-input"
                  autoComplete="new-password"
                />
                <button
                  onClick={handleCredentialsSave}
                  className="btn"
                  disabled={securitySaving}
                >
                  {securitySaving ? 'Saving…' : 'Update'}
                </button>
              </div>
            </div>
          </div>

          {/* Export */}
          <div className="options-section">
            <div className="options-section-title">Export</div>
            <div className="options-row">
              <div className="options-row-info">
                <div className="options-row-label">OPML Feed URL</div>
                <div className="options-row-desc">
                  Copy the OPML endpoint URL to import all your feeds into another reader (e.g. Miniflux).
                </div>
              </div>
              <button onClick={handleCopyOPML} className="btn">
                {opmlCopied ? 'Copied!' : 'Copy OPML URL'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default Options;
