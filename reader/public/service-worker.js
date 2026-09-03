// Duplicated from reader/src/constants/caches.js — a classic worker cannot
// import from src/. Keep both in sync when bumping the version.
const CACHE_NAME = 'reader-app-shell-v6';
const IMAGES_CACHE = 'reader-images-v6';
const FAVICONS_CACHE = 'reader-favicons-v6';

// Duplicated from reader/src/constants/db.js — classic worker cannot import from src/.
// Keep in sync with DB_NAME and DB_VERSION in reader/src/constants/db.js.
const DB_NAME = 'reader-db';
const DB_VERSION = 5;

const APP_SHELL = [
  '/',
  '/index.html',
];

let cachingEnabled = true;
let apiUrl = '';

// Utility to limit cache size
async function trimCache(cacheName, maxItems) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length > maxItems) {
    await cache.delete(keys[0]);
    await trimCache(cacheName, maxItems);
  }
}

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SET_CACHING_ENABLED') {
    cachingEnabled = event.data.enabled;
    console.log('[SW] Caching enabled set to:', cachingEnabled);
    if (!cachingEnabled) {
      // Clear all runtime caches immediately when switching to live mode.
      caches.delete(CACHE_NAME);
      caches.delete(IMAGES_CACHE);
      caches.delete(FAVICONS_CACHE);
    }
  }

  if (event.data && event.data.type === 'SET_API_URL') {
    apiUrl = event.data.url;
    console.log('[SW] API URL set to:', apiUrl);
  }
});

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(APP_SHELL);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (
            cacheName !== CACHE_NAME && 
            cacheName !== IMAGES_CACHE && 
            cacheName !== FAVICONS_CACHE
          ) {
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => {
      return trimCache(CACHE_NAME, 50);
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') {
    event.respondWith(fetch(event.request));
    return;
  }

  // The worker has root scope but only owns the reader. Everything below is
  // served by the same origin and must reach the network untouched, or the
  // catch-all caching branch would capture dashboard assets into the reader's
  // app shell and serve them back offline.
  const isOtherApp =
    url.pathname === '/manage' ||
    url.pathname.startsWith('/manage/') ||
    url.pathname === '/fever' ||
    url.pathname.startsWith('/fever/') ||
    url.pathname.startsWith('/feed/');
  const isNonReaderApi =
    url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/reader/');
  if (isOtherApp || isNonReaderApi) {
    return;
  }

  // API calls -> Network First, but we handle DB fallback in React. For actual API requests, we just do Network Only.
  if (url.pathname.startsWith('/api/reader/')) {
    if (url.pathname.includes('/image-proxy')) {
      // Images: Cache First
      event.respondWith(
        caches.match(event.request).then((response) => {
          return response || fetch(event.request).then(netResp => {
            if (cachingEnabled && netResp.status === 200) {
              const respClone = netResp.clone();
              caches.open(IMAGES_CACHE).then(cache => {
                cache.put(event.request, respClone);
              });
            }
            return netResp;
          });
        })
      );
      return;
    }

    if (url.pathname.includes('/favicon/')) {
      // Favicons: Cache First
      event.respondWith(
        caches.match(event.request).then((response) => {
          return response || fetch(event.request).then(netResp => {
            if (cachingEnabled && netResp.status === 200) {
              const respClone = netResp.clone();
              caches.open(FAVICONS_CACHE).then(cache => {
                cache.put(event.request, respClone);
              });
            }
            return netResp;
          });
        })
      );
      return;
    }

    // Other API: Network Only (React handles indexedDB fallback)
    event.respondWith(fetch(event.request));
    return;
  }

  // Assets (JS, CSS, static): Network First to get updates, fallback to Cache
  if (!cachingEnabled) {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    fetch(event.request).then(response => {
      return caches.open(CACHE_NAME).then(cache => {
        cache.put(event.request, response.clone());
        return response;
      });
    }).catch(() => {
      return caches.match(event.request);
    })
  );
});

// ─── Periodic Background Sync ────────────────────────────────────────────────

self.addEventListener('periodicsync', (event) => {
  if (event.tag === 'reader-periodic-sync' && cachingEnabled) {
    console.log('[SW] periodicsync fired');
    event.waitUntil(doBackgroundSync());
  }
});

