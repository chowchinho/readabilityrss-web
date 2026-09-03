import { openDB } from 'idb';
import { DB_NAME, DB_VERSION } from './constants/db';

export async function initDB() {
  const openOnce = () => openDB(DB_NAME, DB_VERSION, {
    upgrade(db, oldVersion) {
      if (oldVersion < 2) {
        if (db.objectStoreNames.contains('articles')) {
          db.deleteObjectStore('articles');
        }
      }
      if (!db.objectStoreNames.contains('articles')) {
        const articleStore = db.createObjectStore('articles', { keyPath: 'id' });
        articleStore.createIndex('source_id', 'source_id');
        articleStore.createIndex('created_at', 'created_at');
        articleStore.createIndex('is_read', 'is_read');
      }
      if (!db.objectStoreNames.contains('feeds')) {
        db.createObjectStore('feeds', { keyPath: 'id' }); // stores categories list + total unread in a single 'metadata' record, and feeds in others
      }
      if (!db.objectStoreNames.contains('sync_state')) {
        db.createObjectStore('sync_state', { keyPath: 'key' });
      }
      if (!db.objectStoreNames.contains('vote_outbox')) {
        db.createObjectStore('vote_outbox', { keyPath: 'article_id' });
      }
      if (!db.objectStoreNames.contains('article_images')) {
        db.createObjectStore('article_images', { keyPath: 'article_id' });
      }
    },
  });

  try {
    const db = await openOnce();
    if (!db.objectStoreNames.contains('articles') ||
        !db.objectStoreNames.contains('feeds') ||
        !db.objectStoreNames.contains('sync_state') ||
        !db.objectStoreNames.contains('vote_outbox') ||
        !db.objectStoreNames.contains('article_images')) {
      db.close();
      await new Promise((resolve) => {
        const req = indexedDB.deleteDatabase(DB_NAME);
        req.onsuccess = () => resolve();
        req.onerror = () => resolve();
        req.onblocked = () => resolve();
      });
      return await openOnce();
    }
    return db;
  } catch (err) {
    const message = String(err?.message || '');
    const name = String(err?.name || '');
    const shouldRecover = name === 'UnknownError' || message.includes('Internal error opening backing store');
    if (!shouldRecover) throw err;

    await new Promise((resolve) => {
      const req = indexedDB.deleteDatabase(DB_NAME);
      req.onsuccess = () => resolve();
      req.onerror = () => resolve();
      req.onblocked = () => resolve();
    });

    return openOnce();
  }
}

// Articles
export async function getArticlesFromDB() {
  const db = await initDB();
  const all = await db.getAll('articles');
  return all.sort((a, b) => {
    const dateStrA = a.pub_date || a.created_at;
    const dateStrB = b.pub_date || b.created_at;
    const utcDateA = (dateStrA && !dateStrA.includes('Z') && !dateStrA.includes('+')) 
      ? dateStrA.replace(' ', 'T') + 'Z' 
      : dateStrA;
    const utcDateB = (dateStrB && !dateStrB.includes('Z') && !dateStrB.includes('+')) 
      ? dateStrB.replace(' ', 'T') + 'Z' 
      : dateStrB;
    return new Date(utcDateB) - new Date(utcDateA);
  });
}

export async function saveArticlesToDB(articles) {
  const db = await initDB();
  const tx = db.transaction('articles', 'readwrite');
  for (const article of articles) {
    const existing = await tx.store.get(article.id);

    // If the incoming record has no full content, preserve whatever is already cached
    // (sync endpoints return content_head snippets, not full content)
    if (!article.content) {
      if (existing?.content) {
        tx.store.put({
          ...existing,
          ...article,
          content: existing.content,
          offline_cache_complete: existing.offline_cache_complete,
          offline_cached_at: existing.offline_cached_at
        });
        continue;
      }
    }

    tx.store.put({
      ...existing,
      ...article,
      offline_cache_complete: article.offline_cache_complete ?? existing?.offline_cache_complete,
      offline_cached_at: article.offline_cached_at ?? existing?.offline_cached_at
    });
  }
  await tx.done;
}

