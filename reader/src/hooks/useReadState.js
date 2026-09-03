import { useEffect, useRef, useCallback } from 'react';
import { markRead, markUnread, bulkMarkRead } from '../api';
import { markArticleReadInDB } from '../db';

const BATCH_SYNC_INTERVAL_MS = 5000;
const PENDING_SYNC_STORAGE_KEY = 'reader_pending_read_sync';

function loadPendingSyncMap() {
  try {
    const raw = localStorage.getItem(PENDING_SYNC_STORAGE_KEY);
    if (!raw) return new Map();
    return new Map(JSON.parse(raw));
  } catch (err) {
    console.warn('Failed to load pending read sync queue', err);
    return new Map();
  }
}

function savePendingSyncMap(map) {
  try {
    localStorage.setItem(PENDING_SYNC_STORAGE_KEY, JSON.stringify(Array.from(map.entries())));
  } catch (err) {
    console.warn('Failed to persist pending read sync queue', err);
  }
}

function isOfflineFetchError(err) {
  return !navigator.onLine || err instanceof TypeError || err?.message?.includes('Failed to fetch');
}

export function useReadState(articles, setArticles, onReadStatusChange, { persistLocal = true } = {}) {
  const pendingSyncRef = useRef(loadPendingSyncMap()); // id -> desired read state
  const syncIntervalRef = useRef(null);
  const latestReqIdRef = useRef(new Map());

  const queuePendingSync = useCallback((id, isRead) => {
    pendingSyncRef.current.set(id, isRead);
    savePendingSyncMap(pendingSyncRef.current);
  }, []);

  const clearPendingSync = useCallback((id) => {
    pendingSyncRef.current.delete(id);
    savePendingSyncMap(pendingSyncRef.current);
  }, []);

  const applyLocalReadState = useCallback((id, isRead, previousIsRead) => {
    setArticles(prev => prev.map(a => String(a.id) === String(id) ? { ...a, is_read: isRead ? 1 : 0 } : a));
    if (persistLocal) {
      markArticleReadInDB(id, isRead).catch(err => console.error('Failed to update local DB', err));
    }
    if (onReadStatusChange) {
      onReadStatusChange(id, isRead, previousIsRead);
    }
  }, [persistLocal, setArticles, onReadStatusChange]);

  useEffect(() => {
    if (!persistLocal) return undefined;

    syncIntervalRef.current = setInterval(async () => {
      if (pendingSyncRef.current.size === 0 || !navigator.onLine) {
        return;
      }

      const pendingEntries = Array.from(pendingSyncRef.current.entries());
      pendingSyncRef.current.clear();
      savePendingSyncMap(pendingSyncRef.current);

      const readIds = pendingEntries
        .filter(([, isRead]) => isRead)
        .map(([id]) => id);
      const unreadIds = pendingEntries
        .filter(([, isRead]) => !isRead)
        .map(([id]) => id);

      try {
        if (readIds.length > 0) {
          await bulkMarkRead({ article_ids: readIds });
        }

        if (unreadIds.length > 0) {
          const unreadResults = await Promise.allSettled(unreadIds.map(id => markUnread(id)));
          unreadResults.forEach((result, index) => {
            if (result.status === 'rejected') {
              queuePendingSync(unreadIds[index], false);
            }
          });
        }
      } catch (err) {
        console.error('Failed to sync read state', err);
        pendingEntries.forEach(([id, isRead]) => queuePendingSync(id, isRead));
      }
    }, BATCH_SYNC_INTERVAL_MS);

    return () => clearInterval(syncIntervalRef.current);
  }, [persistLocal, queuePendingSync]);

  const markArticleRead = useCallback(async (id) => {
    const article = articles.find(a => String(a.id) === String(id));
    if (article && article.is_read) return;

    const reqId = (latestReqIdRef.current.get(String(id)) || 0) + 1;
    latestReqIdRef.current.set(String(id), reqId);

    applyLocalReadState(id, true, article ? !!article.is_read : false);

    try {
      if (persistLocal) {
        clearPendingSync(id);
      }
      await markRead(id);
      if (latestReqIdRef.current.get(String(id)) !== reqId) return;
    } catch (err) {
      if (latestReqIdRef.current.get(String(id)) !== reqId) return;
      if (persistLocal && isOfflineFetchError(err)) {
        queuePendingSync(id, true);
        return;
      }

      console.error('Mark read failed', err);
      applyLocalReadState(id, false, true);
    }
  }, [articles, applyLocalReadState, clearPendingSync, persistLocal, queuePendingSync]);

  const markArticleUnread = useCallback(async (id) => {
    const article = articles.find(a => String(a.id) === String(id));
    if (article && !article.is_read) return;

    const reqId = (latestReqIdRef.current.get(String(id)) || 0) + 1;
    latestReqIdRef.current.set(String(id), reqId);

    applyLocalReadState(id, false, article ? !!article.is_read : true);

    try {
      if (persistLocal) {
        clearPendingSync(id);
      }
      await markUnread(id);
      if (latestReqIdRef.current.get(String(id)) !== reqId) return;
    } catch (err) {
      if (latestReqIdRef.current.get(String(id)) !== reqId) return;
      if (persistLocal && isOfflineFetchError(err)) {
        queuePendingSync(id, false);
        return;
      }

      console.error('Mark unread failed', err);
      applyLocalReadState(id, true, false);
    }
  }, [articles, applyLocalReadState, clearPendingSync, persistLocal, queuePendingSync]);

  const toggleReadManual = useCallback(async (id, isCurrentlyRead) => {
    if (isCurrentlyRead) {
      await markArticleUnread(id);
    } else {
      await markArticleRead(id);
    }
  }, [markArticleRead, markArticleUnread]);

  return { toggleReadManual, markArticleRead, markArticleUnread };
}
