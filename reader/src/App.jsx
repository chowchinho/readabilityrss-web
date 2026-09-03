import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { flushSync } from 'react-dom';
import { useParams, useNavigate } from 'react-router-dom';
import { makeSlug, parseId } from './utils/slug';
import { displayLimitCovering } from './utils/displayWindow';
import { resolveAutoMarkRead } from './utils/autoMarkRead';
import Login from './components/Login';
import { getArticle, getArticles, getFeeds, setOnAuthFailure, bulkMarkRead, patchFeedSource, getSortMode, API_URL, SESSION_KEY, isAiEnabled, fetchAiConfig, flushVoteOutbox } from './api';
import { getFeedsDataFromDB, getArticlesFromDB, getArticleFromDB, getSettings, saveArticlesToDB, markArticleReadInDB } from './db';
import { applyLocalCachePolicy } from './sync';
import Sidebar from './components/Sidebar';
import ArticleFeed from './components/ArticleFeed';
import ArticleReader from './components/ArticleReader';
import SourcesIndex from './components/SourcesIndex';
import Settings from './components/Settings';
import Resizer from './components/Resizer';
import { useReadState } from './hooks/useReadState';
import { useSync } from './hooks/useSync';
import { useOffline } from './hooks/useOffline';
import { useAdjacentPrefetch } from './hooks/useAdjacentPrefetch';
import { isOfflineCachingEnabled } from './offlinePreferences';

function getArticleTimestamp(article) {
  const dateStr = article?.pub_date || article?.created_at;
  if (!dateStr) return 0;

  const utcDateStr = (!dateStr.includes('Z') && !dateStr.includes('+'))
    ? dateStr.replace(' ', 'T') + 'Z'
    : dateStr;

  return new Date(utcDateStr).getTime() || 0;
}

// Smart and random orderings are produced server-side; the arrays below already hold
// that order, so re-sorting them by date silently discarded the ranking. Only
// "latest" needs a client-side sort.
function sortForMode(list) {
  if (getSortMode() !== 'latest') return list;
  return [...list].sort((a, b) => getArticleTimestamp(b) - getArticleTimestamp(a));
}

