import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import '../styles/sidebar.css';
import SyncStatus from './SyncStatus';
import { CATEGORY_ORDER_KEY, parseCategoryOrder, sortByCategoryOrder } from '../utils/categoryOrder';

export default function Sidebar({
  feedsData,
  selectedFeedId,
  onSelectFeed,
  selectedCategoryId = 'all',
  onSelectCategory,
  isOpen,
  onClose,
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

  const toggleTheme = (e) => {
    e.stopPropagation();
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

  // On Mobile, we show the full sidebar but it's hidden/shown via 'isOpen'
  // On Desktop, we show it always but it can be 'collapsed' into a single icon bar

  const effectivelyCollapsed = isSidebarCollapsed && isDesktop;

  return (
    <>
      <div className={`sidebar-overlay ${isOpen ? 'visible' : ''}`} onClick={isOpen ? onClose : undefined}></div>

      <div className={`sidebar ${isOpen ? 'open' : ''} ${effectivelyCollapsed ? 'collapsed' : ''}`}>
        {!effectivelyCollapsed && (
          <div className="sidebar-top-row">
            <Link to="/" className="nav-brand">
              <img className="nav-logo" src="/favicon-192.png" alt="" />
              ReadabilityRSS
              {window.location.hostname === 'localhost' && <span className="dev-badge">LOCAL</span>}
            </Link>
            {!isDesktop && (
              <button
                type="button"
                className="sidebar-mobile-close-btn"
                onClick={onClose}
                title="Close Sidebar"
              >
                <span className="material-symbols-outlined">close</span>
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
            {isDesktop && (
              <button
                type="button"
                className="sidebar-toggle-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleSidebar();
                }}
                title={effectivelyCollapsed ? "Expand Sidebar" : "Collapse Sidebar"}
              >
                <span className="material-symbols-outlined" style={{ fontSize: '20px' }}>
                  {effectivelyCollapsed ? 'menu' : 'menu_open'}
                </span>
              </button>
            )}
            {!effectivelyCollapsed && (
              <span
                className={selectedFeedId === null && (selectedCategoryId === 'all' || !selectedCategoryId) ? 'all-sources-active' : ''}
                style={{ cursor: 'pointer' }}
              >
                ALL ARTICLES
              </span>
            )}
          </div>
          {!effectivelyCollapsed && renderBadge('all', feedsData.total_unread)}
        </div>

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
                    {cat.name}
                  </span>
                  <span
                    className="category-toggle-icon material-symbols-outlined"
                    style={{ fontSize: 16 }}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleCategory(cat.id);
                    }}
                    title={collapsedCats[cat.id] ? "Expand category" : "Collapse category"}
                  >
                    {collapsedCats[cat.id] ? 'expand_more' : 'expand_less'}
                  </span>
                </div>
                {!collapsedCats[cat.id] && cat.feeds.map(feed => (
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
            ))}
          </div>
          );
        })()}

        <div className="sidebar-footer">
          <div className="settings-row" onClick={onOpenSettings}>
            <div className="settings-row-content">
              <span className="material-symbols-outlined icon">settings</span>
              {!effectivelyCollapsed && <span className="label">Settings</span>}
            </div>

            {!effectivelyCollapsed && (
              <button
                onClick={toggleTheme}
                className="theme-toggle"
                title="Toggle Theme"
              >
                <span className="material-symbols-outlined icon">
                  {theme === 'dark' ? 'light_mode' : 'dark_mode'}
                </span>
              </button>
            )}
          </div>

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
        </div>
      </div>
    </>
  );
}
