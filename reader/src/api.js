// Empty means same-origin: the API is served by the same process as this bundle.
// A non-empty VITE_API_URL is only needed for bundles that are not served over
// HTTP from the backend — currently just the Capacitor Android build.
export const API_URL = (import.meta.env.VITE_API_URL || '').trim().replace(/\/$/, '');

export const SESSION_KEY = 'rrss_session';
const LEGACY_SESSION_KEYS = ['reader_token', 'rrss_token'];

// One-time migration: adopt whichever pre-consolidation key exists so nobody
// is logged out by the switch to a shared origin.
function readStoredToken() {
  const current = localStorage.getItem(SESSION_KEY);
  if (current) return current;
  for (const key of LEGACY_SESSION_KEYS) {
    const legacy = localStorage.getItem(key);
    if (legacy) {
      localStorage.setItem(SESSION_KEY, legacy);
      localStorage.removeItem(key);
      return legacy;
    }
  }
  return '';
}

import { initDB, getQueuedVotes, clearQueuedVote } from './db.js';

let token = readStoredToken();
let authFailureCallback = null;

export const setOnAuthFailure = (callback) => {
  authFailureCallback = callback;
};

export const setToken = (newToken) => {
  token = newToken;
  if (newToken) {
    localStorage.setItem(SESSION_KEY, newToken);
    initDB().then((db) => {
      if (db.objectStoreNames.contains('sync_state')) {
        const tx = db.transaction('sync_state', 'readwrite');
        tx.objectStore('sync_state').put({ key: 'auth_token', value: newToken });
      }
    }).catch((err) => {
      console.warn('Failed to save auth_token to IDB', err);
    });
  } else {
    localStorage.removeItem(SESSION_KEY);
    initDB().then((db) => {
      if (db.objectStoreNames.contains('sync_state')) {
        const tx = db.transaction('sync_state', 'readwrite');
        tx.objectStore('sync_state').delete('auth_token');
      }
    }).catch((err) => {
      console.warn('Failed to delete auth_token from IDB', err);
    });
  }
};

const authFetch = async (path, options = {}) => {
  const headers = {
    ...options.headers,
  };
  
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const url = path.startsWith('http') ? path : `${API_URL}${path}`;
  const response = await fetch(url, { ...options, headers });
  
  if (response.status === 401) {
    setToken('');
    if (authFailureCallback) authFailureCallback();
    throw new Error('Unauthorized');
  }
  
  if (!response.ok) {
    throw new Error(`API Error: ${response.status} ${response.statusText}`);
  }
  
  return response.json();
};

export const login = async (username, password) => {
  let response;
  try {
    response = await fetch(`${API_URL}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
  } catch (error) {
    throw new Error(`Unable to reach login server at ${API_URL || window.location.origin}`);
  }

  if (!response.ok) {
    let detail = 'Login failed';
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch {
      // Keep the generic message if the response body is not JSON.
    }
    throw new Error(detail);
  }

  const data = await response.json();
  setToken(data.token);
  return data;
};

export const getAuthStatus = async () => {
  const response = await fetch(`${API_URL}/api/auth/status`, {
    headers: { 'Content-Type': 'application/json' }
  });
  if (!response.ok) {
    throw new Error(`Unable to read auth status: ${response.status}`);
  }
  return response.json();
};

// First run only. The backend rejects this once an account exists, so it cannot
// be used to take over an instance that is already set up.
export const setupAccount = async (username, password) => {
  let response;
  try {
    response = await fetch(`${API_URL}/api/auth/setup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
  } catch (error) {
    throw new Error(`Unable to reach the server at ${API_URL || window.location.origin}`);
  }

  if (!response.ok) {
    let detail = 'Could not create the account';
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch {
      // Keep the generic message if the response body is not JSON.
    }
    throw new Error(detail);
  }

  const data = await response.json();
  setToken(data.token);
  return data;
};

export const getFeeds = (since = null) => {
  let url = '/api/reader/feeds';
  if (since) url += `?since=${encodeURIComponent(since)}`;
  return authFetch(url);
};
export const SORT_MODES = ['smart', 'latest', 'random'];
export const SORT_KEY = 'reader_sort_mode';
export const AI_ENABLED_KEY = 'reader_ai_enabled';

// Cached so getSortMode() can stay synchronous — every ranking control in the reader
// calls it during render. Unset means on, so a first run or an offline start behaves
// the way it did before the switch existed.
export function isAiEnabled() {
  return localStorage.getItem(AI_ENABLED_KEY) !== 'false';
}

export async function fetchAiConfig() {
  try {
    const data = await authFetch('/api/reader/config');
    const enabled = data?.ai_enabled !== false;
    localStorage.setItem(AI_ENABLED_KEY, String(enabled));
    return enabled;
  } catch (err) {
    // Offline must not silently strip the controls — keep the last known value.
    return isAiEnabled();
  }
}

// The preference as the user last set it, whatever the AI switch says. Settings edits
// this one, so turning AI off and on again does not cost them their choice.
export function getStoredSortMode() {
  const v = localStorage.getItem(SORT_KEY);
  return SORT_MODES.includes(v) ? v : 'smart';
}

export function getSortMode() {
  if (!isAiEnabled()) return 'latest';
  return getStoredSortMode();
}

export const getArticles = (since, limit = 200, offset = 0, source_id = null, sort = null, category_id = null) => {
  let url = `/api/reader/articles?limit=${limit}&offset=${offset}`;
  if (since) url += `&since=${encodeURIComponent(since)}`;
  if (source_id) url += `&source_id=${source_id}`;
  if (category_id && category_id !== 'all') url += `&category_id=${encodeURIComponent(category_id)}`;
  url += `&sort=${sort || getSortMode()}`;
  return authFetch(url);
};
export const getArticle = (id) => authFetch(`/api/reader/articles/${id}`);
export const getArticleImages = (id) => authFetch(`/api/reader/article-images/${id}`);
export const markRead = (id) => authFetch(`/api/reader/articles/${id}/read`, { method: 'POST' });
export const markUnread = (id) => authFetch(`/api/reader/articles/${id}/unread`, { method: 'POST' });
export const bulkMarkRead = (payload) => authFetch('/api/reader/mark-read', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload)
});
export const patchFeedSource = (id, data) => authFetch(`/api/feed-sources/${id}`, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data)
});
export const sendEvents = (events) => authFetch('/api/reader/events', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ events })
});
export const setVote = (id, vote) => authFetch(`/api/reader/articles/${id}/vote`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ vote })
});
export const getScoreBreakdown = (id) => authFetch(`/api/reader/articles/${id}/score-breakdown`);

export async function flushVoteOutbox() {
  let queued = [];
  try {
    queued = await getQueuedVotes();
  } catch {
    return;
  }
  for (const row of queued) {
    try {
      await setVote(row.article_id, row.vote);
      await clearQueuedVote(row.article_id);
    } catch {
      // Still offline. Leave the rest queued for the next attempt.
      break;
    }
  }
}

