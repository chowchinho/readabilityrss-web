import React, { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import '../styles/sources_index.css';
import { API_URL, getArticle, getSortMode } from '../api';
import { mergeShuffledOrder } from '../utils/shuffle';
import { CATEGORY_ORDER_KEY, parseCategoryOrder, sortByCategoryOrder } from '../utils/categoryOrder';
import CardMeta from './CardMeta';
import FeedbackButtons from './FeedbackButtons';
import { useImpressions, recordEvent, flushEvents } from '../hooks/useImpressions';
import SmartCropImage from './SmartCropImage';
import ArticleCardSlideshow from './ArticleCardSlideshow';
import { formatRelativeDate, cleanTranslationTag, stripHtml } from '../utils/articleText';

// Survives this component unmounting while the reader is inside an article.
let indexOrderCache = { key: null, ids: [] };
const heroBannerCache = new Map();

function truncateSnippet(text, maxLen) {
  const trimmed = (text || '').replace(/\s*(\.{2,}|…)\s*$/, '').trim();
  if (!trimmed) return '';
  return trimmed.length > maxLen ? `${trimmed.slice(0, maxLen).trimEnd()}…` : trimmed;
}

const snippetCache = new Map();
const SNIPPET_CACHE_MAX = 500;

function computeCleanSnippet(art, maxLen, featuredFullText) {
  if (!art) return '';

  if (maxLen > 300 && featuredFullText) {
    const cleanFull = cleanTranslationTag(featuredFullText);
    if (cleanFull && cleanFull.length > 5) {
      return truncateSnippet(cleanFull, maxLen);
    }
  }

  let sourceText = '';
  if (maxLen > 300) {
    const fullText = stripHtml(art.content || art.description || '');
    sourceText = (fullText && fullText.length > 50) ? fullText : (art.featured_snippet || art.snippet || fullText || '');
  } else {
    sourceText = art.snippet || art.featured_snippet || stripHtml(art.description || art.content || '');
  }

  const rawSnippet = sourceText ? cleanTranslationTag(sourceText) : '';
  if (rawSnippet && rawSnippet.length > 5) {
    return truncateSnippet(rawSnippet, maxLen);
  }

  if (art.title) {
    const cleanTitle = cleanTranslationTag(art.title);
    const parts = cleanTitle.split(/(?<=[♪！!||\—\–\:\;\n]|\s[-—]\s)/).map(s => s.trim()).filter(p => p && p.length > 2);
    if (parts.length > 1) {
      const snippetParts = parts.slice(1).filter(p => !p.toLowerCase().includes((art.source_name || '').toLowerCase()));
      if (snippetParts.length > 0) {
        return truncateSnippet(snippetParts.join(' '), maxLen);
      }
    }
    return truncateSnippet(cleanTitle, maxLen);
  }
  return '';
}

function getCachedCleanSnippet(art, maxLen = 120, featuredFullText = '') {
  if (!art) return '';
  const key = `${art.id}_${maxLen}_${featuredFullText ? featuredFullText.length : 0}_${art.title || ''}_${art.snippet || ''}`;
  if (snippetCache.has(key)) {
    const cached = snippetCache.get(key);
    snippetCache.delete(key);
    snippetCache.set(key, cached);
    return cached;
  }
  const result = computeCleanSnippet(art, maxLen, featuredFullText);
  if (snippetCache.size >= SNIPPET_CACHE_MAX) {
    const firstKey = snippetCache.keys().next().value;
    snippetCache.delete(firstKey);
  }
  snippetCache.set(key, result);
  return result;
}

const faviconCache = new Map();
const FAVICON_CACHE_MAX = 500;

function getCachedFaviconUrl(art, feedsMap) {
  if (!art) return null;
  const feed = feedsMap ? feedsMap.get(String(art.source_id)) : null;
  const targetUrl = feed?.site_url || feed?.url || art.url;
  if (!targetUrl) return null;
  if (faviconCache.has(targetUrl)) {
    const cached = faviconCache.get(targetUrl);
    faviconCache.delete(targetUrl);
    faviconCache.set(targetUrl, cached);
    return cached;
  }
  const result = `https://www.google.com/s2/favicons?sz=32&domain_url=${encodeURIComponent(targetUrl)}`;
  if (faviconCache.size >= FAVICON_CACHE_MAX) {
    const firstKey = faviconCache.keys().next().value;
    faviconCache.delete(firstKey);
  }
  faviconCache.set(targetUrl, result);
  return result;
}

export default function SourcesIndex({
  feedsData,
  articles = [],
  onSelectFeed,
  onSelectArticle,
  onBulkMarkRead,
  onToggleRead,
  activeFeedId = null,
  onClearActiveFeed = null,
  selectedCategory: externalSelectedCategory,
  onSelectCategory,
  hasMore = false,
  onLoadMore = null,
  showOnlyUnread: externalShowOnlyUnread,
  setShowOnlyUnread: externalSetShowOnlyUnread,
  onVoted,
  isOffline = false,
}) {
  const [internalCategory, setInternalCategory] = useState('all');
  const selectedCategory = externalSelectedCategory !== undefined ? externalSelectedCategory : internalCategory;

  useImpressions();

  const changeCategory = (catId) => {
    if (onSelectCategory) {
      onSelectCategory(catId);
    } else {
      setInternalCategory(catId);
    }
  };
  const [internalShowOnlyUnread, setInternalShowOnlyUnread] = useState(false);
  const showOnlyUnread = externalShowOnlyUnread !== undefined ? externalShowOnlyUnread : internalShowOnlyUnread;
  const setShowOnlyUnread = externalSetShowOnlyUnread || setInternalShowOnlyUnread;
  const [readAllHovered, setReadAllHovered] = useState(false);
  const [readAllConfirming, setReadAllConfirming] = useState(false);
  const [featuredFullTextMap, setFeaturedFullTextMap] = useState({});
  const [visibleCount, setVisibleCount] = useState(9);
  const [animatingOutIds, setAnimatingOutIds] = useState(new Set());
  const [revealedMetaId, setRevealedMetaId] = useState(null);
  const hasRanking = getSortMode() === 'smart';
  const sentinelRef = useRef(null);
  const quickMarkTimersRef = useRef(new Map()); // id -> timeoutId

  useEffect(() => () => {
    quickMarkTimersRef.current.forEach(clearTimeout);
    quickMarkTimersRef.current.clear();
  }, []);

  // Touch only: any tap outside the revealed row collapses it. CardMeta stops
  // propagation on its own tap, so this never fires for the row that opened.
  useEffect(() => {
    if (revealedMetaId === null) return;
    const dismiss = () => setRevealedMetaId(null);
    document.addEventListener('click', dismiss);
    return () => document.removeEventListener('click', dismiss);
  }, [revealedMetaId]);

  const handleRevealToggle = useCallback((id) => {
    setRevealedMetaId((current) => (current === id ? null : id));
  }, []);

  const handleQuickMarkRead = (e, art) => {
    e.stopPropagation();
    if (!onToggleRead || !art) return;

    // The only place read_no_vote is raised: one card, dismissed deliberately, never
    // opened. A vote already says more than this does, so a voted article is skipped.
    const reportDismissal = () => {
      if (art.is_read || art.vote) return;
      recordEvent({
        article_id: art.id,
        event_type: 'read_no_vote',
        primary_topic: art.topics?.primary,
        secondary_topics: art.topics?.secondary,
        region: art.topics?.region,
        article_type: art.topics?.type,
      });
      flushEvents();
    };

    if (showOnlyUnread && !art.is_read) {
      reportDismissal();
      setAnimatingOutIds(prev => new Set(prev).add(art.id));
      const timerId = setTimeout(() => {
        quickMarkTimersRef.current.delete(art.id);
        onToggleRead(art.id, art.is_read);
        setAnimatingOutIds(prev => {
          const next = new Set(prev);
          next.delete(art.id);
          return next;
        });
      }, 280);
      quickMarkTimersRef.current.set(art.id, timerId);
    } else {
      if (!art.is_read) reportDismissal();
      onToggleRead(art.id, art.is_read);
    }
  };
  const pillsRef = useRef(null);
  const [isMouseDown, setIsMouseDown] = useState(false);
  const [startX, setStartX] = useState(0);
  const [scrollLeft, setScrollLeft] = useState(0);

  const [pillFades, setPillFades] = useState({ start: false, end: false });

  // Only fade an edge that actually has hidden content beyond it, so a bar with
  // nothing to scroll renders sharp instead of dimming its first pill at rest.
  const updatePillFades = useCallback(() => {
    const el = pillsRef.current;
    if (!el) return;
    const maxScroll = el.scrollWidth - el.clientWidth;
    const overflowing = maxScroll > 1;
    setPillFades({
      start: overflowing && el.scrollLeft > 1,
      end: overflowing && el.scrollLeft < maxScroll - 1,
    });
  }, []);

  const handlePillsMouseDown = (e) => {
    if (!pillsRef.current) return;
    setIsMouseDown(true);
    setStartX(e.pageX - pillsRef.current.offsetLeft);
    setScrollLeft(pillsRef.current.scrollLeft);
  };

  const handlePillsMouseLeave = () => {
    setIsMouseDown(false);
  };

  const handlePillsMouseUp = () => {
    setIsMouseDown(false);
  };

  const handlePillsMouseMove = (e) => {
    if (!isMouseDown || !pillsRef.current) return;
    e.preventDefault();
    const x = e.pageX - pillsRef.current.offsetLeft;
    const walk = (x - startX) * 1.5;
    pillsRef.current.scrollLeft = scrollLeft - walk;
  };

  const effectiveFeedId = activeFeedId;

  // Re-evaluate on width changes too: resizing can remove the overflow entirely.
  useEffect(() => {
    const el = pillsRef.current;
    if (!el) return undefined;
    updatePillFades();
    const observer = new ResizeObserver(updatePillFades);
    observer.observe(el);
    return () => observer.disconnect();
  }, [updatePillFades, effectiveFeedId]);

  // Reset visible article count to 9 whenever active feed or category/unread filters change
  useEffect(() => {
    setVisibleCount(9);
  }, [effectiveFeedId, selectedCategory, showOnlyUnread]);

  // Flatten all feeds across categories
  const allFeeds = useMemo(() => {
    if (!feedsData?.categories) return [];
    return feedsData.categories.flatMap(cat =>
      (cat.feeds || []).map(feed => ({
        ...feed,
        categoryName: cat.name || 'Uncategorized',
        categoryId: cat.id || 'uncat',
      }))
    );
  }, [feedsData]);

  // Read on every render rather than caching: the settings modal writes this key
  // directly and has no change callback, so the raw string is the only signal we get.
  const savedCategoryOrderRaw = typeof localStorage !== 'undefined'
    ? localStorage.getItem(CATEGORY_ORDER_KEY)
    : null;

  // Extract available categories, honouring the custom order from settings.
  // Sort before the id substitution below — Uncategorized is stored as null, not 'uncat'.
  const categoriesList = useMemo(() => {
    if (!feedsData?.categories) return [];
    const ordered = sortByCategoryOrder(feedsData.categories, parseCategoryOrder(savedCategoryOrderRaw));
    return ordered.map(cat => ({
      id: cat.id || 'uncat',
      name: cat.name || 'Uncategorized',
      count: (cat.feeds || []).length,
      unread: (cat.feeds || []).reduce((acc, f) => acc + (f.unread_count || 0), 0),
    }));
  }, [feedsData, savedCategoryOrderRaw]);

  // Group articles by source_id for quick preview lookups
  const articlesBySource = useMemo(() => {
    const map = new Map();
    articles.forEach(art => {
      const sourceKey = String(art.source_id);
      if (!map.has(sourceKey)) map.set(sourceKey, []);
      map.get(sourceKey).push(art);
    });
    // Only re-sort for chronological. Under smart or random the incoming array is
    // already in the order the server ranked it, and sorting by date here was
    // discarding the ranking on every feed index page.
    if (getSortMode() === 'latest') {
      map.forEach((list) => {
        list.sort((a, b) => new Date(b.pub_date || b.created_at) - new Date(a.pub_date || a.created_at));
      });
    }
    return map;
  }, [articles]);

  // Active feed object for Single Feed Index view
  const activeFeedObj = useMemo(() => {
    if (!effectiveFeedId) return null;
    return allFeeds.find(f => String(f.id) === String(effectiveFeedId));
  }, [effectiveFeedId, allFeeds]);

  // Articles for active feed
  const activeFeedArticles = useMemo(() => {
    if (!effectiveFeedId) return [];
    const raw = articlesBySource.get(String(effectiveFeedId)) || [];
    if (showOnlyUnread) {
      return raw.filter(art => !art.is_read);
    }
    return raw;
  }, [effectiveFeedId, articlesBySource, showOnlyUnread]);

  // Filter all articles for the All Articles view
  const allArticlesFiltered = useMemo(() => {
    return articles.filter(art => {
      if (showOnlyUnread && art.is_read) return false;
      if (selectedCategory !== 'all') {
        const feed = allFeeds.find(f => String(f.id) === String(art.source_id));
        if (!feed || String(feed.categoryId) !== String(selectedCategory)) return false;
      }
      return true;
    });
  }, [articles, showOnlyUnread, selectedCategory, allFeeds]);

  const viewKey = `${effectiveFeedId ?? 'all'}|${selectedCategory}|${showOnlyUnread ? 'unread' : 'any'}`;

  // Deal a fresh order whenever the view changes, and keep it while the view holds.
  // The cache lives outside the component deliberately: opening an article unmounts
  // this one, and a reader coming back should find the page they left, not a reshuffle.
  // Switching category changes the key, which is what re-deals.
  const randomizedAllArticles = useMemo(() => {
    if (!allArticlesFiltered.length) return [];
    // The hub used to shuffle unconditionally. Now that feed order is a setting, the
    // shuffle belongs to "random" only — under smart it was hiding the ranking, and
    // under latest it was hiding the chronology.
    if (getSortMode() !== 'random') return allArticlesFiltered;
    const previousIds = indexOrderCache.key === viewKey ? indexOrderCache.ids : [];
    const ordered = mergeShuffledOrder(previousIds, allArticlesFiltered);
    indexOrderCache = { key: viewKey, ids: ordered.map(a => a.id) };
    return ordered;
  }, [allArticlesFiltered, viewKey]);

  const targetArticles = effectiveFeedId && activeFeedObj ? activeFeedArticles : randomizedAllArticles;
  const hasMoreLocal = visibleCount < targetArticles.length;
  const canLoadMore = hasMoreLocal || hasMore;

  // Infinite scroll intersection observer. `visibleCount` is a dependency so the
  // observer re-subscribes after each batch — otherwise a sentinel that stays in
  // view never re-fires and loading stalls until the user scrolls again.
  useEffect(() => {
    if (!canLoadMore) return;
    const observer = new IntersectionObserver((entries) => {
      if (!entries[0].isIntersecting) return;
      if (visibleCount < targetArticles.length) {
        setVisibleCount(prev => Math.min(prev + 8, targetArticles.length));
      } else if (hasMore && onLoadMore) {
        onLoadMore();
      }
    }, { rootMargin: '300px' });

    const currentSentinel = sentinelRef.current;
    if (currentSentinel) observer.observe(currentSentinel);

    return () => {
      if (currentSentinel) observer.unobserve(currentSentinel);
    };
  }, [canLoadMore, visibleCount, targetArticles.length, hasMore, onLoadMore]);

  // Helper to extract image URL for articles
  const getArticleImage = (art) => {
    if (!art) return null;
    return art.main_image_proxy
      ? (art.main_image_proxy.startsWith('http') ? art.main_image_proxy : `${API_URL}${art.main_image_proxy}`)
      : art.main_image;
  };

  // Pick a random feature image from the feed's articles for the hero banner bg.
  // The pick is cached per feed so a growing article pool (infinite scroll, late
  // first load) does not re-deal the banner underneath the reader.
  const heroBannerImage = useMemo(() => {
    if (!effectiveFeedId) return null;
    if (heroBannerCache.has(effectiveFeedId)) return heroBannerCache.get(effectiveFeedId);
    const withImages = activeFeedArticles.map(getArticleImage).filter(Boolean);
    if (!withImages.length) return null;
    const pick = withImages[Math.floor(Math.random() * withImages.length)];
    heroBannerCache.set(effectiveFeedId, pick);
    return pick;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveFeedId, activeFeedArticles]);

  const feedsMap = useMemo(() => new Map(allFeeds.map(f => [String(f.id), f])), [allFeeds]);

  // Helper to extract favicon URL for articles
  const getFaviconUrl = useCallback((art) => {
    return getCachedFaviconUrl(art, feedsMap);
  }, [feedsMap]);

  const getCleanTitle = (art) => {
    if (!art || !art.title) return '';
    return cleanTranslationTag(art.title);
  };

  // Current featured article candidate
  const currentFeaturedCandidate = targetArticles[0] || null;

  useEffect(() => {
    if (!currentFeaturedCandidate?.id) return;
    const artId = currentFeaturedCandidate.id;
    if (featuredFullTextMap[artId]) return;

    if (currentFeaturedCandidate.content || currentFeaturedCandidate.featured_snippet) {
      const text = currentFeaturedCandidate.featured_snippet || stripHtml(currentFeaturedCandidate.content);
      if (text && text.length > 300) {
        setFeaturedFullTextMap(prev => ({ ...prev, [artId]: text }));
        return;
      }
    }

    let isMounted = true;
    getArticle(artId).then(fullArt => {
      if (!isMounted) return;
      if (fullArt && fullArt.content) {
        const text = stripHtml(fullArt.content);
        if (text) {
          setFeaturedFullTextMap(prev => ({ ...prev, [artId]: text }));
        }
      }
    }).catch(err => {
      console.warn('Could not fetch full article text for hero card:', err);
    });

    return () => { isMounted = false; };
  }, [currentFeaturedCandidate?.id]);

  const getCleanSnippet = useCallback((art, maxLen = 120) => {
    return getCachedCleanSnippet(art, maxLen, featuredFullTextMap[art?.id]);
  }, [featuredFullTextMap]);

  const renderScrollSentinelOrEnd = (totalCount) => {
    // An empty count used to bail out here, which stranded any view whose matches all
    // sit past the loaded window: no sentinel meant no onLoadMore, so it never filled.
    if (totalCount === 0 && !hasMore) return null;
    if (visibleCount < totalCount || hasMore) {
      return (
        <div ref={sentinelRef} className="monocle-infinite-sentinel">
          <div className="monocle-loading-spinner">
            <span className="material-symbols-outlined spinning-icon" style={{ fontSize: 20 }}>sync</span>
            <span>Loading more stories...</span>
          </div>
        </div>
      );
    }
    return (
      <div className="monocle-end-of-page">
        <span className="monocle-end-line" />
        <span className="monocle-end-badge">END OF COLLECTION • {totalCount} ARTICLES</span>
        <span className="monocle-end-line" />
      </div>
    );
  };

  // =========================================================
  // RENDER: SINGLE FEED INDEX PAGE (Monocle 3-Row Collection + Infinite Scroll)
  // =========================================================
  if (effectiveFeedId && activeFeedObj) {
    const visibleArticles = activeFeedArticles.slice(0, visibleCount);
    const featuredArticle = visibleArticles[0] || null;
    const gridArticles = visibleArticles.slice(1);
    const featuredSnippet = featuredArticle ? getCleanSnippet(featuredArticle, 850) : '';

    return (
      <div className="sources-index-container is-feed-index">
        {/* Sticky Header */}
        <div className="sources-index-header">
          <div className="feed-hero-banner">
            {heroBannerImage && (
              <img
                className="feed-hero-banner-bg"
                src={heroBannerImage}
                alt=""
                aria-hidden="true"
              />
            )}
            <div className="feed-banner-info">
              <div className="feed-banner-icon">
                <img
                  src={`https://www.google.com/s2/favicons?sz=64&domain_url=${encodeURIComponent(activeFeedObj.site_url || activeFeedObj.url)}`}
                  alt=""
                  onError={(e) => { e.target.style.display = 'none'; }}
                />
              </div>
              <div>
                <h1 className="feed-banner-title">{activeFeedObj.name}</h1>
                <div className="feed-banner-meta">
                  <span
                    className="feed-banner-category-link"
                    onClick={() => {
                      if (activeFeedObj?.categoryId) {
                        onClearActiveFeed && onClearActiveFeed();
                        changeCategory(activeFeedObj.categoryId);
                      }
                    }}
                    style={{ cursor: 'pointer' }}
                    title={`View all ${activeFeedObj.categoryName} articles`}
                  >
                    {activeFeedObj.categoryName}
                  </span>
                  <span>•</span>
                  <span>{activeFeedArticles.length} Articles</span>
                </div>
              </div>
            </div>
            {(activeFeedObj.unread_count || 0) > 0 && (
              isOffline ? (
                <span className="category-pill-btn active feed-banner-readall">
                  {activeFeedObj.unread_count} Unread
                </span>
              ) : (
                <button
                  type="button"
                  className={`category-pill-btn active feed-banner-readall ${readAllConfirming ? 'is-confirming' : ''}`}
                  onMouseEnter={() => setReadAllHovered(true)}
                  onMouseLeave={() => { setReadAllHovered(false); setReadAllConfirming(false); }}
                  onClick={() => {
                    if (readAllConfirming) {
                      setReadAllConfirming(false);
                      onBulkMarkRead && onBulkMarkRead(activeFeedObj.id);
                    } else {
                      setReadAllConfirming(true);
                    }
                  }}
                  title={readAllConfirming ? 'Click again to confirm' : 'Mark all as read'}
                >
                  {readAllConfirming
                    ? 'Confirm'
                    : (readAllHovered ? 'Read All' : `${activeFeedObj.unread_count} Unread`)}
                </button>
              )
            )}
          </div>
        </div>

        {/* Monocle Content Collection */}
        <div className="monocle-content-collection">
          {/* Row 1: Featured Highlight Article */}
          {featuredArticle ? (
            <div className={`monocle-featured-row ${animatingOutIds.has(featuredArticle.id) ? 'is-animating-out' : ''}`}>
              <div
                className={`monocle-featured-card ${featuredArticle.is_read ? 'is-read' : ''}`}
                data-id={featuredArticle.id}
                onClick={() => onSelectArticle && onSelectArticle(featuredArticle.id)}
              >
                <div className="monocle-featured-media">
                  <ArticleCardSlideshow
                    article={featuredArticle}
                    mainImage={getArticleImage(featuredArticle)}
                    focalX={featuredArticle.focal_x}
                    focalY={featuredArticle.focal_y}
                    fallbackIconSize={64}
                  />
                  {hasRanking && (
                    <div className="monocle-media-feedback">
                      <FeedbackButtons
                        article={featuredArticle}
                        buttonClassName="monocle-media-feedback-btn"
                        onVoted={onVoted}
                      />
                    </div>
                  )}
                  <button
                    className={`quick-mark-read-btn ${featuredArticle.is_read ? 'is-read' : ''}`}
                    onClick={(e) => handleQuickMarkRead(e, featuredArticle)}
                    title={featuredArticle.is_read ? "Mark as Unread" : "Mark as Read"}
                  >
                    <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
                      beenhere
                    </span>
                  </button>
                </div>
                <div className="monocle-featured-body">
                  <h2 className="monocle-featured-title">{getCleanTitle(featuredArticle)}</h2>
                  {featuredSnippet && (
                    <p className="monocle-featured-excerpt">{featuredSnippet}</p>
                  )}
                  <CardMeta
                    article={featuredArticle}
                    timeLabel={formatRelativeDate(featuredArticle.pub_date || featuredArticle.created_at)}
                    isRevealed={revealedMetaId === featuredArticle.id}
                    onRevealToggle={handleRevealToggle}
                  />
                </div>
              </div>
            </div>
          ) : (
            <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
              No articles available in this feed.
            </div>
          )}

          {/* Grid Articles (Appended in batches of 8) */}
          {gridArticles.length > 0 && (
            <div className="monocle-four-col-grid">
              {gridArticles.map(article => {
                const img = getArticleImage(article);
                const snippet = getCleanSnippet(article, 1000);

                return (
                  <div key={article.id} className={`monocle-grid-card-cell ${animatingOutIds.has(article.id) ? 'is-animating-out' : ''}`}>
                    <div
                      className={`monocle-grid-card ${article.is_read ? 'is-read' : ''}`}
                    data-id={article.id}
                      onClick={() => onSelectArticle && onSelectArticle(article.id)}
                    >
                      <div className="monocle-card-image-wrap">
                        <ArticleCardSlideshow
                          article={article}
                          mainImage={img}
                          focalX={article.focal_x}
                          focalY={article.focal_y}
                          fallbackIconSize={36}
                        />
                        {hasRanking && (
                          <div className="monocle-media-feedback">
                            <FeedbackButtons
                              article={article}
                              buttonClassName="monocle-media-feedback-btn"
                              onVoted={onVoted}
                            />
                          </div>
                        )}
                        <button
                          className={`quick-mark-read-btn ${article.is_read ? 'is-read' : ''}`}
                          onClick={(e) => handleQuickMarkRead(e, article)}
                          title={article.is_read ? "Mark as Unread" : "Mark as Read"}
                        >
                          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
                            beenhere
                          </span>
                        </button>
                      </div>
                      <div className="monocle-card-content">
                        <h3 className="monocle-grid-card-title">{getCleanTitle(article)}</h3>
                        {snippet && <p className="monocle-grid-card-snippet">{snippet}</p>}
                        <div className="monocle-card-footer">
                          <CardMeta
                            article={article}
                            timeLabel={formatRelativeDate(article.pub_date || article.created_at)}
                            compact
                            isRevealed={revealedMetaId === article.id}
                            onRevealToggle={handleRevealToggle}
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Infinite Scroll Sentinel or End of Collection Marker */}
          {renderScrollSentinelOrEnd(activeFeedArticles.length)}
        </div>
      </div>
    );
  }

  // =========================================================
  // RENDER: ALL ARTICLES INDEX PAGE (All Articles Collection + Infinite Scroll)
  // =========================================================
  const visibleArticles = randomizedAllArticles.slice(0, visibleCount);
  const featuredArticle = visibleArticles[0] || null;
  const gridArticles = visibleArticles.slice(1);
  const featuredSnippet = featuredArticle ? getCleanSnippet(featuredArticle, 850) : '';

  return (
    <div className="sources-index-container">
      {/* Sticky Header */}
      <div className="sources-index-header">
        <div className="sources-header-top">
          <div className="sources-header-title-group">
            <span className="material-symbols-outlined">newspaper</span>
            <div>
              <h1 className="sources-header-title">All Articles</h1>
              <div className="sources-header-subtitle">
                {allArticlesFiltered.length} Articles • {feedsData.total_unread || 0} Total Unread
              </div>
            </div>
          </div>

          <div className="sources-header-actions">
            <button
              className={`sources-toggle-btn ${showOnlyUnread ? 'active' : ''}`}
              onClick={() => setShowOnlyUnread(prev => !prev)}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>filter_alt</span>
              Unread Only
            </button>
            <button
              className="sources-toggle-btn"
              onClick={() => onBulkMarkRead && onBulkMarkRead(null)}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>done_all</span>
              Mark All Read
            </button>
          </div>
        </div>

        {/* Category Pills Bar */}
        <div className="sources-filter-bar">
          <div
            ref={pillsRef}
            className={`category-pills-scroll ${pillFades.start ? 'fade-start' : ''} ${pillFades.end ? 'fade-end' : ''}`}
            onScroll={updatePillFades}
            onMouseDown={handlePillsMouseDown}
            onMouseLeave={handlePillsMouseLeave}
            onMouseUp={handlePillsMouseUp}
            onMouseMove={handlePillsMouseMove}
          >
            <button
              className={`category-pill-btn ${selectedCategory === 'all' ? 'active' : ''}`}
              onClick={() => changeCategory('all')}
            >
              All Sources
              <span className="category-pill-badge">{allFeeds.length}</span>
            </button>
            {categoriesList.map(cat => (
              <button
                key={cat.id}
                className={`category-pill-btn ${selectedCategory === cat.id ? 'active' : ''}`}
                onClick={() => changeCategory(cat.id)}
              >
                {cat.name}
                <span className="category-pill-badge">{cat.unread > 0 ? cat.unread : cat.count}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Monocle Content Collection */}
      <div className="monocle-content-collection">
        {/* Row 1: Featured Highlight Article */}
        {featuredArticle ? (
          <div className={`monocle-featured-row ${animatingOutIds.has(featuredArticle.id) ? 'is-animating-out' : ''}`}>
            <div
              className={`monocle-featured-card ${featuredArticle.is_read ? 'is-read' : ''}`}
                data-id={featuredArticle.id}
              onClick={() => onSelectArticle && onSelectArticle(featuredArticle.id)}
            >
              <div className="monocle-featured-media">
                <ArticleCardSlideshow
                  article={featuredArticle}
                  mainImage={getArticleImage(featuredArticle)}
                  focalX={featuredArticle.focal_x}
                  focalY={featuredArticle.focal_y}
                  fallbackIconSize={64}
                />
                {hasRanking && (
                  <div className="monocle-media-feedback">
                    <FeedbackButtons
                      article={featuredArticle}
                      buttonClassName="monocle-media-feedback-btn"
                      onVoted={onVoted}
                    />
                  </div>
                )}
                <button
                  className={`quick-mark-read-btn ${featuredArticle.is_read ? 'is-read' : ''}`}
                  onClick={(e) => handleQuickMarkRead(e, featuredArticle)}
                  title={featuredArticle.is_read ? "Mark as Unread" : "Mark as Read"}
                >
                  <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
                    beenhere
                  </span>
                </button>
              </div>

              <div className="monocle-featured-body">
                <h2 className="monocle-featured-title">{getCleanTitle(featuredArticle)}</h2>
                {featuredSnippet && (
                  <p className="monocle-featured-excerpt">{featuredSnippet}</p>
                )}
                <CardMeta
                  article={featuredArticle}
                  timeLabel={formatRelativeDate(featuredArticle.pub_date || featuredArticle.created_at)}
                  faviconUrl={getFaviconUrl(featuredArticle)}
                  showIdentity
                  isRevealed={revealedMetaId === featuredArticle.id}
                  onRevealToggle={handleRevealToggle}
                />
              </div>
            </div>
          </div>
        ) : !hasMore && (
          <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
            No articles found matching filters.
          </div>
        )}

        {/* Grid Articles (Appended in batches of 8) */}
        {gridArticles.length > 0 && (
          <div className="monocle-four-col-grid">
            {gridArticles.map(article => {
              const img = getArticleImage(article);
              const snippet = getCleanSnippet(article, 1000);
              const favicon = getFaviconUrl(article);

              return (
                <div key={article.id} className={`monocle-grid-card-cell ${animatingOutIds.has(article.id) ? 'is-animating-out' : ''}`}>
                  <div
                    className={`monocle-grid-card ${article.is_read ? 'is-read' : ''}`}
                    data-id={article.id}
                    onClick={() => onSelectArticle && onSelectArticle(article.id)}
                  >
                    <div className="monocle-card-image-wrap">
                      <ArticleCardSlideshow
                        article={article}
                        mainImage={img}
                        focalX={article.focal_x}
                        focalY={article.focal_y}
                        fallbackIconSize={32}
                      />
                      {hasRanking && (
                        <div className="monocle-media-feedback">
                          <FeedbackButtons
                            article={article}
                            buttonClassName="monocle-media-feedback-btn"
                            onVoted={onVoted}
                          />
                        </div>
                      )}
                      <button
                        className={`quick-mark-read-btn ${article.is_read ? 'is-read' : ''}`}
                        onClick={(e) => handleQuickMarkRead(e, article)}
                        title={article.is_read ? "Mark as Unread" : "Mark as Read"}
                      >
                        <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
                          beenhere
                        </span>
                      </button>
                    </div>

                    <div className="monocle-card-content">
                      <h3 className="monocle-grid-card-title">{getCleanTitle(article)}</h3>
                      {snippet && <p className="monocle-grid-card-snippet">{snippet}</p>}
                      <div className="monocle-card-footer">
                        <CardMeta
                          article={article}
                          timeLabel={formatRelativeDate(article.pub_date || article.created_at)}
                          faviconUrl={favicon}
                          showIdentity
                          compact
                          isRevealed={revealedMetaId === article.id}
                          onRevealToggle={handleRevealToggle}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Infinite Scroll Sentinel or End of Collection Marker */}
        {renderScrollSentinelOrEnd(randomizedAllArticles.length)}
      </div>
    </div>
  );
}