export async function getArticleFromDB(id) {
  const db = await initDB();
  return db.get('articles', id);
}

export async function deleteArticleFromDB(id) {
  const db = await initDB();
  await db.delete('articles', id);
  if (db.objectStoreNames.contains('article_images')) {
    await db.delete('article_images', id);
  }
}

export async function saveArticleImagesToDB(articleId, images) {
  if (!articleId || !images) return;
  const db = await initDB();
  await db.put('article_images', { article_id: articleId, images });
}

export async function getArticleImagesFromDB(articleId) {
  if (!articleId) return null;
  const db = await initDB();
  const res = await db.get('article_images', articleId);
  return res ? res.images : null;
}

export async function deleteArticleImagesFromDB(articleId) {
  if (!articleId) return;
  const db = await initDB();
  await db.delete('article_images', articleId);
}

export async function markArticleReadInDB(id, isRead) {
  const db = await initDB();
  const tx = db.transaction('articles', 'readwrite');
  const article = await tx.store.get(id);
  if (article) {
    article.is_read = isRead ? 1 : 0;
    tx.store.put(article);
  }
  await tx.done;
}

export async function setArticleOfflineCacheState(id, isComplete) {
  const db = await initDB();
  const tx = db.transaction('articles', 'readwrite');
  const article = await tx.store.get(id);
  if (article) {
    article.offline_cache_complete = isComplete;
    article.offline_cached_at = isComplete ? new Date().toISOString() : null;
    tx.store.put(article);
  }
  await tx.done;
}

// Feeds
export async function saveFeedsDataToDB(feedsData) {
  const db = await initDB();
  await db.put('feeds', { id: 'feedsData', data: feedsData });
}

export async function getFeedsDataFromDB() {
  const db = await initDB();
  const result = await db.get('feeds', 'feedsData');
  return result ? result.data : { categories: [], total_unread: 0 };
}

// Sync State
export async function getSyncTimestamp() {
  const db = await initDB();
  const res = await db.get('sync_state', 'last_sync');
  return res ? res.value : null;
}

export async function setSyncTimestamp(timestamp) {
  const db = await initDB();
  await db.put('sync_state', { key: 'last_sync', value: timestamp });
}

export async function getCheckTimestamp() {
  const db = await initDB();
  const res = await db.get('sync_state', 'last_check');
  return res ? res.value : null;
}

export async function setCheckTimestamp(timestamp) {
  const db = await initDB();
  await db.put('sync_state', { key: 'last_check', value: timestamp });
}


const SETTINGS_LS_KEY = 'reader_settings_v1';

export async function getSettings() {
  const lsFallback = () => {
    try {
      const raw = localStorage.getItem(SETTINGS_LS_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  };

  try {
    const db = await initDB();
    const res = await db.get('sync_state', 'settings');
    if (res) return res.value;
  } catch { /* fall through */ }

  return lsFallback() || { syncInterval: 6, retentionDays: 3, maxStorageMB: 2000, showReadArticles: false };
}

export async function saveSettings(settings) {
  // Always persist to localStorage so mobile browsers with unreliable IndexedDB
  // still retain settings across sessions.
  try { localStorage.setItem(SETTINGS_LS_KEY, JSON.stringify(settings)); } catch { /* quota */ }

  try {
    const db = await initDB();
    await db.put('sync_state', { key: 'settings', value: settings });
  } catch { /* IndexedDB unavailable — localStorage copy is the backup */ }
}

export async function clearAllData() {
  const db = await initDB();
  await db.clear('articles');
  await db.clear('feeds');
  await db.clear('sync_state');
  if (db.objectStoreNames.contains('vote_outbox')) {
    await db.clear('vote_outbox');
  }
  if (db.objectStoreNames.contains('article_images')) {
    await db.clear('article_images');
  }
}

export async function queueVote(id, vote) {
  const db = await initDB();
  await db.put('vote_outbox', { article_id: id, vote, queued_at: Date.now() });
}

export async function getQueuedVotes() {
  const db = await initDB();
  return db.getAll('vote_outbox');
}

export async function clearQueuedVote(id) {
  const db = await initDB();
  await db.delete('vote_outbox', id);
}