function App() {
  // Keep the selected feed visibly active for a beat before mobile navigation.
  // This gives Android predictive back a stable Pane 1 snapshot to return to.
  const MOBILE_FEED_NAV_DELAY_MS = 100;
  const SYSTEM_VERSION = "2026-08-11 17:06 UTC";
  const { feedSlug, articleSlug } = useParams();
  const routeArticleId = articleSlug ? parseId(articleSlug) : null;
  const routeFeedId = feedSlug ? parseId(feedSlug) : null;
  const routeContextFeedId = feedSlug === 'all' ? null : routeFeedId;
  const selectedArticleId = articleSlug ? parseId(articleSlug) : null;
  const navigate = useNavigate();
  const [isAuthenticated, setIsAuthenticated] = useState(!!localStorage.getItem(SESSION_KEY));
  const [error, setError] = useState('');

  const [feedsData, setFeedsData] = useState({ categories: [], total_unread: 0 });
  const [articles, setArticles] = useState([]);
  const [fullArticle, setFullArticle] = useState(null);
  const [articleLoading, setArticleLoading] = useState(false);
  const [articleFetchRevision, setArticleFetchRevision] = useState(0);
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const [exitConfirmVisible, setExitConfirmVisible] = useState(false);
  const [pane2ContextFeedId, setPane2ContextFeedId] = useState(() => routeContextFeedId);
  // The Sources Index Hub replaces the all-articles stream. Deriving it from the route
  // rather than holding it in state is what makes browser-back out of an article land
  // on the index again instead of on a stream view nothing navigated to.
  const [isDesktop, setIsDesktop] = useState(window.innerWidth >= 768);
  const isIndexViewMode = isDesktop && !selectedArticleId;
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(localStorage.getItem('reader_sidebar_collapsed') === 'true');
  const [showSettings, setShowSettings] = useState(false);
  const [loading, setLoading] = useState(true);
  const [allArticlesViewMode, setAllArticlesViewMode] = useState(() => localStorage.getItem('reader_all_articles_view_mode') || 'standard');
  const [deferredInstallPrompt, setDeferredInstallPrompt] = useState(null);
  const [isInstallStandalone, setIsInstallStandalone] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  });
  const [isInstallPromptPending, setIsInstallPromptPending] = useState(false);
  const [selectedCategoryId, setSelectedCategoryId] = useState('all');
  const [showOnlyUnread, setShowOnlyUnread] = useState(false);
  const [showReadArticles, setShowReadArticles] = useState(localStorage.getItem('reader_show_read') === 'true');
  const [hideEmptySources, setHideEmptySources] = useState(localStorage.getItem('reader_hide_empty_sources') === 'true');
  const [offlineCachingEnabled, setOfflineCachingEnabled] = useState(isOfflineCachingEnabled);
  const [displayLimit, setDisplayLimit] = useState(20);
  const [isFetchingMore, setIsFetchingMore] = useState(false);
  const isFetchingMoreRef = useRef(false);
  // Scope+offset pairs the server has nothing new for. The backfill effect re-fires
  // while the in-scope count stays put, and articles that arrive but fail the local
  // retention filter never move it — without this the same page refetches forever.
  const exhaustedFetchesRef = useRef(new Set());
  const loadedArticleIdsRef = useRef(new Set());
  const autoMarkedIdRef = useRef(null);
  const [settings, setSettings] = useState({ retentionDays: 14 });
  
  const [sessionReadIds, setSessionReadIds] = useState(new Set());
  const [hiddenReadIds, setHiddenReadIds] = useState(new Set());
  const [readStateOverrides, setReadStateOverrides] = useState(new Map());
  const [toastMsg, setToastMsg] = useState(null);
  const isLeavingRef = useRef(false);
  const skipNextPopRef = useRef(false);
  const isMobileSidebarOpenRef = useRef(false);
  const toastTimerRef = useRef(null);
  const feedNavigationTimerRef = useRef(null);
  const readStateBaselineRef = useRef(new Map());
  const isPureLiveMode = !offlineCachingEnabled;
  const { isOffline, isForcedOffline, toggleForcedOffline, reportSyncFailure, reportSyncSuccess } = useOffline(offlineCachingEnabled);
  useEffect(() => { isFetchingMoreRef.current = isFetchingMore; }, [isFetchingMore]);
  useEffect(() => {
    loadedArticleIdsRef.current = new Set(articles.map(a => String(a.id)));
  }, [articles]);

  const showToast = useCallback((msg) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToastMsg(msg);
    toastTimerRef.current = setTimeout(() => setToastMsg(null), 1500);
  }, []);

  const handleReadStatusChange = useCallback((id, isRead, previousIsRead) => {
    setFullArticle(prev => (prev && String(prev.id) === String(id)) ? { ...prev, is_read: isRead ? 1 : 0 } : prev);

    setReadStateOverrides(prev => {
      const next = new Map(prev);
      const baselineMap = readStateBaselineRef.current;
      const hasBaseline = baselineMap.has(id);
      const baseline = hasBaseline ? baselineMap.get(id) : previousIsRead;

      if (!hasBaseline) {
        baselineMap.set(id, previousIsRead);
      }

      if (baseline === isRead) {
        next.delete(id);
        baselineMap.delete(id);
      } else {
        next.set(id, isRead);
      }

      return next;
    });

    if (isRead) {
      if (!showReadArticles) {
        setSessionReadIds(prev => new Set(prev).add(id));
      }
    } else {
      setSessionReadIds(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      setHiddenReadIds(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }, [showReadArticles]);

  const handleVoted = useCallback((articleId, vote) => {
    if (!vote) return;
    const previous = articles.find((a) => String(a.id) === String(articleId));
    const wasRead = previous ? !!previous.is_read : false;

    setArticles((prev) => prev.map((a) =>
      a.id === articleId ? { ...a, vote, is_read: 1 } : a
    ));
    markArticleReadInDB(articleId, true).catch(() => {});

    // The vote endpoint marks the article read server-side, so the same local
    // bookkeeping a manual mark-read does has to happen here too. Without it the card
    // fails the sessionReadIds test in filteredArticles and disappears from column 2,
    // where it is meant to stay in its voted state - a like would read as a delete.
    // It also keeps the sidebar unread count from lagging until the next fetch.
    if (!wasRead) handleReadStatusChange(articleId, true, false);
  }, [articles, handleReadStatusChange]);

  const handleToggleSidebar = useCallback(() => {
    const newValue = !isSidebarCollapsed;
    setIsSidebarCollapsed(newValue);
    localStorage.setItem('reader_sidebar_collapsed', String(newValue));
  }, [isSidebarCollapsed]);

  const { toggleReadManual, markArticleRead } = useReadState(articles, setArticles, handleReadStatusChange, {
    persistLocal: offlineCachingEnabled,
  });
  const { syncing, cacheProgress, performSync, lastSyncTime } = useSync({
    enabled: offlineCachingEnabled,
    onSyncFailure: reportSyncFailure,
    onSyncSuccess: reportSyncSuccess,
  });
  const prevIsOfflineRef = useRef(isOffline);

  useEffect(() => {
    if (!isOffline) {
      flushVoteOutbox().catch(() => {});
      if (offlineCachingEnabled && prevIsOfflineRef.current) {
        performSync();
      }
    }
    prevIsOfflineRef.current = isOffline;
  }, [offlineCachingEnabled, isOffline, performSync]);

  // Always-current ref for isDesktop — avoids stale closure in event handlers
  const isDesktopRef = useRef(isDesktop);
  useEffect(() => { isDesktopRef.current = isDesktop; }, [isDesktop]);

  // Mobile back-button handling
  const pushMobileHistoryState = useCallback((state) => {
    window.history.pushState(state, '', window.location.href);
  }, []);

  const getFeedPath = useCallback((feedId) => {
    if (!feedId) return '/';

    const feed = feedsData.categories.flatMap(c => c.feeds).find(f => f.id === feedId);
    return `/${makeSlug(feedId, feed?.name || String(feedId))}/`;
  }, [feedsData]);

  const getArticlePath = useCallback((article, contextFeedId = pane2ContextFeedId) => {
    const contextSlug = contextFeedId
      ? makeSlug(contextFeedId, feedsData.categories.flatMap(c => c.feeds).find(f => f.id === contextFeedId)?.name || String(contextFeedId))
      : 'all';

    return `/${contextSlug}/${makeSlug(article.id, article.title)}`;
  }, [feedsData, pane2ContextFeedId]);

  const hideReadArticlesForLeavingFeed = useCallback((leavingFeedId) => {
    if (showReadArticles) return;

    setHiddenReadIds(prev => {
      const next = new Set(prev);
      articles.forEach(article => {
        const isInLeavingFeed = leavingFeedId
          ? String(article.source_id) === String(leavingFeedId)
          : true;

        if (isInLeavingFeed && article.is_read && sessionReadIds.has(article.id)) {
          next.add(article.id);
        }
      });
      return next;
    });
  }, [articles, sessionReadIds, showReadArticles]);

  const mergeArticlesNewestFirst = useCallback((incomingArticles) => {
    setArticles(prev => {
      const byId = new Map(prev.map(article => [article.id, article]));
      incomingArticles.forEach(article => {
        const existing = byId.get(article.id);
        const mergedIsRead = (existing?.is_read || article.is_read) ? 1 : 0;
        byId.set(article.id, existing ? { ...existing, ...article, is_read: mergedIsRead } : article);
      });

      return sortForMode(Array.from(byId.values()));
    });
  }, []);

  const hydrateAllSources = useCallback(async () => {
    if (isPureLiveMode) return;
    if (typeof navigator !== 'undefined' && !navigator.onLine) return;

    const retentionDays = settings.retentionDays || 14;
    const since = new Date(Date.now() - (retentionDays * 24 * 60 * 60 * 1000)).toISOString();
    const pageSize = 500;
    const aggregatedArticles = [];
    let offset = 0;

    try {
      while (true) {
        const resp = await getArticles(since, pageSize, offset, null);
        const pageArticles = resp?.articles || [];

        if (pageArticles.length === 0) break;

        aggregatedArticles.push(...pageArticles);

        if (pageArticles.length < pageSize) break;
        offset += pageSize;
      }

      if (aggregatedArticles.length > 0) {
        mergeArticlesNewestFirst(aggregatedArticles);
        await saveArticlesToDB(aggregatedArticles);
      }
    } catch (err) {
      console.warn('Failed to hydrate all-sources article list', err);
    }
  }, [isPureLiveMode, mergeArticlesNewestFirst, settings.retentionDays]);

  const loadLiveData = useCallback(async (clearAdjustments = false) => {
    setLoading(true);
    try {
      const liveSettings = await getSettings();
      const retentionDays = liveSettings.retentionDays || 14;
      const since = new Date(Date.now() - (retentionDays * 24 * 60 * 60 * 1000)).toISOString();
      const [liveFeeds, liveArticles] = await Promise.all([
        getFeeds(since),
        getArticles(null, 200)
      ]);

      setFeedsData(liveFeeds || { categories: [], total_unread: 0 });
      setArticles(liveArticles?.articles || []);
      setSettings(liveSettings);

      if (clearAdjustments) {
        setReadStateOverrides(new Map());
        readStateBaselineRef.current = new Map();
      }
    } catch (err) {
      console.error('Failed to load live data', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const openMobileSidebar = useCallback(() => {
    if (isDesktopRef.current || routeArticleId) return;
    if (isMobileSidebarOpenRef.current) return;
    flushSync(() => {
      setExitConfirmVisible(false);
      setIsMobileSidebarOpen(true);
    });
    isMobileSidebarOpenRef.current = true;

    if (!window.history.state?.sidebarBackTrap) {
      pushMobileHistoryState({ mobileBackCatcher: true, sidebarBackTrap: true });
    }
  }, [routeArticleId, pushMobileHistoryState]);

  const closeMobileSidebar = useCallback(() => {
    setExitConfirmVisible(false);
    setIsMobileSidebarOpen(false);
    isMobileSidebarOpenRef.current = false;

    if (!isDesktopRef.current && !routeArticleId && window.history.state?.sidebarBackTrap) {
      skipNextPopRef.current = true;
      window.history.back();
    }
  }, [routeArticleId]);

  // Intercept OS/browser back presses on mobile
  useEffect(() => {
    const handlePopState = () => {
      if (isDesktop || !isAuthenticated) return;

      if (skipNextPopRef.current) {
        skipNextPopRef.current = false;
        return;
      }

      if (isLeavingRef.current) {
        isLeavingRef.current = false;
        return; // Allow natural exit
      }

      if (routeArticleId) {
        // Back from Article View (Pane 3) — marks read and returns to Feed
        return;
      }

      if (isMobileSidebarOpenRef.current) {
        setExitConfirmVisible(true);
        pushMobileHistoryState({ mobileBackCatcher: true, sidebarBackTrap: true });
        return;
      }

      openMobileSidebar();
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [isAuthenticated, isDesktop, routeArticleId, openMobileSidebar, pushMobileHistoryState]);

  // Some mobile/browser back paths still skip same-document popstate and go straight to unload.
  // When Pane 1 is open, use the browser-native unload confirmation as a fallback.
  useEffect(() => {
    if (!isAuthenticated || isDesktop || routeArticleId || !isMobileSidebarOpen) return;

    const handleBeforeUnloadConfirm = (e) => {
      if (isLeavingRef.current) return;
      e.preventDefault();
      e.returnValue = '';
      return '';
    };

    window.addEventListener('beforeunload', handleBeforeUnloadConfirm);
    return () => window.removeEventListener('beforeunload', handleBeforeUnloadConfirm);
  }, [isAuthenticated, isDesktop, routeArticleId, isMobileSidebarOpen]);

  // Apply saved layout widths from localStorage to CSS variables on mount
  useEffect(() => {
    const sw = localStorage.getItem('reader_sidebar_width');
    const fw = localStorage.getItem('reader_feed_width');
    if (sw) document.documentElement.style.setProperty('--sidebar-width', sw);
    if (fw) document.documentElement.style.setProperty('--feed-width', fw);
  }, []);

  useEffect(() => {
    setOnAuthFailure(() => {
      setIsAuthenticated(false);
      setError('Session expired. Please login again.');
    });
  }, []);

  useEffect(() => {
    const handleResize = () => setIsDesktop(window.innerWidth >= 768);
    window.addEventListener('resize', handleResize);
    
    // Request persistent storage if available to support a large offline library
    if (navigator.storage && navigator.storage.persist) {
      navigator.storage.persist().then(persistent => {
        if (persistent) {
          console.log('Storage will not be cleared except by explicit user action.');
        } else {
          console.log('Storage may be cleared by the UA under storage pressure.');
        }
      });
    }

    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    const displayModeQuery = window.matchMedia('(display-mode: standalone)');
    const syncInstallState = () => {
      setIsInstallStandalone(displayModeQuery.matches || window.navigator.standalone === true);
    };

    const handleBeforeInstallPrompt = (event) => {
      event.preventDefault();
      setDeferredInstallPrompt(event);
    };

    const handleAppInstalled = () => {
      setDeferredInstallPrompt(null);
      setIsInstallPromptPending(false);
      syncInstallState();
    };

    syncInstallState();
    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
    window.addEventListener('appinstalled', handleAppInstalled);
    displayModeQuery.addEventListener('change', syncInstallState);

    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
      window.removeEventListener('appinstalled', handleAppInstalled);
      displayModeQuery.removeEventListener('change', syncInstallState);
    };
  }, []);

  useEffect(() => {
    return () => {
      if (feedNavigationTimerRef.current) {
        clearTimeout(feedNavigationTimerRef.current);
      }
    };
  }, []);


  const withTimeout = (promise, ms = 12000) => {
    return Promise.race([
      promise,
      new Promise((_, reject) => setTimeout(() => reject(new Error('Timed out')), ms))
    ]);
  };

  const loadDataFromDB = async (clearAdjustments = false, timeoutMs = 12000) => {
    setLoading(true);
    try {
      const [dbFeeds, dbArticles, dbSettings] = await Promise.all([
        withTimeout(getFeedsDataFromDB(), timeoutMs),
        withTimeout(getArticlesFromDB(), timeoutMs),
        withTimeout(getSettings())
      ]);
      setFeedsData(dbFeeds);
      setArticles(dbArticles);
      setSettings(dbSettings);
      if (clearAdjustments) {
        setReadStateOverrides(new Map());
        readStateBaselineRef.current = new Map();
      }
    } catch (err) {
      console.error('Failed to load from DB', err);
      // IndexedDB can occasionally fail to initialize on some browsers/devices.
      // Keep the app usable by falling back to live API data.
      if (typeof navigator !== 'undefined' && navigator.onLine) {
        try {
          const retentionDays = Number(localStorage.getItem('reader_retention_days') || 14);
          const since = new Date(Date.now() - (retentionDays * 24 * 60 * 60 * 1000)).toISOString();
          const [liveFeeds, liveArticles] = await Promise.all([
            getFeeds(since),
            getArticles(null, 200)
          ]);
          setFeedsData(liveFeeds || { categories: [], total_unread: 0 });
          setArticles(liveArticles?.articles || []);
        } catch (apiErr) {
          console.error('Live fallback load failed', apiErr);
        }
      }
    } finally {
      setLoading(false);
    }
  };

  const applySyncedData = useCallback(async ({ feedsData: nextFeedsData, articles: nextArticles }) => {
    setFeedsData(nextFeedsData || { categories: [], total_unread: 0 });
    setArticles(nextArticles || []);
    setLoading(false);

    try {
      const latestSettings = await getSettings();
      setSettings(latestSettings);
    } catch (err) {
      console.warn('Failed to refresh settings after sync', err);
    }

    setReadStateOverrides(new Map());
    readStateBaselineRef.current = new Map();
  }, []);

  // The AI master switch lives on the server. Holding it in state as well as in the
  // localStorage cache is what re-renders the feed when it flips, so the ranking
  // controls appear or disappear without a manual reload.
  const [aiEnabled, setAiEnabled] = useState(isAiEnabled());

  useEffect(() => {
    if (!isAuthenticated) return;
    let cancelled = false;
    const refresh = () => {
      fetchAiConfig().then(value => {
        if (!cancelled) setAiEnabled(value);
      });
    };
    refresh();
    window.addEventListener('focus', refresh);
    return () => {
      cancelled = true;
      window.removeEventListener('focus', refresh);
    };
  }, [isAuthenticated]);

  useEffect(() => {
    if (!isAuthenticated) return;
    if (isPureLiveMode) {
      loadLiveData(true);
      return;
    }

    const canReachBackend = async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);
        const resp = await fetch(`${API_URL}/health`, { signal: controller.signal, cache: 'no-store' });
        clearTimeout(timeoutId);
        return resp.ok;
      } catch (err) {
        return false;
      }
    };

    const bootSync = async () => {
      // Start local DB hydration, but don't let a slow/empty IndexedDB block first paint.
      const initialLoadPromise = loadDataFromDB(false, 1500);
      const syncOnStartup = localStorage.getItem('reader_sync_on_startup') !== 'false';
      const isOnline = typeof navigator !== 'undefined' && navigator.onLine;
      const canSync = isOnline || await canReachBackend();
      if (!canSync) {
        await initialLoadPromise;
        if (offlineCachingEnabled) {
          applyLocalCachePolicy().catch(err => console.warn('Startup cache cleanup failed:', err));
        }
        return;
      }

      if (syncOnStartup) {
        await performSync({
          onDataReady: async (data) => {
            await applySyncedData(data);
          }
        });
        await initialLoadPromise.catch(() => {});
        await loadDataFromDB(true);
        return;
      }

      const [dbFeeds, dbArticles] = await Promise.all([getFeedsDataFromDB(), getArticlesFromDB()]);
      const hasData = (dbFeeds?.categories?.length || 0) > 0 || (dbArticles?.length || 0) > 0;
      if (!hasData) {
        await performSync({
          onDataReady: async (data) => {
            await applySyncedData(data);
          }
        });
        await loadDataFromDB(true);
      } else {
        await initialLoadPromise.catch(() => {});
        if (offlineCachingEnabled) {
          applyLocalCachePolicy().catch(err => console.warn('Startup cache cleanup failed:', err));
        }
      }
    };

    bootSync();
  }, [isAuthenticated, isPureLiveMode, loadLiveData, offlineCachingEnabled, performSync, applySyncedData]);

  // Re-load from DB when sync finishes to show new data
  // Skip the first run (syncing starts as false on mount — bootSync handles the initial load)
  const syncingEffectMounted = useRef(false);
  useEffect(() => {
    if (!syncingEffectMounted.current) {
      syncingEffectMounted.current = true;
      return;
    }
    if (!isPureLiveMode && isAuthenticated && !syncing) {
      loadDataFromDB(true); // Clear local adjustments atomically with new data load
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPureLiveMode, syncing]);

  // Reload from DB when a background sync completes while the app is open
  useEffect(() => {
    if (!offlineCachingEnabled || !isAuthenticated) return;
    if (!('serviceWorker' in navigator)) return;
    const handleSWMessage = (event) => {
      if (event.data?.type === 'BACKGROUND_SYNC_COMPLETE') {
        console.log('[App] Background sync complete, reloading from DB');
        loadDataFromDB(true);
      }
    };
    navigator.serviceWorker.addEventListener('message', handleSWMessage);
    return () => navigator.serviceWorker.removeEventListener('message', handleSWMessage);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offlineCachingEnabled, isAuthenticated]);

  const handleLoginSuccess = () => {
    setIsAuthenticated(true);
    setIsMobileSidebarOpen(false);
    isMobileSidebarOpenRef.current = false;
    setExitConfirmVisible(false);
    setPane2ContextFeedId(routeContextFeedId);
    setSessionReadIds(new Set());
    setHiddenReadIds(new Set());
    setReadStateOverrides(new Map());
    readStateBaselineRef.current = new Map();
    setError('');

    // Fresh-login mobile "All Articles" lands directly on Pane 2, so add the
    // same back-trap entry used by the normal Pane 1 -> Pane 2 flow.
    if (
      window.innerWidth < 768 &&
      routeContextFeedId === null &&
      !routeArticleId &&
      !window.history.state?.sidebarBackTrap
    ) {
      pushMobileHistoryState({ mobileBackCatcher: true, sidebarBackTrap: true });
    }
  };

  const handleSelectFeed = useCallback((id) => {
    setError('');
    setExitConfirmVisible(false);
    setDisplayLimit(20);
    if (feedNavigationTimerRef.current) {
      clearTimeout(feedNavigationTimerRef.current);
      feedNavigationTimerRef.current = null;
    }
    
    const destPath = getFeedPath(id);
    const leavingFeedId = pane2ContextFeedId;

    if (isDesktop) {
      flushSync(() => {
        setPane2ContextFeedId(id);
        hideReadArticlesForLeavingFeed(leavingFeedId);
        setIsMobileSidebarOpen(false);
      });
      isMobileSidebarOpenRef.current = false;

      navigate(destPath);
      return;
    }

    if (isMobileSidebarOpen) {
      flushSync(() => {
        hideReadArticlesForLeavingFeed(leavingFeedId);
        setPane2ContextFeedId(id);
      });
      // Keep the sidebar history entry aligned with the selected feed route.
      if (window.history.state?.sidebarBackTrap) {
        window.history.replaceState(
          { mobileBackCatcher: true, sidebarBackTrap: true },
          '',
          destPath
        );
      }
      feedNavigationTimerRef.current = setTimeout(() => {
        navigate(destPath);
        // Close sidebar after the delayed navigate so Pane 1 had time to
        // render the selected feed as active before we move to Pane 2.
        setIsMobileSidebarOpen(false);
        isMobileSidebarOpenRef.current = false;
        feedNavigationTimerRef.current = null;
      }, MOBILE_FEED_NAV_DELAY_MS);
      return;
    }

    flushSync(() => {
      setPane2ContextFeedId(id);
      hideReadArticlesForLeavingFeed(leavingFeedId);
    });
    feedNavigationTimerRef.current = setTimeout(() => {
      navigate(destPath);
      feedNavigationTimerRef.current = null;
    }, MOBILE_FEED_NAV_DELAY_MS);
  }, [getFeedPath, hideReadArticlesForLeavingFeed, isDesktop, isMobileSidebarOpen, navigate, pane2ContextFeedId]);

  const handleSelectCategory = useCallback((catId) => {
    setError('');
    setDisplayLimit(20);
    exhaustedFetchesRef.current.clear();
    setSelectedCategoryId(catId || 'all');
    handleSelectFeed(null);
  }, [handleSelectFeed]);

  useEffect(() => {
    if (!routeArticleId) {
      setPane2ContextFeedId(routeContextFeedId);
    }
  }, [routeArticleId, routeContextFeedId]);

  useEffect(() => {
    if (!isAuthenticated || pane2ContextFeedId !== null) return;
    hydrateAllSources();
  }, [hydrateAllSources, isAuthenticated, pane2ContextFeedId]);

  const handleExitConfirmLeave = useCallback(() => {
    setExitConfirmVisible(false);
    setIsMobileSidebarOpen(false);
    isMobileSidebarOpenRef.current = false;
    skipNextPopRef.current = true;
    isLeavingRef.current = true;
    window.history.go(-2);
  }, []);

  const handleSelectArticle = useCallback((id) => {
    if (selectedArticleId === id) return;

    setError('');

    const selected = articles.find(a => a.id === id);
    if (selected) {
      navigate(getArticlePath(selected));
    }
  }, [articles, getArticlePath, selectedArticleId, navigate]);

  // Auto-mark on open. This depends on `articles`, so it also re-runs when the user
  // manually marks the open article unread — autoMarkedIdRef keeps it to one mark per
  // opening so that toggle isn't instantly undone.
  useEffect(() => {
    const activeArticle = articles.find(a => String(a.id) === String(selectedArticleId)) || (fullArticle && String(fullArticle.id) === String(selectedArticleId) ? fullArticle : null);
    const { mark, nextAutoMarkedId } = resolveAutoMarkRead(
      selectedArticleId,
      activeArticle,
      autoMarkedIdRef.current
    );
    autoMarkedIdRef.current = nextAutoMarkedId;
    if (mark) markArticleRead(selectedArticleId);
  }, [articles, fullArticle, markArticleRead, selectedArticleId]);

  // Fetch full article content when selection changes
  useEffect(() => {
    if (!selectedArticleId) {
      setFullArticle(null);
      return;
    }

    // Check if articles list already has full content for this article
    const existing = articles.find(a => a.id === selectedArticleId);
    if (existing && existing.content) {
      setFullArticle({ ...existing, is_cached: true });
      setArticleLoading(false);
      setError('');
      return;
    }

    let cancelled = false;
    setArticleLoading(true);
    setFullArticle(null);
    setError('');

    const loadCachedArticle = async () => {
      try {
        const local = await getArticleFromDB(selectedArticleId);
        if (!cancelled && local && local.content) {
          setFullArticle(prev => {
            const currentIsRead = (prev && String(prev.id) === String(local.id)) ? prev.is_read : undefined;
            const isRead = currentIsRead !== undefined ? (currentIsRead || local.is_read) : (existing?.is_read || local.is_read);
            return { ...local, is_read: isRead ? 1 : 0, is_cached: true };
          });
          setArticles(prev => {
            const exists = prev.some(a => String(a.id) === String(local.id));
            return exists
              ? prev.map(a => String(a.id) === String(local.id) ? { ...a, ...local, is_read: (a.is_read || local.is_read) ? 1 : 0, is_cached: true } : a)
              : [{ ...local, is_cached: true }, ...prev];
          });
          setError('');
          return true;
        }
      } catch (dbErr) {
        console.error('Local DB fetch also failed', dbErr);
      }

      return false;
    };

    if (!isPureLiveMode && isOffline) {
      loadCachedArticle()
        .then(foundLocal => {
          if (!cancelled && !foundLocal) {
            setError('Failed to load article content (Offline)');
          }
        })
        .finally(() => {
          if (!cancelled) setArticleLoading(false);
        });
      return () => { cancelled = true; };
    }

    getArticle(selectedArticleId)
      .then(data => {
        if (!cancelled && data) {
          setFullArticle(prev => {
            const currentIsRead = (prev && String(prev.id) === String(data.id)) ? prev.is_read : undefined;
            const isRead = currentIsRead !== undefined ? (currentIsRead || data.is_read) : (existing?.is_read || data.is_read);
            return { ...data, is_read: isRead ? 1 : 0, is_cached: false };
          });
          setArticles(prev => {
            const exists = prev.some(a => String(a.id) === String(data.id));
            return exists
              ? prev.map(a => String(a.id) === String(data.id) ? { ...a, ...data, is_read: (a.is_read || data.is_read) ? 1 : 0 } : a)
              : [data, ...prev];
          });
          if (offlineCachingEnabled) {
            saveArticlesToDB([data]).catch(err => console.warn('Failed to persist article to DB', err));
          }
        }
      })
      .catch(async err => {
        if (isPureLiveMode) {
          if (!cancelled) {
            const isOnline = typeof navigator !== 'undefined' && navigator.onLine;
            const is404 = err?.message?.includes('404');
            if (!isOnline) {
              setError('Failed to load article content (Offline)');
            } else if (is404) {
              setError('Article no longer available on server');
            } else {
              setError('Failed to load article — tap Try again');
            }
          }
          return;
        }

        console.warn('Network fetch failed, trying local DB...', err);
        const foundLocal = await loadCachedArticle();
        if (foundLocal) return;

        if (!cancelled) {
          const isOnline = typeof navigator !== 'undefined' && navigator.onLine;
          const is404 = err?.message?.includes('404');
          if (!isOnline) {
            setError('Failed to load article content (Offline)');
          } else if (is404) {
            setError('Article no longer available on server');
          } else {
            setError('Failed to load article — tap Try again');
          }
        }
      })
      .finally(() => { if (!cancelled) setArticleLoading(false); });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedArticleId, articleFetchRevision, isPureLiveMode, offlineCachingEnabled, isOffline]);

  const filteredArticles = useMemo(() => {
    const cutoff = Date.now() - ((settings.retentionDays || 14) * 24 * 60 * 60 * 1000);
    const sessionIdsArr = Array.from(sessionReadIds).map(String);
    const hiddenIdsArr = Array.from(hiddenReadIds).map(String);
    const selIdStr = selectedArticleId ? String(selectedArticleId) : null;

    const kept = articles
      .filter(a => !pane2ContextFeedId || String(a.source_id) === String(pane2ContextFeedId))
      .filter(a => {
        // In offline mode, only show articles with locally cached content
        if (isOffline && !a.content) return false;

        // Apply retention filter: hide articles older than the cutoff
        const artTime = getArticleTimestamp(a);
        if (artTime < cutoff) return false;

        if (showReadArticles) return true;

        const idStr = String(a.id);
        // If it's the currently selected article, always show it to prevent blinking when it's marked as read
        if (idStr === selIdStr) return true;

        if (!a.is_read) return true;
        // If read, only show if it was newly read this session and not yet hidden
        return sessionIdsArr.includes(idStr) && !hiddenIdsArr.includes(idStr);
      });
    return sortForMode(kept);
  }, [articles, pane2ContextFeedId, showReadArticles, selectedArticleId, sessionReadIds, hiddenReadIds, settings.retentionDays, isOffline]);

  useAdjacentPrefetch({ filteredArticles, selectedArticleId, isOffline, enabled: offlineCachingEnabled });

  const liveFeedsData = useMemo(() => {
    if (!feedsData?.categories) return feedsData;

    const reductions = Array.from(readStateOverrides.entries()).reduce((acc, [id, isRead]) => {
      const art = articles.find(a => String(a.id) === String(id));
      if (!art) return acc;

      const srcId = String(art.source_id);
      acc[srcId] = (acc[srcId] || 0) + (isRead ? 1 : -1);
      return acc;
    }, {});

    let totalUnread = 0;
    const categories = feedsData.categories.map(cat => ({
      ...cat,
      feeds: (cat.feeds || []).map(feed => {
        const delta = reductions[String(feed.id)] || reductions[feed.id] || 0;
        const count = Math.max(0, (feed.unread_count || 0) - delta);
        totalUnread += count;
        return { ...feed, unread_count: count };
      })
    }));

    return { ...feedsData, categories, total_unread: totalUnread };
  }, [feedsData, articles, readStateOverrides]);

  const offlineBadgeFeedsData = useMemo(() => {
    if (!feedsData?.categories) return feedsData;

    const cutoff = Date.now() - ((settings.retentionDays || 14) * 24 * 60 * 60 * 1000);
    const unreadCounts = articles.reduce((acc, article) => {
      const articleTime = getArticleTimestamp(article);

      // Only count articles that are actually available offline
      if (!article.content || articleTime < cutoff || article.is_read) {
        return acc;
      }

      acc[article.source_id] = (acc[article.source_id] || 0) + 1;
      return acc;
    }, {});

    let totalUnread = 0;
    const categories = feedsData.categories.map(cat => ({
      ...cat,
      feeds: (cat.feeds || []).map(feed => {
        const count = unreadCounts[feed.id] || 0;
        totalUnread += count;
        return { ...feed, unread_count: count };
      })
    }));

    return { ...feedsData, categories, total_unread: totalUnread };
  }, [feedsData, articles, settings.retentionDays]);

  // Online: server counts adjusted for local reads (authoritative).
  // Offline: locally cached unread counts within retention window.
  const sidebarFeedsData = useMemo(() => {
    if (isOffline) return offlineBadgeFeedsData;
    return liveFeedsData;
  }, [isOffline, liveFeedsData, offlineBadgeFeedsData]);

  // When "hide sources with no unread" is on, drop 0-unread feeds from the
  // sidebar (keeping the currently-selected one so it doesn't vanish mid-read)
  // and drop categories left with no visible feeds. total_unread is left intact
  // so the ALL SOURCES badge still reflects the true total.
  const filteredSidebarFeedsData = useMemo(() => {
    if (!hideEmptySources || !sidebarFeedsData?.categories) return sidebarFeedsData;
    const categories = sidebarFeedsData.categories
      .map(cat => ({
        ...cat,
        feeds: (cat.feeds || []).filter(
          f => (f.unread_count || 0) > 0 || f.id === pane2ContextFeedId
        ),
      }))
      .filter(cat => (cat.feeds || []).length > 0);
    return { ...sidebarFeedsData, categories };
  }, [hideEmptySources, sidebarFeedsData, pane2ContextFeedId]);

  const selectedFeedServerUnreadCount = useMemo(() => {
    if (!pane2ContextFeedId || !feedsData?.categories?.length) return 0;

    return feedsData.categories
      .flatMap(category => category.feeds || [])
      .find(feed => String(feed.id) === String(pane2ContextFeedId))
      ?.unread_count || 0;
  }, [feedsData, pane2ContextFeedId]);

  const displayedArticles = filteredArticles.slice(0, displayLimit);

  // The Sources Index filters by category client-side over the loaded window, so a
  // category whose articles all sit past that window renders empty while its badge
  // shows a healthy server count. Scope pagination to the category to close the gap.
  // Gated on the index view because pane 2 deliberately ignores the category pills:
  // scoping its pagination would stall its scroll once the category ran out.
  const activeCategoryId = (isIndexViewMode && !pane2ContextFeedId && selectedCategoryId && selectedCategoryId !== 'all')
    ? selectedCategoryId
    : null;

  const activeCategoryFeedIds = useMemo(() => {
    if (!activeCategoryId || !feedsData?.categories?.length) return null;
    const cat = feedsData.categories.find(
      c => String(c.id ?? 'uncat') === String(activeCategoryId)
    );
    if (!cat) return null;
    return new Set((cat.feeds || []).map(f => String(f.id)));
  }, [feedsData, activeCategoryId]);

  const activeCategoryServerUnreadCount = useMemo(() => {
    if (!activeCategoryId || !feedsData?.categories?.length) return 0;
    const cat = feedsData.categories.find(
      c => String(c.id ?? 'uncat') === String(activeCategoryId)
    );
    return (cat?.feeds || []).reduce((acc, f) => acc + (f.unread_count || 0), 0);
  }, [feedsData, activeCategoryId]);

  // How many of the loaded articles the index is actually able to show right now.
  // This is the correct offset for a category-scoped fetch — the global length would
  // skip straight past the articles we are trying to reach.
  const loadedInScopeCount = useMemo(() => {
    if (!activeCategoryFeedIds) return filteredArticles.length;
    return filteredArticles.filter(a => activeCategoryFeedIds.has(String(a.source_id))).length;
  }, [filteredArticles, activeCategoryFeedIds]);

  // `force` skips the in-memory shortcut: the Sources Index paginates with its own
  // counter, so displayLimit is irrelevant to it and would stall the server fetch.
  const handleLoadMore = useCallback(async (force = false) => {
    if (loading || isFetchingMoreRef.current) return;
    if (typeof navigator !== 'undefined' && !navigator.onLine) return;

    // If we have more articles in memory already, just show them
    if (!force && displayLimit < filteredArticles.length) {
      setDisplayLimit(prev => prev + 20);
      return;
    }

    // If we've reached the end of local articles, but server count says more, fetch from server
    // For now, let's keep it simple and just increase the limit if possible, 
    // but the user wants to avoid overloading the server.
    // If we reached here, it means filteredArticles.length matches what's in memory.
    // If the sidebar count is higher, we might need a dynamic fetch.
    
    // Check if we should fetch more from server
    // (This part is complex because articles are synced background-ly, but we can do a targeted fetch)
    // Finding the total count for the current view:
    let totalExpected = 0;
    if (pane2ContextFeedId) {
      totalExpected = selectedFeedServerUnreadCount;
    } else if (activeCategoryId) {
      totalExpected = activeCategoryServerUnreadCount;
    } else {
      totalExpected = feedsData.total_unread;
    }

    if (loadedInScopeCount >= totalExpected && totalExpected > 0) return;

    const fetchKey = `${pane2ContextFeedId || ''}|${activeCategoryId || ''}|${loadedInScopeCount}`;
    if (exhaustedFetchesRef.current.has(fetchKey)) return;

    setIsFetchingMore(true);
    try {
      const resp = await getArticles(null, 50, loadedInScopeCount, pane2ContextFeedId, null, activeCategoryId);
      if (resp.articles.length > 0) {
        if (offlineCachingEnabled) {
          await saveArticlesToDB(resp.articles);
        }
        // Decided against the count inside the updater below: React 18 runs that
        // during render, so reading a variable it assigns is a race.
        if (resp.articles.every(a => loadedArticleIdsRef.current.has(String(a.id)))) {
          exhaustedFetchesRef.current.add(fetchKey);
        }
        setArticles(prev => {
          // Merge avoiding duplicates
          const existingIds = new Set(prev.map(a => a.id));
          const newOnes = resp.articles.filter(a => !existingIds.has(a.id));
          // Appending is already correct for smart/random: page 2 follows page 1
          // in the order the server ranked them.
          return sortForMode([...prev, ...newOnes]);
        });
        setDisplayLimit(prev => prev + 20);

        // Refresh feed counts — the badge may be stale if new articles arrived
        // on the server since the last sync (e.g. scheduler ran after getFeeds() was called).
        try {
          const retentionMs = (settings.retentionDays || 14) * 24 * 60 * 60 * 1000;
          const since = new Date(Date.now() - retentionMs).toISOString();
          const freshFeeds = await getFeeds(since);
          if (freshFeeds) setFeedsData(freshFeeds);
        } catch (err) {
          // Non-critical — badge may show stale count until next sync
        }
      } else {
        exhaustedFetchesRef.current.add(fetchKey);
      }
    } catch (err) {
      console.error('Failed to fetch more articles', err);
    } finally {
      setIsFetchingMore(false);
    }
  }, [displayLimit, filteredArticles.length, loadedInScopeCount, loading, pane2ContextFeedId, feedsData, selectedFeedServerUnreadCount, activeCategoryId, activeCategoryServerUnreadCount, offlineCachingEnabled, settings.retentionDays]);

  const selectedArticleIndex = filteredArticles.findIndex(a => String(a.id) === String(selectedArticleId));
  const selectedArticleMeta = filteredArticles[selectedArticleIndex];

  // Pane 2 slices to displayLimit in date order, but the Sources Index presents
  // articles shuffled — so an article opened from it is usually outside that window
  // and never renders, which is why it showed no highlight. Grow the window to reach it.
  useEffect(() => {
    if (selectedArticleIndex < 0) return;
    setDisplayLimit(prev => displayLimitCovering(prev, selectedArticleIndex));
  }, [selectedArticleIndex]);
  const hasRemoteMore = pane2ContextFeedId
    ? filteredArticles.length < selectedFeedServerUnreadCount
    : activeCategoryId
      ? loadedInScopeCount < activeCategoryServerUnreadCount
      : filteredArticles.length < (feedsData?.total_unread || 0);
  const hasMoreForFeed = displayLimit < filteredArticles.length || (!isOffline && hasRemoteMore);

  // Stable identity: the index's IntersectionObserver re-subscribes on every change
  // to this prop, so an inline arrow here would rebuild the observer each render.
  const handleIndexLoadMore = useCallback(() => handleLoadMore(true), [handleLoadMore]);

  // When a feed is selected but has no local articles, proactively fetch from server.
  // This handles feeds whose articles were never synced or were pruned locally.
  useEffect(() => {
    if (loading || isFetchingMore || !feedsData.categories.length) return;

    let feedUnreadCount;
    let needsInitialFeedBackfill;
    if (pane2ContextFeedId) {
      feedUnreadCount = feedsData.categories.flatMap(c => c.feeds).find(f => f.id === pane2ContextFeedId)?.unread_count || 0;
      needsInitialFeedBackfill = filteredArticles.length < Math.min(feedUnreadCount, 50);
    } else if (activeCategoryId) {
      feedUnreadCount = activeCategoryServerUnreadCount;
      needsInitialFeedBackfill = loadedInScopeCount < Math.min(feedUnreadCount, 50);
    } else {
      feedUnreadCount = feedsData.total_unread;
      needsInitialFeedBackfill = filteredArticles.length === 0;
    }

    if (needsInitialFeedBackfill && feedUnreadCount > 0) {
      if (navigator.onLine) {
        handleLoadMore();
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pane2ContextFeedId, filteredArticles.length, loadedInScopeCount, activeCategoryId, activeCategoryServerUnreadCount, feedsData]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (!isDesktop || !selectedArticleId) return;

      // Skip shortcuts if user is typing in an input or textarea
      const target = e.target;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
        return;
      }

      if (e.key === 'd' || e.key === 'ArrowRight') {
        if (selectedArticleIndex < filteredArticles.length - 1) {
          handleSelectArticle(filteredArticles[selectedArticleIndex + 1].id);
        }
      } else if (e.key === 'a' || e.key === 'ArrowLeft') {
        if (selectedArticleIndex > 0) {
          handleSelectArticle(filteredArticles[selectedArticleIndex - 1].id);
        }
      } else if (e.key === 'r' && selectedArticleMeta) {
        toggleReadManual(selectedArticleMeta.id, selectedArticleMeta.is_read);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isDesktop, selectedArticleId, selectedArticleIndex, filteredArticles, selectedArticleMeta, toggleReadManual, handleSelectArticle]);

  const retryArticle = useCallback(() => {
    setError('');
    setArticleFetchRevision(r => r + 1);
  }, []);

  const handleHideArticle = useCallback((id) => {
    setHiddenReadIds(prev => new Set(prev).add(id));
  }, []);

  const navigateToPrev = useCallback(() => {
    if (selectedArticleIndex > 0) {
      const a = filteredArticles[selectedArticleIndex - 1];
      navigate(getArticlePath(a), { replace: true });
    } else {
      showToast('First article');
    }
  }, [selectedArticleIndex, filteredArticles, getArticlePath, navigate, showToast]);

  const navigateToNext = useCallback(() => {
    if (selectedArticleIndex < filteredArticles.length - 1) {
      const a = filteredArticles[selectedArticleIndex + 1];
      navigate(getArticlePath(a), { replace: true });
    } else {
      showToast('Last article');
    }
  }, [selectedArticleIndex, filteredArticles, getArticlePath, navigate, showToast]);

  const handleUpdateFeedViewMode = useCallback(async (feedId, newMode) => {
    try {
      const field = isDesktop ? 'desktop_view_mode' : 'mobile_view_mode';
      await patchFeedSource(feedId, { [field]: newMode });
      
      // Update local state for immediate feedback
      setFeedsData(prev => ({
        ...prev,
        categories: prev.categories.map(cat => ({
          ...cat,
          feeds: (cat.feeds || []).map(f => 
            f.id === feedId ? { ...f, [field]: newMode } : f
          )
        }))
      }));
    } catch (err) {
      console.error('Failed to update view mode', err);
      setError('Failed to save view preference');
    }
  }, [isDesktop]);

  const handleBulkMarkRead = useCallback(async (sourceId) => {
    try {
      const payload = sourceId ? { source_id: sourceId } : { mark_all: true };
      await bulkMarkRead(payload);
      
      // Update local React state instantly
      setArticles(prev => prev.map(a => {
        if (!sourceId || a.source_id === sourceId) {
          return { ...a, is_read: 1 };
        }
        return a;
      }));

      // Persist to IndexedDB
      if (offlineCachingEnabled) {
        const dbArticles = await getArticlesFromDB();
        const itemsToUpdate = dbArticles
          .filter(a => !sourceId || a.source_id === sourceId)
          .map(a => ({ ...a, is_read: 1 }));

        if (itemsToUpdate.length > 0) {
          await saveArticlesToDB(itemsToUpdate);
        }

        performSync();
      } else {
        const retentionMs = (settings.retentionDays || 14) * 24 * 60 * 60 * 1000;
        const since = new Date(Date.now() - retentionMs).toISOString();
        const freshFeeds = await getFeeds(since);
        if (freshFeeds) setFeedsData(freshFeeds);
      }
    } catch (err) {
      console.error('Failed bulk mark read', err);
      setError('Failed to mark articles as read');
    }
  }, [offlineCachingEnabled, performSync, settings.retentionDays]);

  const handleInstallApp = useCallback(async () => {
    if (!deferredInstallPrompt) return;

    setIsInstallPromptPending(true);
    try {
      await deferredInstallPrompt.prompt();
      await deferredInstallPrompt.userChoice;
    } catch (err) {
      console.warn('Install prompt failed', err);
    } finally {
      setDeferredInstallPrompt(null);
      setIsInstallPromptPending(false);
    }
  }, [deferredInstallPrompt]);

  // Must stay above the isAuthenticated early return: hooks declared after it are
  // skipped while logged out, so logging in changes the hook count mid-session.
  const activeCategoryName = useMemo(() => {
    if (!selectedCategoryId || selectedCategoryId === 'all') return 'All Articles';
    const cat = liveFeedsData?.categories?.find(
      c => (c.id || 'uncat') === selectedCategoryId || String(c.id) === String(selectedCategoryId)
    );
    return cat ? (cat.name || 'Uncategorized') : 'All Articles';
  }, [selectedCategoryId, liveFeedsData]);

  if (!isAuthenticated) {
    return <Login onAuth={handleLoginSuccess} />;
  }

  const selectedFeed = liveFeedsData?.categories?.flatMap(c => c?.feeds || [])?.find(f => f?.id === pane2ContextFeedId);
  const unreadCount = pane2ContextFeedId ? selectedFeed?.unread_count : liveFeedsData?.total_unread;
  const currentViewMode = pane2ContextFeedId
    ? (isDesktop ? selectedFeed?.desktop_view_mode : selectedFeed?.mobile_view_mode) || 'standard'
    : allArticlesViewMode;

  const toggleViewMode = () => {
    const nextMode = currentViewMode === 'standard' ? 'full_image' : 'standard';
    if (pane2ContextFeedId) {
      handleUpdateFeedViewMode(pane2ContextFeedId, nextMode);
    } else {
      setAllArticlesViewMode(nextMode);
      localStorage.setItem('reader_all_articles_view_mode', nextMode);
    }
  };


  return (
    <>
      <div className="app-container">
      {(!selectedArticleId || isDesktop) && (
        <div className="mobile-top-bar">
          {cacheProgress > 0 && offlineCachingEnabled && (
            <div
              className="mobile-cache-progress-bar"
              style={{
                width: `${cacheProgress}%`,
                backgroundColor: cacheProgress >= 100 ? 'var(--accent)' : '#4caf50',
              }}
            />
          )}
          <button className="mobile-menu-btn" onClick={openMobileSidebar}>
            <span className="material-symbols-outlined">menu</span>
          </button>

          <div className="mobile-title-group">
            <span className="mobile-title-text">
              {isIndexViewMode
                ? (pane2ContextFeedId ? (selectedFeed?.name || 'Feed Index') : activeCategoryName)
                : (pane2ContextFeedId ? (selectedFeed?.name || 'Articles') : activeCategoryName)}
              {window.location.hostname === 'localhost' && <span className="dev-badge">LOCAL</span>}
            </span>
            {unreadCount > 0 && <div className="header-unread-badge">{unreadCount}</div>}
            {!isIndexViewMode && (
              <button
                className="view-mode-btn"
                onClick={(e) => { e.stopPropagation(); toggleViewMode(); }}
                title={`Switch to ${currentViewMode === 'standard' ? 'Full Image' : 'Standard'} View`}
              >
                <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                  {currentViewMode === 'standard' ? 'image' : 'view_stream'}
                </span>
              </button>
            )}
          </div>

          {!isPureLiveMode && !isOffline && (
            <button className="mobile-sync-btn" onClick={performSync} disabled={syncing}>
              <span className={`material-symbols-outlined ${syncing ? 'spinning' : ''}`}>refresh</span>
            </button>
          )}
        </div>
      )}

      <Sidebar
        feedsData={filteredSidebarFeedsData}
        selectedFeedId={pane2ContextFeedId}
        onSelectFeed={(feedId) => {
          handleSelectFeed(feedId);
        }}
        selectedCategoryId={selectedCategoryId}
        onSelectCategory={handleSelectCategory}
        isOpen={isMobileSidebarOpen}
        onClose={closeMobileSidebar}
        onOpenSettings={() => { closeMobileSidebar(); setShowSettings(true); }}
        syncing={syncing}
        lastSyncTime={lastSyncTime}
        performSync={performSync}
        cacheProgress={cacheProgress}
        onToggleSidebar={handleToggleSidebar}
        isSidebarCollapsed={isSidebarCollapsed}
        isDesktop={isDesktop}
        onBulkMarkRead={handleBulkMarkRead}
        isOffline={isOffline}
        isForcedOffline={isForcedOffline}
        toggleForcedOffline={toggleForcedOffline}
      />
      
      {!isSidebarCollapsed && isDesktop && <Resizer varName="--sidebar-width" defaultWidth={256} minWidth={150} maxWidth={400} />}

      {isIndexViewMode ? (
        <div style={{ 
          display: (!isDesktop && (selectedArticleId || isMobileSidebarOpen)) ? 'none' : 'flex',
          flex: 1,
          height: '100%', 
          overflow: 'hidden' 
        }}>
          <SourcesIndex
            feedsData={liveFeedsData}
            articles={filteredArticles}
            onSelectFeed={handleSelectFeed}
            activeFeedId={pane2ContextFeedId}
            onClearActiveFeed={() => handleSelectFeed(null)}
            selectedCategory={selectedCategoryId}
            onSelectCategory={handleSelectCategory}
            onSelectArticle={handleSelectArticle}
            onBulkMarkRead={handleBulkMarkRead}
            onToggleRead={(id, isRead) => toggleReadManual(id, isRead)}
            onVoted={handleVoted}
            hasMore={!isOffline && hasRemoteMore}
            onLoadMore={handleIndexLoadMore}
            showOnlyUnread={showOnlyUnread}
            setShowOnlyUnread={setShowOnlyUnread}
          />
        </div>
      ) : (
        <>
          <div style={{ 
            display: (!isDesktop && (selectedArticleId || isMobileSidebarOpen)) ? 'none' : 'flex',
            flex: isDesktop ? 'none' : 1,
            width: isDesktop ? 'var(--feed-width)' : 'auto',
            flexShrink: 0,
            height: '100%', 
            overflow: 'hidden' 
          }}>
            <ArticleFeed
              articles={displayedArticles}
              hasMore={hasMoreForFeed}
              onLoadMore={handleLoadMore}
              selectedArticleId={selectedArticleId}
              onSelectArticle={handleSelectArticle}
              loading={loading && !articles.length}
              isFetchingMore={isFetchingMore}
              selectedFeedId={pane2ContextFeedId}
              feedsData={liveFeedsData}
              isDesktop={isDesktop}
              isOffline={isOffline}
              sessionReadIds={sessionReadIds}
              onHideArticle={handleHideArticle}
              onUpdateViewMode={handleUpdateFeedViewMode}
              customViewMode={currentViewMode}
              onVoted={handleVoted}
            />
          </div>

          {isDesktop && <Resizer varName="--feed-width" defaultWidth={500} minWidth={250} maxWidth={800} />}

          <div style={{ 
            display: (!isDesktop && !selectedArticleId) ? 'none' : 'flex', 
            flex: 1, 
            height: '100%', 
            overflow: 'hidden' 
          }}>
            <ArticleReader
              article={fullArticle}
              loading={articleLoading}
              error={error}
              onBack={() => {
                navigate(-1);
              }}
              onSwipeLeft={navigateToNext}
              onSwipeRight={navigateToPrev}
              toastMsg={toastMsg}
              onToastDismiss={() => setToastMsg(null)}
              onToggleRead={() => {
                const target = (fullArticle && String(fullArticle.id) === String(selectedArticleId)) ? fullArticle : selectedArticleMeta;
                if (target) toggleReadManual(target.id, target.is_read);
              }}
              onRetry={retryArticle}
              isDesktop={isDesktop}
              articles={filteredArticles}
              isOffline={isOffline}
              onVoted={handleVoted}
            />
          </div>
        </>
      )}

      {showSettings && <Settings version={SYSTEM_VERSION} onClose={() => { 
        const nextOfflineCachingEnabled = isOfflineCachingEnabled();
        setShowSettings(false);
        setOfflineCachingEnabled(nextOfflineCachingEnabled);
        setShowReadArticles(localStorage.getItem('reader_show_read') === 'true');
        setHideEmptySources(localStorage.getItem('reader_hide_empty_sources') === 'true');
        if (nextOfflineCachingEnabled) {
          performSync();
        } else {
          loadLiveData(true);
        }
      }}
      showInstallButton={navigator.maxTouchPoints > 0 && !isInstallStandalone && !!deferredInstallPrompt}
      onInstallApp={handleInstallApp}
      installPending={isInstallPromptPending}
      categories={feedsData?.categories || []}
      aiEnabled={aiEnabled}
      />}

      </div>


      {toastMsg && <div className="app-toast">{toastMsg}</div>}
      {exitConfirmVisible && !isDesktop && (
        <div className="exit-confirm-backdrop" onClick={() => setExitConfirmVisible(false)}>
          <div className="exit-confirm-card" onClick={(e) => e.stopPropagation()}>
            <div className="exit-confirm-content">
              <h3>Leave Reader?</h3>
              <p>Use Stay to keep browsing, or Leave to exit the reader.</p>
            </div>
            <div className="exit-confirm-actions">
              <button className="exit-confirm-btn stay" onClick={() => setExitConfirmVisible(false)}>
                Stay
              </button>
              <button className="exit-confirm-btn leave" onClick={handleExitConfirmLeave}>
                Leave
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default App;
