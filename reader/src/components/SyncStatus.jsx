import React, { useEffect, useState } from 'react';
import { getArticlesFromDB, getSettings } from '../db';

const UNLIMITED_STORAGE_MB = 999999;

function formatStorageSummary(usageBytes, maxStorageMB) {
  if (!usageBytes && usageBytes !== 0) return null;

  const usageMB = usageBytes / (1024 * 1024);
  const usageText = usageMB >= 1024 ? `${(usageMB / 1024).toFixed(1)}GB` : `${usageMB.toFixed(1)}MB`;

  if (!maxStorageMB || maxStorageMB >= UNLIMITED_STORAGE_MB) {
    return `${usageText} cached`;
  }

  const maxText = maxStorageMB >= 1024 ? `${(maxStorageMB / 1024).toFixed(1)}GB` : `${maxStorageMB}MB`;
  return `${usageText}/${maxText} cached`;
}

export default function SyncStatus({
  syncing,
  lastSyncTime,
  performSync,
  cacheProgress,
  isCollapsed,
  isOffline,
  isForcedOffline,
  toggleForcedOffline,
}) {
  const [cacheStats, setCacheStats] = useState({ usageBytes: null, completeCount: 0, totalCount: 0, maxStorageMB: null });

  const isCachingEnabled = localStorage.getItem('offlineCaching') === 'true';

  useEffect(() => {
    if (!isCachingEnabled || isCollapsed) return undefined;

    let cancelled = false;

    const loadCacheStats = async () => {
      try {
        const [articles, settings, estimate] = await Promise.all([
          getArticlesFromDB(),
          getSettings(),
          navigator.storage?.estimate ? navigator.storage.estimate() : Promise.resolve({})
        ]);

        if (cancelled) return;

        const retentionDays = settings.retentionDays || 14;
        const cutoff = Date.now() - (retentionDays * 24 * 60 * 60 * 1000);
        const retainedArticles = articles.filter((article) => {
          const dateStr = article.created_at || article.pub_date;
          const utcDateStr = (dateStr && !dateStr.includes('Z') && !dateStr.includes('+'))
            ? dateStr.replace(' ', 'T') + 'Z'
            : dateStr;
          return new Date(utcDateStr).getTime() >= cutoff;
        });

        setCacheStats({
          usageBytes: estimate?.usage ?? null,
          completeCount: retainedArticles.filter(article => article.offline_cache_complete).length,
          totalCount: retainedArticles.length,
          maxStorageMB: settings.maxStorageMB ?? null
        });
      } catch (err) {
        console.warn('Failed to load sync status cache stats', err);
      }
    };

    loadCacheStats();

    const isBusy = syncing || (cacheProgress > 0 && cacheProgress < 100);
    const intervalMs = isBusy ? 3000 : 30000;
    const intervalId = setInterval(loadCacheStats, intervalMs);

    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [isCachingEnabled, isCollapsed, cacheProgress, syncing, lastSyncTime, isOffline]);

  if (!isCachingEnabled) {
    return (
      <div className={`sync-status-container ${isCollapsed ? 'collapsed' : ''}`}>
        <div className="sync-info" style={{ opacity: 0.8 }}>
          <span className="material-symbols-outlined sync-icon" style={{ color: 'var(--accent)' }}>
            sensors
          </span>
          {!isCollapsed && <span className="status-text" style={{ color: 'var(--accent)', fontWeight: 600 }}>Live Mode</span>}
        </div>
      </div>
    );
  }

  let statusText = 'Unknown';
  let statusInlineSuffix = null;
  if (isOffline) {
    statusText = 'Offline';
  } else if (syncing) {
    statusText = 'Syncing';
    if (cacheProgress > 0) {
      statusInlineSuffix = `${cacheProgress}%`;
    }
  } else if (cacheProgress > 0 && cacheProgress < 100) {
    statusText = `Caching ${cacheProgress}%`;
  } else if (lastSyncTime) {
    const d = new Date(lastSyncTime);
    const diffMins = Math.floor((Date.now() - d) / 60000);
    if (diffMins < 1) statusText = 'Synced just now';
    else if (diffMins < 60) statusText = `Synced ${diffMins}m ago`;
    else statusText = `Synced ${Math.floor(diffMins / 60)}h ago`;
  }

  let shortStatus = null;
  if (lastSyncTime) {
    const d = new Date(lastSyncTime);
    const diffMins = Math.floor((Date.now() - d) / 60000);
    if (diffMins < 1) shortStatus = <span>now</span>;
    else if (diffMins < 60) shortStatus = <>{diffMins}m<br />ago</>;
    else if (diffMins < 1440) shortStatus = <>{Math.floor(diffMins / 60)}h<br />ago</>;
    else shortStatus = <>{Math.floor(diffMins / 1440)}d<br />ago</>;
  }

  const isBackgroundCaching = cacheProgress > 0 && cacheProgress < 100;
  const shouldSpinIcon = !isOffline && (syncing || isBackgroundCaching);
  const storageSummary = formatStorageSummary(cacheStats.usageBytes, cacheStats.maxStorageMB);
  const articleSummary = `${cacheStats.completeCount}/${cacheStats.totalCount} articles ready`;
  const detailText = [storageSummary, articleSummary].filter(Boolean).join(', ');
  const isStorageFull =
    cacheStats.maxStorageMB &&
    cacheStats.maxStorageMB < UNLIMITED_STORAGE_MB &&
    cacheStats.usageBytes != null &&
    (cacheStats.usageBytes / (1024 * 1024)) >= cacheStats.maxStorageMB * 0.95;

  return (
    <div
      className={`sync-status-container ${isCollapsed ? 'collapsed' : ''} ${isBackgroundCaching ? 'is-caching' : ''}`}
      style={{ cursor: 'default', position: 'relative' }}
    >
      {/* Cache Progress Bar */}
      {cacheProgress > 0 && (
        <div
          className="cache-progress-bar"
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            height: '2px',
            width: `${cacheProgress}%`,
            backgroundColor: '#4caf50',
            transition: 'width 0.3s ease',
            zIndex: 10
          }}
        />
      )}

      <div className="sync-info">
        <button
          type="button"
          className="sync-icon-btn"
          onClick={toggleForcedOffline}
          data-sync-label={isOffline ? 'Go online' : 'Go offline'}
          title={isOffline ? 'Go online' : 'Go offline'}
          aria-label={isOffline ? 'Go online' : 'Go offline'}
        >
          <span className={`material-symbols-outlined sync-icon ${shouldSpinIcon ? 'spinning' : ''} ${isOffline ? 'offline' : ''}`}>
            {isOffline ? 'cloud_off' : (shouldSpinIcon ? 'sync' : 'cloud_done')}
          </span>
        </button>
        {isCollapsed ? (
          <div className="collapsed-status-text">{shortStatus}</div>
        ) : (
          <span className="status-text">
            <span>
              {statusText}
              {syncing && <span className="sync-ellipsis" aria-hidden="true">...</span>}
              {statusInlineSuffix && <span className="sync-percent"> {statusInlineSuffix}</span>}
              {!isOffline && !syncing && !isBackgroundCaching && (
                <button type="button" className="sync-now-link" onClick={performSync}>
                  {' '} · Sync Now
                </button>
              )}
            </span>
            {isForcedOffline && <span className="status-subtext">Connectivity checks will bring you back online automatically.</span>}
            {detailText && <span className="status-subtext">{detailText}</span>}
            {isStorageFull && <span className="status-storage-full">Storage full</span>}
          </span>
        )}
      </div>
    </div>
  );
}
