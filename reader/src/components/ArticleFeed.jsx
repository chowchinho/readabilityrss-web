import React, { useEffect, useRef, useCallback } from 'react';
import ArticleCard from './ArticleCard';
import { API_URL } from '../api';
import { useImpressions } from '../hooks/useImpressions';
import '../styles/feed.css';

export default function ArticleFeed({ 
  articles, 
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
}) {
  useImpressions();
  const loaderRef = useRef(null);
  const containerRef = useRef(null);
  const timersRef = useRef(new Map()); // id -> timeoutId
  const isFetchingMoreRef = useRef(isFetchingMore);
  useEffect(() => { isFetchingMoreRef.current = isFetchingMore; }, [isFetchingMore]);

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
    return (
      <div className="feed-container" style={{ alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ color: 'var(--text-muted)' }}>No articles found.</p>
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
        {selectedFeedId ? (
          <>
            <img 
              src={`${API_URL}${selectedFeed?.favicon_url}`} 
              alt="" 
              className="feed-header-favicon" 
              onError={(e) => e.target.style.display='none'}
            />
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
          <h2>ALL ARTICLES</h2>
        )}
        {unreadCount > 0 && <div className="header-unread-badge">{unreadCount}</div>}
      </div>
      
      {articles.map(article => (
        <ArticleCard
          key={article.id}
          article={article}
          isActive={article.id === selectedArticleId}
          onClick={handleCardClick}
          hideSource={!!selectedFeedId}
          viewMode={currentViewMode}
          isOffline={isOffline}
          onVoted={onVoted}
        />
      ))}
      <div ref={loaderRef} className="feed-loader">
        <div className="loader-text">
          {isFetchingMore ? 'Loading more…' : (!hasMore ? '· · ·' : null)}
        </div>
      </div>
    </div>
  );
}
