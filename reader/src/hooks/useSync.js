import { useState, useEffect, useCallback, useRef } from 'react';
import { runSync } from '../sync';
import { getCheckTimestamp, getSettings } from '../db';
import { isOfflineCachingEnabled } from '../offlinePreferences';

const DAILY_SYNC_HOUR = 7;

function getMsUntilNextDailySync() {
  const now = new Date();
  const next = new Date(now);
  next.setHours(DAILY_SYNC_HOUR, 0, 0, 0);

  if (next <= now) {
    next.setDate(next.getDate() + 1);
  }

  return next.getTime() - now.getTime();
}

export function useSync({ enabled = true, onSyncFailure, onSyncSuccess } = {}) {
  const [syncing, setSyncing] = useState(false);
  const [cacheProgress, setCacheProgress] = useState(0);
  const [lastSyncTime, setLastSyncTime] = useState(null);
  const [error, setError] = useState(null);
  const syncingRef = useRef(false);

  const performSync = useCallback(async (options = {}) => {
    if (!enabled) return;
    if (syncingRef.current) return;
    syncingRef.current = true;
    setSyncing(true);
    setError(null);
    try {
      // Pass a callback to track background caching progress
      await runSync((progress) => {
        setCacheProgress(progress);
      }, options.onDataReady);
      const ts = await getCheckTimestamp();
      setLastSyncTime(ts);
      onSyncSuccess?.();
    } catch (err) {
      console.error('Sync failed, retrying in 15s...', err);
      await new Promise(resolve => setTimeout(resolve, 15000));

      try {
        await runSync((progress) => {
          setCacheProgress(progress);
        }, options.onDataReady);
        const ts = await getCheckTimestamp();
        setLastSyncTime(ts);
        onSyncSuccess?.();
      } catch (retryErr) {
        console.error('Sync retry also failed', retryErr);
        onSyncFailure?.();
        setError(retryErr.message);
        setCacheProgress(0);

        // Register a one-shot background sync so the browser retries when connectivity returns
        if ('serviceWorker' in navigator && isOfflineCachingEnabled()) {
          navigator.serviceWorker.ready
            .then((reg) => 'sync' in reg && reg.sync.register('reader-sync-retry'))
            .catch(err => console.warn('[useSync] sync.register failed:', err));
        }
      }
    } finally {
      syncingRef.current = false;
      setSyncing(false);
    }
  }, [enabled, onSyncFailure, onSyncSuccess]);

  useEffect(() => {
    if (!enabled) {
      setLastSyncTime(null);
      setCacheProgress(0);
      setSyncing(false);
      setError(null);
      return;
    }

    // Initial load of last check time
    getCheckTimestamp().then(ts => setLastSyncTime(ts));
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return undefined;

    // Keep the configured interval sync and add a fixed daily sync at 7:00 local time.
    let intervalId;
    let dailyTimeoutId;
    let dailyIntervalId;
    let cancelled = false;

    const runIfOnline = () => {
      if (navigator.onLine) {
        performSync();
      }
    };

    getSettings().then(settings => {
      if (cancelled) return;

      const intervalHours = settings.syncInterval || 6;
      if (intervalHours > 0) {
        intervalId = setInterval(runIfOnline, intervalHours * 60 * 60 * 1000);
      }

      dailyTimeoutId = setTimeout(() => {
        runIfOnline();
        dailyIntervalId = setInterval(runIfOnline, 24 * 60 * 60 * 1000);
      }, getMsUntilNextDailySync());
    });

    return () => {
      cancelled = true;
      clearInterval(intervalId);
      clearTimeout(dailyTimeoutId);
      clearInterval(dailyIntervalId);
    };
  }, [enabled, performSync]);

  return { syncing, cacheProgress, lastSyncTime, performSync, error };
}
