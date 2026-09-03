// Relative by default: the dashboard is served from the same origin as the API.
export const API_URL = (process.env.REACT_APP_API_URL || '/api').trim().replace(/\/$/, '');
export const BACKEND_BASE_URL = API_URL.replace(/\/api$/, '');

export function toAbsoluteBackendUrl(url) {
  if (!url) return url;
  if (/^https?:\/\//i.test(url)) return url;
  if (url.startsWith('/')) return `${BACKEND_BASE_URL}${url}`;
  return url;
}

if (typeof window !== 'undefined') {
  window.__RRSS_DEBUG__ = {
    dashboardApiUrl: API_URL,
    dashboardBackendBaseUrl: BACKEND_BASE_URL,
    origin: window.location.origin,
    href: window.location.href,
    envApiUrl: process.env.REACT_APP_API_URL || null,
    readTokenPreview: () => {
      const token = localStorage.getItem(TOKEN_KEY) || '';
      return token ? `${token.slice(0, 8)}...` : '';
    },
  };
}

// --- Token management ---
const TOKEN_KEY = 'rrss_session';
const LEGACY_TOKEN_KEYS = ['rrss_token', 'reader_token'];

// One-time migration — see reader/src/api.js for the matching logic.
function migrateLegacyToken() {
  if (localStorage.getItem(TOKEN_KEY)) return;
  for (const key of LEGACY_TOKEN_KEYS) {
    const legacy = localStorage.getItem(key);
    if (legacy) {
      localStorage.setItem(TOKEN_KEY, legacy);
      localStorage.removeItem(key);
      return;
    }
  }
}
migrateLegacyToken();

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || '';
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

let authFailureCallback = null;

export function setOnAuthFailure(callback) {
  authFailureCallback = callback;
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

// --- Authenticated fetch wrapper ---
function authHeaders(extra = {}) {
  const token = getToken();
  const headers = { ...extra };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

async function authFetch(url, options = {}) {
  const headers = authHeaders(options.headers || {});
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    clearToken();
    if (authFailureCallback) {
      authFailureCallback();
    }
    throw new Error('Session expired');
  }
  return response;
}

// --- Auth endpoints (no token needed) ---
export async function getAuthStatus() {
  const token = getToken();
  const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
  const response = await fetch(`${API_URL}/auth/status`, { headers });
  if (!response.ok) throw new Error('Failed to check auth status');
  return response.json();
}

export async function logout() {
  const response = await authFetch(`${API_URL}/auth/logout`, { method: 'POST' });
  clearToken();
  if (!response.ok) throw new Error('Failed to logout');
  return response.json();
}

export async function updateCredentials(currentPassword, newUsername, newPassword) {
  const response = await authFetch(`${API_URL}/auth/credentials`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      current_password: currentPassword,
      new_username: newUsername,
      new_password: newPassword,
    }),
  });
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || 'Failed to update credentials');
  }
  return response.json();
}

export async function getSystemSettings() {
  const response = await authFetch(`${API_URL}/settings`);
  if (!response.ok) throw new Error('Failed to load system settings');
  return response.json();
}

export async function getStorageStats() {
  const response = await authFetch(`${API_URL}/settings/storage-stats`);
  if (!response.ok) throw new Error('Failed to load storage stats');
  return response.json();
}

export async function updateSystemSettings(settings) {
  const response = await authFetch(`${API_URL}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || 'Failed to save system settings');
  }
  return response.json();
}

// --- Parse ---
export async function parseURL(url, overrides = {}) {
  try {
    const response = await authFetch(`${API_URL}/parse`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, overrides }),
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(
        errorData.detail || `API error: ${response.status} ${response.statusText}`
      );
    }

    const data = await response.json();
    return data;
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Failed to parse URL';
    console.error('[ParseURL Error]', {
      message: errorMessage,
      originalError: error,
      apiUrl: API_URL,
      timestamp: new Date().toISOString(),
    });
    throw new Error(errorMessage);
  }
}

// Link Discovery
export async function discoverLinks(url) {
  const response = await authFetch(`${API_URL}/discover-links`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  if (!response.ok) throw new Error('Failed to discover links');
  return response.json();
}

export async function discoverLinksWithSelector(url, itemSelector, linkSelector, excludeSelector) {
  const response = await authFetch(`${API_URL}/discover-links-with-selector`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, item_selector: itemSelector, link_selector: linkSelector, exclude_selector: excludeSelector }),
  });
  if (!response.ok) throw new Error('Failed to discover links');
  return response.json();
}

// Feed Sources CRUD
export async function getFeedSources() {
  const response = await authFetch(`${API_URL}/feed-sources`);
  if (!response.ok) throw new Error('Failed to fetch feed sources');
  return response.json();
}

export async function getTranslationUsage(days = 7) {
  const response = await authFetch(`${API_URL}/translation/usage?days=${days}`);
  if (!response.ok) throw new Error('Failed to fetch translation usage');
  return response.json();
}

export async function createFeedSource(data) {
  const response = await authFetch(`${API_URL}/feed-sources`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    let errorMsg = 'Failed to create feed source';
    try {
      const err = await response.json();
      if (err.detail) {
        if (typeof err.detail === 'string') {
          errorMsg = err.detail;
        } else if (Array.isArray(err.detail)) {
          errorMsg = err.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
        } else {
          errorMsg = JSON.stringify(err.detail);
        }
      }
    } catch (_) {}
    throw new Error(errorMsg);
  }
  return response.json();
}

export async function updateFeedSource(id, data) {
  const response = await authFetch(`${API_URL}/feed-sources/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    let errorMsg = 'Failed to update feed source';
    try {
      const err = await response.json();
      if (err.detail) {
        if (typeof err.detail === 'string') {
          errorMsg = err.detail;
        } else if (Array.isArray(err.detail)) {
          errorMsg = err.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
        } else {
          errorMsg = JSON.stringify(err.detail);
        }
      }
    } catch (_) {}
    throw new Error(errorMsg);
  }
  return response.json();
}

