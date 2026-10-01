import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import '../styles/sidebar.css';
import SyncStatus from './SyncStatus';
import FeedsScreen from './FeedsScreen';
import { displayCategoryName } from '../utils/articleText';
import { CATEGORY_ORDER_KEY, parseCategoryOrder, sortByCategoryOrder } from '../utils/categoryOrder';

export default function Sidebar({
  feedsData,
  selectedFeedId,
  onSelectFeed,
  selectedCategoryId = 'all',
  onSelectCategory,
  isOpen,
  onOpenSettings,
  syncing,
  lastSyncTime,
  performSync,
  cacheProgress,
  onToggleSidebar,
  isSidebarCollapsed,
  isDesktop,
  onBulkMarkRead,
  isOffline,
  isForcedOffline,
  toggleForcedOffline,
  savedRowOnlyWhenNonempty,
}) {
  const [collapsedCats, setCollapsedCats] = useState({});
  const [theme, setTheme] = useState(localStorage.getItem('reader_theme') || 'light');
  const [hoveredId, setHoveredId] = useState(null);
  const [confirmId, setConfirmId] = useState(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('reader_theme', theme);
  }, [theme]);

  const toggleCategory = (catId) => {
    setCollapsedCats(prev => ({ ...prev, [catId]: !prev[catId] }));
  };

  // Eases the light/dark swap instead of flashing the whole screen at once. The class
  // is on only for the swap, so ordinary interactions keep their own timings.
  const toggleTheme = (e) => {
    e.stopPropagation();
    const root = document.documentElement;
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      root.classList.add('theme-switching');
      window.setTimeout(() => root.classList.remove('theme-switching'), 320);
    }
    setTheme(prev => prev === 'light' ? 'dark' : 'light');
  };

  const handleBadgeClick = (e, id) => {
    e.stopPropagation();
    if (confirmId === id) {
      onBulkMarkRead(id === 'all' ? null : id);
      setConfirmId(null);
    } else {
      setConfirmId(id);
    }
  };

  const renderBadge = (id, count) => {
    if (count <= 0) return null;
    
    const isConfirming = confirmId === id;
    const isHovered = hoveredId === id;

    // Use interactive version only on desktop
    if (isDesktop && !isOffline) {
      return (
        <span 
          className={`unread-badge ${isConfirming ? 'confirm' : ''}`}
          onMouseEnter={() => setHoveredId(id)}
          onMouseLeave={() => { setHoveredId(null); setConfirmId(null); }}
          onClick={(e) => handleBadgeClick(e, id)}
          title={isConfirming ? "Click again to confirm" : "Mark all as read"}
        >
          {isConfirming ? 'Confirm' : (isHovered ? 'Read All' : count)}
        </span>
      );
    }

    return <span className="unread-badge">{count}</span>;
  };

  if (!isDesktop) {
    return (
      <FeedsScreen
        feedsData={feedsData}
        selectedFeedId={selectedFeedId}
        selectedCategoryId={selectedCategoryId}
        onSelectFeed={onSelectFeed}
        onSelectCategory={onSelectCategory}
        isOpen={isOpen}
        onOpenSettings={onOpenSettings}
        theme={theme}
        onToggleTheme={toggleTheme}
        syncing={syncing}
        lastSyncTime={lastSyncTime}
        performSync={performSync}
        cacheProgress={cacheProgress}
        isOffline={isOffline}
        isForcedOffline={isForcedOffline}
        toggleForcedOffline={toggleForcedOffline}
        savedRowOnlyWhenNonempty={savedRowOnlyWhenNonempty}
      />
    );
  }

  // On Desktop, we show it always but it can be 'collapsed' into a single icon bar

  const effectivelyCollapsed = isSidebarCollapsed && isDesktop;

  return (
    <>
      <div className={`sidebar ${effectivelyCollapsed ? 'collapsed' : ''}`}>
        {!effectivelyCollapsed && (
          <div className="sidebar-top-row">
            <Link to="/" className="nav-brand">
              <img className="nav-logo" src="/favicon-192.png" alt="" />
              ReadabilityRSS
              {window.location.hostname === 'localhost' && <span className="dev-badge">LOCAL</span>}
            </Link>
            {isDesktop && (
              <button
                type="button"
                className="sidebar-toggle-btn"
                onClick={onToggleSidebar}
                title="Collapse sidebar"
                aria-label="Collapse sidebar"
              >
                <span className="material-symbols-outlined">left_panel_close</span>
              </button>
            )}
          </div>
        )}

        <div
          className="sidebar-header"
          onClick={!effectivelyCollapsed ? () => { onSelectCategory && onSelectCategory('all'); onSelectFeed(null); } : undefined}
          style={!effectivelyCollapsed ? { cursor: 'pointer' } : undefined}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {effectivelyCollapsed ? (
              <button
                type="button"
                className="sidebar-toggle-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleSidebar();
                }}
                title="Expand sidebar"
                aria-label="Expand sidebar"
              >
                <span className="material-symbols-outlined">left_panel_open</span>
              </button>
            ) : (
              <span className="material-symbols-outlined sidebar-header-icon" aria-hidden="true">inbox</span>
            )}
            {!effectivelyCollapsed && (
              <span
                className={selectedFeedId === null && (selectedCategoryId === 'all' || !selectedCategoryId) ? 'all-sources-active' : ''}
                style={{ cursor: 'pointer' }}
              >
                All articles
              </span>
            )}
          </div>
          {!effectivelyCollapsed && renderBadge('all', feedsData.total_unread)}
        </div>

        {!effectivelyCollapsed && (!savedRowOnlyWhenNonempty || (feedsData?.total_saved || 0) > 0) && (
          <div
            className={`feed-item saved-row ${selectedFeedId === 'saved' ? 'active' : ''}`}
            onClick={() => onSelectFeed('saved')}
            style={{ margin: '2px 0 6px' }}
          >
            <div className="favicon-wrapper">
              <span className="material-symbols-outlined" style={{ fontSize: '18px', color: 'var(--text-secondary)' }}>
                bookmark
              </span>
            </div>
            <span className="feed-name">Saved</span>
            {(feedsData?.total_saved || 0) > 0 && (
              <span className="unread-badge">{feedsData.total_saved}</span>
            )}
          </div>
        )}

        {!effectivelyCollapsed && (() => {
          const savedOrder = parseCategoryOrder(localStorage.getItem(CATEGORY_ORDER_KEY));
          const sortedCategories = sortByCategoryOrder(feedsData?.categories || [], savedOrder);
          return (
          <div className="sidebar-content">
            {sortedCategories.map(cat => (
              <div key={cat.id || 'uncat'} className="category-group">
                <div className="category-header">
                  <span
                    className={`category-header-title ${selectedFeedId === null && String(selectedCategoryId) === String(cat.id) ? 'active' : ''}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectCategory && onSelectCategory(cat.id);
                    }}
                  >
                    {displayCategoryName(cat.name)}
                  </span>
                  <span
                    className={`category-toggle-icon material-symbols-outlined ${collapsedCats[cat.id] ? 'is-collapsed' : ''}`}
                    style={{ fontSize: 16 }}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleCategory(cat.id);
                    }}
                    title={collapsedCats[cat.id] ? "Expand category" : "Collapse category"}
                  >
                    expand_less
                  </span>
                </div>
                {/* Always rendered so the fold can animate; inert keeps collapsed
                    feeds out of the tab order and away from clicks. */}
                <div
                  className={`category-feeds ${collapsedCats[cat.id] ? 'is-collapsed' : ''}`}
                  inert={collapsedCats[cat.id] ? '' : undefined}
                >
                <div className="category-feeds-inner">
                {cat.feeds.map(feed => (
                  <div
                    key={feed.id}
                    className={`feed-item ${selectedFeedId === feed.id ? 'active' : ''}`}
                    onClick={() => onSelectFeed(feed.id)}
                  >
                    <div className="favicon-wrapper">
                      <img
                        src={`https://www.google.com/s2/favicons?sz=32&domain_url=${encodeURIComponent(feed.site_url || feed.url)}`}
                        alt=""
                        className="feed-favicon"
                        onError={(e) => {
                          e.target.style.display = 'none';
                          const fallback = e.target.parentElement?.querySelector('.favicon-fallback');
                          if (fallback) fallback.style.display = 'flex';
                        }}
                      />
                      <span className="material-symbols-outlined favicon-fallback" style={{ display: 'none', fontSize: '16px', color: 'var(--text-muted)' }}>
                        rss_feed
                      </span>
                    </div>
                    <span className="feed-name">{feed.name}</span>
                    {renderBadge(feed.id, feed.unread_count)}
                  </div>
                ))}
                </div>
                </div>
              </div>
            ))}
          </div>
          );
        })()}

        <div className={`sidebar-footer ${effectivelyCollapsed ? 'is-collapsed' : ''}`}>
          <SyncStatus
            syncing={syncing}
            lastSyncTime={lastSyncTime}
            performSync={performSync}
            cacheProgress={cacheProgress}
            isCollapsed={effectivelyCollapsed}
            isOffline={isOffline}
            isForcedOffline={isForcedOffline}
            toggleForcedOffline={toggleForcedOffline}
          />
          <div className="sidebar-footer-icons">
            <button
              type="button"
              onClick={toggleTheme}
              className="theme-toggle"
              title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
              aria-label={theme === 'dark' ? 'Light mode' : 'Dark mode'}
            >
              <span className="material-symbols-outlined">
                {theme === 'dark' ? 'light_mode' : 'dark_mode'}
              </span>
            </button>
            <button
              type="button"
              onClick={onOpenSettings}
              className="theme-toggle settings-btn"
              title="Settings"
              aria-label="Settings"
            >
              <span className="material-symbols-outlined">settings</span>
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
