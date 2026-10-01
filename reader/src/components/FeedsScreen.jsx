import React, { useMemo, useState } from 'react';
import '../styles/feeds_screen.css';
import SyncStatus from './SyncStatus';
import { displayCategoryName } from '../utils/articleText';
import { CATEGORY_ORDER_KEY, parseCategoryOrder, sortByCategoryOrder } from '../utils/categoryOrder';

function FeedFavicon({ feed }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return <span className="material-symbols-outlined fs-favicon fs-favicon-fallback" aria-hidden="true">rss_feed</span>;
  }
  return (
    <img
      className="fs-favicon"
      src={`https://www.google.com/s2/favicons?sz=32&domain_url=${encodeURIComponent(feed.site_url || feed.url)}`}
      alt=""
      onError={() => setFailed(true)}
    />
  );
}

// Phones: the sidebar as the root screen of the back stack (Feeds, then a list,
// then an article) rather than an overlay. App.jsx owns the history entries; this
// only renders the page while `isOpen`, and stays mounted so its scroll survives.
export default function FeedsScreen({
  feedsData,
  selectedFeedId,
  selectedCategoryId,
  onSelectFeed,
  onSelectCategory,
  isOpen,
  onOpenSettings,
  theme,
  onToggleTheme,
  syncing,
  lastSyncTime,
  performSync,
  cacheProgress,
  isOffline,
  isForcedOffline,
  toggleForcedOffline,
  savedRowOnlyWhenNonempty,
}) {
  const [query, setQuery] = useState('');
  const [collapsedCats, setCollapsedCats] = useState({});

  const categories = useMemo(() => {
    const sorted = sortByCategoryOrder(
      feedsData?.categories || [],
      parseCategoryOrder(localStorage.getItem(CATEGORY_ORDER_KEY))
    );
    const q = query.trim().toLowerCase();
    return sorted
      .map(cat => ({
        ...cat,
        unread: cat.feeds.reduce((sum, f) => sum + (f.unread_count || 0), 0),
        shown: q ? cat.feeds.filter(f => f.name.toLowerCase().includes(q)) : cat.feeds,
      }))
      .filter(cat => !q || cat.shown.length > 0);
  }, [feedsData, query]);

  const searching = query.trim() !== '';

  // Coming back to Feeds should show every feed, not the last search.
  const pickFeed = (id) => { setQuery(''); onSelectFeed(id); };
  const pickCategory = (id) => { setQuery(''); onSelectCategory(id); };
  const allActive = selectedFeedId === null && (!selectedCategoryId || selectedCategoryId === 'all');
  const savedActive = selectedFeedId === 'saved';
  const showSavedRow = !savedRowOnlyWhenNonempty || (feedsData?.total_saved || 0) > 0;

  return (
    <div
      className={`feeds-screen${isOpen ? ' is-open' : ''}`}
      aria-hidden={!isOpen}
      inert={isOpen ? undefined : ''}
    >
      {/* A bar outside the scroller, not a title inside it: a scroller that fills the
          whole viewport is promoted to the page's root scroller on Android Chrome, and
          then dragging it moves the URL bar and stretches the floating controls too. */}
      <header className="fs-bar">
        <span className="fs-brand">
          <img className="fs-brand-logo" src="/favicon-192.png" alt="" />
          ReadabilityRSS
          {window.location.hostname === 'localhost' && <span className="dev-badge">LOCAL</span>}
        </span>
        <div className="fs-bar-actions">
          <button
            type="button"
            className="fs-round-btn"
            onClick={onToggleTheme}
            aria-label={theme === 'dark' ? 'Light mode' : 'Dark mode'}
            title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
          >
            <span className="material-symbols-outlined">{theme === 'dark' ? 'light_mode' : 'dark_mode'}</span>
          </button>
          <button type="button" className="fs-round-btn" onClick={onOpenSettings} aria-label="Settings" title="Settings">
            <span className="material-symbols-outlined">settings</span>
          </button>
        </div>
      </header>
      <h1 className="fs-sr-only">Feeds</h1>

      <div className="fs-scroll">
        <label className="fs-search">
          <span className="material-symbols-outlined" aria-hidden="true">search</span>
          <input
            type="search"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search feeds"
            aria-label="Search feeds"
            enterKeyHint="search"
            autoComplete="off"
          />
        </label>

        {!searching && (
          <div className="fs-group">
            <button
              type="button"
              className={`fs-row${allActive ? ' is-active' : ''}`}
              onClick={() => pickCategory('all')}
            >
              <span className="fs-row-icon" aria-hidden="true">
                <span className="material-symbols-outlined">inbox</span>
              </span>
              <span className="fs-row-name">All Articles</span>
              {feedsData?.total_unread > 0 && (
                <span className="fs-row-count">{feedsData.total_unread.toLocaleString()}</span>
              )}
              <span className="material-symbols-outlined fs-row-chevron" aria-hidden="true">chevron_right</span>
            </button>
            {showSavedRow && (
              <button
                type="button"
                className={`fs-row${savedActive ? ' is-active' : ''}`}
                onClick={() => pickFeed('saved')}
              >
                <span className="fs-row-icon" aria-hidden="true">
                  <span className="material-symbols-outlined">bookmark</span>
                </span>
                <span className="fs-row-name">Saved</span>
                {Boolean(feedsData?.total_saved > 0) && (
                  <span className="fs-row-count">{feedsData.total_saved.toLocaleString()}</span>
                )}
                <span className="material-symbols-outlined fs-row-chevron" aria-hidden="true">chevron_right</span>
              </button>
            )}
          </div>
        )}

        {categories.map(cat => {
          const catKey = cat.id || 'uncat';
          const collapsed = !searching && collapsedCats[catKey];
          const catActive = selectedFeedId === null && String(selectedCategoryId) === String(cat.id);
          return (
            <section key={catKey} className="fs-section">
              <div className="fs-section-head">
                <button
                  type="button"
                  className={`fs-section-title${catActive ? ' is-active' : ''}`}
                  onClick={() => pickCategory(cat.id)}
                >
                  {displayCategoryName(cat.name)}
                  {cat.unread > 0 && <span className="fs-section-count"> · {cat.unread.toLocaleString()}</span>}
                </button>
                {!searching && (
                  <button
                    type="button"
                    className={`fs-section-toggle${collapsed ? ' is-collapsed' : ''}`}
                    onClick={() => setCollapsedCats(prev => ({ ...prev, [catKey]: !prev[catKey] }))}
                    aria-expanded={!collapsed}
                    aria-label={collapsed ? 'Expand category' : 'Collapse category'}
                  >
                    <span className="material-symbols-outlined">expand_more</span>
                  </button>
                )}
              </div>
              {!collapsed && (
                <div className="fs-group">
                  {cat.shown.map(feed => (
                    <button
                      type="button"
                      key={feed.id}
                      className={`fs-row${selectedFeedId === feed.id ? ' is-active' : ''}`}
                      onClick={() => pickFeed(feed.id)}
                    >
                      <FeedFavicon feed={feed} />
                      <span className="fs-row-name">{feed.name}</span>
                      {feed.unread_count > 0 && (
                        <span className="fs-row-count">{feed.unread_count.toLocaleString()}</span>
                      )}
                      <span className="material-symbols-outlined fs-row-chevron" aria-hidden="true">chevron_right</span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          );
        })}

        {searching && categories.length === 0 && (
          <p className="fs-empty">No feeds match “{query.trim()}”</p>
        )}
      </div>

      <div className="fs-bottom">
        <div className="fs-status">
          <SyncStatus
            syncing={syncing}
            lastSyncTime={lastSyncTime}
            performSync={performSync}
            cacheProgress={cacheProgress}
            isCollapsed={false}
            isOffline={isOffline}
            isForcedOffline={isForcedOffline}
            toggleForcedOffline={toggleForcedOffline}
          />
        </div>
      </div>
    </div>
  );
}
