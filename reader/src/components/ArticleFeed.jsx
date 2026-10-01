import React, { useEffect, useRef, useCallback, useMemo } from 'react';
import ArticleCard from './ArticleCard';
import SwipeRow from './SwipeRow';
import { API_URL, getSortMode } from '../api';
import { selectTopPicks } from '../utils/topPicks';
import { useImpressions } from '../hooks/useImpressions';
import '../styles/feed.css';

export default function ArticleFeed({ 
  articles, 
  rankPool,
  selectedArticleId, 
  onSelectArticle, 
  loading, 
  hasMore, 
  onLoadMore, 
  isFetchingMore,
  selectedFeedId,
  feedsData,
  isDesktop,
  isOffline,
  sessionReadIds,
  onHideArticle,
  onUpdateViewMode,
  customViewMode,
  onVoted,
  largeTitle,
  largeTitleSub,
  onLargeTitleVisibility,
  onRowToggleRead,
  onRowHide,
}) {
  useImpressions();
  const loaderRef = useRef(null);
  const containerRef = useRef(null);
  const timersRef = useRef(new Map()); // id -> timeoutId
  const isFetchingMoreRef = useRef(isFetchingMore);
  useEffect(() => { isFetchingMoreRef.current = isFetchingMore; }, [isFetchingMore]);

  const sortMode = getSortMode();
  // Phones only, and only under smart sort: the other orders have no score to rank by.
  const topPicks = useMemo(
    () => (!isDesktop && sortMode === 'smart' && selectedFeedId !== 'saved' ? selectTopPicks(articles, rankPool) : null),
    [articles, rankPool, isDesktop, sortMode, selectedFeedId]
  );

  // Phones: the large title scrolls away with the list, and the app bar shows the
  // title small once it has gone.
  const largeTitleRef = useRef(null);
  const hasList = articles.length > 0;
  useEffect(() => {
    const el = largeTitleRef.current;
    if (!onLargeTitleVisibility) return undefined;
    if (!el || !containerRef.current) { onLargeTitleVisibility(true); return undefined; }
    const observer = new IntersectionObserver(
      ([entry]) => onLargeTitleVisibility(entry.isIntersecting),
      { root: containerRef.current, threshold: 0 }
    );
    observer.observe(el);
    return () => { observer.disconnect(); onLargeTitleVisibility(true); };
  }, [onLargeTitleVisibility, hasList, isDesktop, largeTitle]);

  const handleCardClick = useCallback((id) => {
    onSelectArticle(id);
  }, [onSelectArticle]);

  const prevArticleIdRef = useRef(null);

  useEffect(() => {
    if (selectedArticleId && containerRef.current) {
      if (prevArticleIdRef.current === selectedArticleId) return;

      setTimeout(() => {
        if (!containerRef.current) return;
        const activeCard = containerRef.current.querySelector(`.article-card[data-id="${selectedArticleId}"]`);
        if (activeCard) {
          let behavior = 'smooth';
          let block = 'nearest';

          if (!prevArticleIdRef.current) {
            // Initial view load or transition from Index -> instant snap & centered
            behavior = 'auto';
            block = 'center';
          } else {
            const cardRect = activeCard.getBoundingClientRect();
            const containerRect = containerRef.current.getBoundingClientRect();
            const distance = Math.abs(cardRect.top - containerRect.top);
            if (distance > containerRect.height * 1.5) {
              behavior = 'auto';
              block = 'center';
            }
          }

          activeCard.scrollIntoView({ behavior, block });
          prevArticleIdRef.current = selectedArticleId;
        }
      }, 50);
    } else if (!selectedArticleId) {
      prevArticleIdRef.current = null;
    }
  }, [selectedArticleId]);



  useEffect(() => {
    if (!hasMore || loading || isOffline) return;

    const observer = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && !isFetchingMoreRef.current) {
        onLoadMore();
      }
    }, { threshold: 1.0 });

    if (loaderRef.current) {
      observer.observe(loaderRef.current);
    }

    return () => observer.disconnect();
  }, [hasMore, loading, isOffline, onLoadMore]);



  // Clean up timers on unmount
  useEffect(() => {
    return () => {
      timersRef.current.forEach(timeoutId => clearTimeout(timeoutId));
      timersRef.current.clear();
    };
  }, []);

  if (loading && articles.length === 0) {
    return (
      <div className="feed-container" style={{ alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ color: 'var(--text-muted)' }}>Loading...</p>
      </div>
    );
  }

  if (articles.length === 0 && isFetchingMore) {
    return (
      <div className="feed-container" style={{ alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ color: 'var(--text-muted)' }}>Loading articles...</p>
      </div>
    );
  }

  if (articles.length === 0) {
    const emptyMsg = selectedFeedId === 'saved'
      ? (isDesktop
          ? 'Nothing saved yet. Save an article from the bookmark in the article toolbar.'
          : 'Nothing saved yet. Save an article from its More menu, or the bookmark at the top of the reading page.')
      : 'No articles found.';
    return (
      <div className="feed-container" style={{ alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '0 24px' }}>{emptyMsg}</p>
      </div>
    );
  }

  const selectedFeed = feedsData?.categories?.flatMap(c => c?.feeds || [])?.find(f => f?.id === selectedFeedId);
  const unreadCount = selectedFeedId ? selectedFeed?.unread_count : feedsData?.total_unread;

  const currentViewMode = customViewMode !== undefined
    ? customViewMode
    : (selectedFeedId 
        ? (isDesktop ? selectedFeed?.desktop_view_mode : selectedFeed?.mobile_view_mode) || 'standard'
        : 'standard');

  const toggleViewMode = () => {
    const nextMode = currentViewMode === 'standard' ? 'full_image' : 'standard';
    onUpdateViewMode(selectedFeedId, nextMode);
  };

  return (
    <div className="feed-container" ref={containerRef}>
      <div className="feed-header">
        {selectedFeedId === 'saved' ? (
          <>
            <span className="material-symbols-outlined" style={{ fontSize: '20px', color: 'var(--text-secondary)' }}>
              bookmark
            </span>
            <h2>Saved</h2>
          </>
        ) : selectedFeedId ? (
          <>
            {/* Opening an article by URL renders this before the feed list arrives. An
                unconditional img requested ".../undefined", failed, and onError then
                hid the element for good. */}
            {selectedFeed?.favicon_url && (
              <img
                key={selectedFeed.favicon_url}
                src={`${API_URL}${selectedFeed.favicon_url}`}
                alt=""
                className="feed-header-favicon"
                onError={(e) => e.target.style.display='none'}
              />
            )}
            <h2>{selectedFeed?.name}</h2>
            <button 
              className="view-mode-btn" 
              onClick={(e) => { e.stopPropagation(); toggleViewMode(); }}
              title={`Switch to ${currentViewMode === 'standard' ? 'Full Image' : 'Standard'} View`}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                {currentViewMode === 'standard' ? 'image' : 'view_stream'}
              </span>
            </button>
          </>
        ) : (
          <h2>All articles</h2>
        )}
        {selectedFeedId === 'saved' ? (
          (feedsData?.total_saved || 0) > 0 && <div className="header-unread-badge">{feedsData.total_saved}</div>
        ) : (
          unreadCount > 0 && <div className="header-unread-badge">{unreadCount}</div>
        )}
      </div>
      {!isDesktop && largeTitle && (
        <div className="feed-large-title" ref={largeTitleRef}>
          <h1>{largeTitle}</h1>
          {largeTitleSub && <p>{largeTitleSub}</p>}
        </div>
      )}
      
      {articles.map(article => {
        const card = (
          <ArticleCard
            key={article.id}
            article={article}
            isActive={article.id === selectedArticleId}
            onClick={handleCardClick}
            hideSource={!!selectedFeedId && selectedFeedId !== 'saved'}
            viewMode={currentViewMode}
            featured={topPicks?.has(article.id) ?? false}
            isOffline={isOffline}
            onVoted={onVoted}
          />
        );
        if (isDesktop || !onRowToggleRead) return card;
        return (
          <SwipeRow
            key={article.id}
            isRead={!!article.is_read}
            onToggleRead={() => onRowToggleRead(article.id, article.is_read)}
            onHide={() => onRowHide?.(article.id)}
          >
            {card}
          </SwipeRow>
        );
      })}
      <div ref={loaderRef} className="feed-loader">
        <div className="loader-text">
          {isFetchingMore ? 'Loading more…' : (!hasMore ? '· · ·' : null)}
        </div>
      </div>
    </div>
  );
}