// ─── One-shot Background Sync (retry on failure / launch) ────────────────────

self.addEventListener('sync', (event) => {
  if (event.tag === 'reader-sync-retry' && cachingEnabled) {
    console.log('[SW] sync event fired, tag:', event.tag);
    event.waitUntil(doBackgroundSync());
  }
});

// ─── Background Sync Helper ───────────────────────────────────────────────────

function openReaderDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (event) => {
      const db = event.target.result;
      if (event.oldVersion < 2 && db.objectStoreNames.contains('articles')) {
        db.deleteObjectStore('articles');
      }
      if (!db.objectStoreNames.contains('articles')) {
        const s = db.createObjectStore('articles', { keyPath: 'id' });
        s.createIndex('source_id', 'source_id');
        s.createIndex('created_at', 'created_at');
        s.createIndex('is_read', 'is_read');
      }
      if (!db.objectStoreNames.contains('feeds')) {
        db.createObjectStore('feeds', { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains('sync_state')) {
        db.createObjectStore('sync_state', { keyPath: 'key' });
      }
      if (!db.objectStoreNames.contains('vote_outbox')) {
        db.createObjectStore('vote_outbox', { keyPath: 'article_id' });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
    req.onblocked = () => reject(new Error('IDB open blocked'));
  });
}

function idbGet(db, store, key) {
  return new Promise((resolve, reject) => {
    const req = db.transaction(store, 'readonly').objectStore(store).get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function idbPut(db, store, value) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, 'readwrite');
    tx.objectStore(store).put(value);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function doBackgroundSync() {
  // API_URL is '' when the API is same-origin, which is falsy — without this
  // fallback background sync would silently stop.
  const base = apiUrl || self.location.origin;

  let db;
  try { db = await openReaderDB(); }
  catch (err) { console.error('[SW] IDB open failed', err); return; }

  const tokenRecord = await idbGet(db, 'sync_state', 'auth_token');
  const token = tokenRecord?.value;
  if (!token) { console.warn('[SW] doBackgroundSync: no token, skipping'); db.close(); return; }

  const lastSyncRecord = await idbGet(db, 'sync_state', 'last_sync');
  const lastSync = lastSyncRecord?.value || null;

  const headers = { Authorization: `Bearer ${token}` };

  try {
    // Feeds
    const feedsResp = await fetch(`${base}/api/reader/feeds`, { headers });
    if (!feedsResp.ok) throw new Error(`Feeds fetch failed: ${feedsResp.status}`);
    const feedsData = await feedsResp.json();
    await idbPut(db, 'feeds', { id: 'feedsData', data: feedsData });

    // Delta articles (lightweight — full pre-caching left to foreground)
    const since = lastSync ? `&since=${encodeURIComponent(lastSync)}` : '';
    const articlesResp = await fetch(`${base}/api/reader/articles?limit=50${since}`, { headers });
    if (!articlesResp.ok) throw new Error(`Articles fetch failed: ${articlesResp.status}`);
    const articlesData = await articlesResp.json();

    // Merge each article — preserve cached full content and offline state
    for (const article of (articlesData.articles || [])) {
      const existing = await idbGet(db, 'articles', article.id);
      await idbPut(db, 'articles', {
        ...(existing || {}),
        ...article,
        content: article.content || existing?.content,
        offline_cache_complete: article.offline_cache_complete ?? existing?.offline_cache_complete,
        offline_cached_at: article.offline_cached_at ?? existing?.offline_cached_at,
      });
    }

    if (articlesData.sync_timestamp) {
      await idbPut(db, 'sync_state', { key: 'last_sync', value: articlesData.sync_timestamp });
    }

    console.log(`[SW] Background sync complete: ${(articlesData.articles || []).length} articles`);

    // Notify any open clients to reload from DB
    const clients = await self.clients.matchAll({ includeUncontrolled: true, type: 'window' });
    for (const client of clients) {
      client.postMessage({ type: 'BACKGROUND_SYNC_COMPLETE' });
    }
  } catch (err) {
    console.error('[SW] doBackgroundSync failed:', err);
    db.close();
    throw err; // Re-throw so the browser retries the sync event
  }
  db.close();
}