export async function deleteFeedSource(id) {
  const response = await authFetch(`${API_URL}/feed-sources/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete feed source');
  return response.json();
}

export async function checkFeedSource(id) {
  const response = await authFetch(`${API_URL}/feed-sources/${id}/check`, {
    method: 'POST',
  });
  if (!response.ok) throw new Error('Failed to check feed source');
  return response.json();
}

export async function toggleFeedSourceEnabled(id) {
  const response = await authFetch(`${API_URL}/feed-sources/${id}/toggle-enabled`, {
    method: 'POST',
  });
  if (!response.ok) throw new Error('Failed to toggle feed source');
  return response.json();
}

// Feed Articles (from DB, for dashboard preview)
export async function getFeedArticles(sourceId) {
  const response = await authFetch(`${API_URL}/feed-sources/${sourceId}/articles`);
  if (!response.ok) throw new Error('Failed to fetch articles');
  return response.json();
}

// Activity Log
export async function getActivityLog(sinceId = 0) {
  const response = await authFetch(`${API_URL}/activity-log?since=${sinceId}`);
  if (!response.ok) throw new Error('Failed to fetch activity log');
  return response.json();
}

// Feed Generation
export async function flushFeedArticles(id) {
  const response = await authFetch(`${API_URL}/feed-sources/${id}/flush`, {
    method: 'POST',
  });
  if (!response.ok) throw new Error('Failed to flush articles');
  return response.json();
}

export async function generateFeed(id) {
  const response = await authFetch(`${API_URL}/feed-sources/${id}/generate`, {
    method: 'POST',
  });
  if (!response.ok) throw new Error('Failed to generate feed');
  return response.json();
}

export async function refreshAllFeeds() {
  const response = await authFetch(`${API_URL}/feed-sources/refresh-all`, {
    method: 'POST',
  });
  if (!response.ok) throw new Error('Failed to refresh feeds');
  return response.json();
}

export async function getRefreshStatus() {
  const response = await authFetch(`${API_URL}/refresh-status`);
  if (!response.ok) return { refreshing: false, current_source_id: null };
  return response.json();
}

// Categories CRUD
export async function getCategories() {
  const response = await authFetch(`${API_URL}/categories`);
  if (!response.ok) throw new Error('Failed to fetch categories');
  return response.json();
}

export async function createCategory(name) {
  const response = await authFetch(`${API_URL}/categories`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!response.ok) throw new Error('Failed to create category');
  return response.json();
}

export async function updateCategory(id, name) {
  const response = await authFetch(`${API_URL}/categories/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!response.ok) throw new Error('Failed to update category');
  return response.json();
}

export async function deleteCategory(id) {
  const response = await authFetch(`${API_URL}/categories/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || 'Failed to delete category');
  }
  return response.json();
}

// Fever API Auth
export async function getFeverAuth() {
  const response = await authFetch(`${API_URL}/fever-auth`);
  if (!response.ok) throw new Error('Failed to fetch Fever auth');
  return response.json();
}

export async function setFeverAuth(username, password) {
  const response = await authFetch(`${API_URL}/fever-auth`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) throw new Error('Failed to set Fever auth');
  return response.json();
}

export async function deleteFeverAuth() {
  const response = await authFetch(`${API_URL}/fever-auth`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete Fever auth');
  return response.json();
}

export async function getFeverEndpoint() {
  const response = await authFetch(`${API_URL}/fever-endpoint`);
  if (!response.ok) throw new Error('Failed to fetch Fever endpoint');
  return response.json();
}

export async function getLabelWeights() {
  const response = await authFetch(`${API_URL}/reader/label-weights`);
  if (!response.ok) throw new Error('Failed to fetch label weights');
  return response.json();
}
