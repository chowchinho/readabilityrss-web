import React, { useEffect, useRef, useState, useMemo } from 'react';
import Typed from 'typed.js';
import { useNavigate } from 'react-router-dom';
import '../styles/reader.css';
import { useSwipe } from '../hooks/useSwipe';
import { API_URL, sendEvents, getSortMode } from '../api';
import { IMAGES_CACHE, FAVICONS_CACHE } from '../constants/caches';
import { makeSlug } from '../utils/slug';
import FeedbackButtons from './FeedbackButtons';
import ScoreBreakdown, { useScoreBreakdown } from './ScoreBreakdown';

// >5s is the threshold the dwell-time literature uses to separate an effective click
// from noise; below it, a click says more about navigation than about interest.
const EFFECTIVE_READ_MS = 5000;

export default function ArticleReader({ article, loading, error, onBack, onSwipeLeft, onSwipeRight, onToggleRead, onRetry, isDesktop, articles, toastMsg, onToastDismiss, isOffline, onVoted }) {
  const containerRef = useRef(null);
  const navigate = useNavigate();
  const typedElement = useRef(null);
  const typedInstance = useRef(null);
  const objectUrlsRef = useRef([]);
  const [renderedContent, setRenderedContent] = useState('');
  const [faviconSrc, setFaviconSrc] = useState(null);
  const [isLeaving, setIsLeaving] = useState(false);
  const [showInfo, setShowInfo] = useState(false);
  const [readRecorded, setReadRecorded] = useState(false);
  // Fetched on demand, exactly as the card popovers do - not shipped with the article.
  const { breakdown, breakdownError } = useScoreBreakdown(article?.id, showInfo);

  // Recorded here rather than at each card's click handler: every surface - the
  // column-2 feed, both index layouts, swipe navigation and a pasted URL - ends up
  // rendering this component, so one hook catches them all and cannot double-count.
  //
  // Firing on render turned keyboard traversal into reads: holding the arrow key
  // logged one 'open' per article stepped past, twelve a minute, indistinguishable
  // from a deliberate read. An article now has to stay on screen for EFFECTIVE_READ_MS
  // before it counts; leaving sooner records a 'skip' instead. The threshold is the
  // one the dwell-time literature uses to separate effective clicks from noise.
  //
  // Both events carry dwell_seconds, so the split can be re-cut later at analysis
  // time - the earlier version recorded no dwell at all and was unfilterable.
  const reportedIdRef = useRef(null);
  useEffect(() => {
    const id = article?.id;
    if (!id || reportedIdRef.current === id) return;

    const tags = {
      primary_topic: article.topics?.primary,
      secondary_topics: article.topics?.secondary,
      region: article.topics?.region,
      article_type: article.topics?.type,
    };
    const openedAt = Date.now();
    let settled = false;
    setReadRecorded(false);

    const report = (eventType) => {
      if (settled) return;
      settled = true;
      reportedIdRef.current = id;
      if (eventType === 'open') setReadRecorded(true);
      sendEvents([{
        article_id: id,
        event_type: eventType,
        dwell_seconds: Math.round((Date.now() - openedAt) / 1000),
        ...tags,
      }]).catch(() => {});
    };

    const timer = setTimeout(() => report('open'), EFFECTIVE_READ_MS);
    // A backgrounded tab is not being read, and the article may never unmount.
    const onHide = () => { if (document.visibilityState === 'hidden') report('skip'); };
    document.addEventListener('visibilitychange', onHide);

    return () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onHide);
      report('skip');
    };
  }, [article?.id]);

  useSwipe({
    onSwipeLeft,
    onSwipeRight: () => { onSwipeRight && onSwipeRight(); },
    enabled: !!article
  });

  useEffect(() => {
    if (article && containerRef.current) {
      containerRef.current.scrollTop = 0;
    }
  }, [article?.id]);

  useEffect(() => {
    const revokeObjectUrls = () => {
      objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      objectUrlsRef.current = [];
    };

    if (!article) {
      revokeObjectUrls();
      setRenderedContent('');
      setFaviconSrc(null);
      return undefined;
    }

    let cancelled = false;
    const baseContent = article.content
      ?.replace(/src="\/api\/reader\/image-proxy\?/g, `src="${API_URL}/api/reader/image-proxy?`)
      ?.replace(/src="\/api\/reader\/cached-image\//g, `src="${API_URL}/api/reader/cached-image/`)
      ?.replace(/srcset="\/api\/reader\/cached-image\//g, `srcset="${API_URL}/api/reader/cached-image/`)
      || '<p style="color: var(--text-muted); font-style: italic;">No content available for this article.</p>';
    const baseFavicon = article.source_favicon
      ? `${API_URL}${article.source_favicon}`
      : `https://www.google.com/s2/favicons?sz=32&domain_url=${encodeURIComponent(article.url)}`;

    const hydrateFromCache = async () => {
      revokeObjectUrls();

      if (!isOffline || !('caches' in window)) {
        if (!cancelled) {
          setRenderedContent(baseContent);
          setFaviconSrc(baseFavicon);
        }
        return;
      }

      try {
        const imageCache = await caches.open(IMAGES_CACHE);
        const faviconCache = await caches.open(FAVICONS_CACHE);
        const doc = new DOMParser().parseFromString(baseContent, 'text/html');

        const images = Array.from(doc.querySelectorAll('img[src]'));
        for (const img of images) {
          const src = img.getAttribute('src');
          if (!src || !src.startsWith('http')) continue;
          const cached = await imageCache.match(src);
          if (!cached) continue;
          const blob = await cached.blob();
          const blobUrl = URL.createObjectURL(blob);
          objectUrlsRef.current.push(blobUrl);
          img.setAttribute('src', blobUrl);
        }

        let cachedFaviconSrc = baseFavicon;
        if (baseFavicon.startsWith('http')) {
          const cachedFavicon = await faviconCache.match(baseFavicon);
          if (cachedFavicon) {
            const blob = await cachedFavicon.blob();
            cachedFaviconSrc = URL.createObjectURL(blob);
            objectUrlsRef.current.push(cachedFaviconSrc);
          }
        }

        if (!cancelled) {
          setRenderedContent(doc.body.innerHTML || baseContent);
          setFaviconSrc(cachedFaviconSrc);
        }
      } catch (err) {
        if (!cancelled) {
          setRenderedContent(baseContent);
          setFaviconSrc(baseFavicon);
        }
      }
    };

    hydrateFromCache();

    return () => {
      cancelled = true;
      revokeObjectUrls();
    };
  }, [article, isOffline]);

  const typingArticles = useMemo(() => {
    if (!articles || articles.length === 0 || article) return [];
    return articles.slice(0, 10);
  }, [articles, !!article]);

  const currentTypingArticleRef = useRef(null);

  const handleTypingClick = () => {
    const a = currentTypingArticleRef.current;
    if (!a) return;
    navigate(`/${makeSlug(a.source_id, a.source_name)}/${makeSlug(a.id, a.title)}`);
  };

  const handleTypingKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleTypingClick();
    }
  };

  useEffect(() => {
    if (!article && typingArticles.length > 0 && typedElement.current) {
      currentTypingArticleRef.current = typingArticles[0];

      typedInstance.current = new Typed(typedElement.current, {
        strings: typingArticles.map(a => `<strong>${a.source_name}</strong> · ${a.title}`),
        typeSpeed: 100,
        backSpeed: 30,
        backDelay: 7500,
        loop: true,
        shuffle: false,
        smartBackspace: false,
        contentType: 'html',
        preStringTyped: (arrayPos) => {
          currentTypingArticleRef.current = typingArticles[arrayPos] || null;
        }
      });

      return () => {
        if (typedInstance.current) typedInstance.current.destroy();
      };
    }
  }, [article, typingArticles]);

  if (!article) {
    return (
      <div className="reader-container empty-state">
        {loading && <div style={{ color: 'var(--text-muted)' }}>Loading...</div>}
        {!loading && !error && (
          <span
            className="typing-link"
            style={{ fontSize: '0.9em' }}
            onClick={handleTypingClick}
            onKeyDown={handleTypingKeyDown}
            role="link"
            tabIndex={0}
          >
            <span ref={typedElement}></span>
          </span>
        )}
        {error && (
          <div style={{ marginTop: '12px' }}>
            <div style={{ color: 'red' }}>{error}</div>
            {onRetry && (
              <button
                onClick={onRetry}
                style={{ marginTop: 8, padding: '6px 14px', cursor: 'pointer', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-primary)' }}
              >
                Try again
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  const handleShare = async () => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: article.title,
          url: article.url,
        });
      } catch (err) {
        console.error('Share failed', err);
      }
    } else {
      navigator.clipboard.writeText(article.url);
      alert('Link copied to clipboard');
    }
  };

  const pubDate = new Date(article.pub_date || article.created_at).toLocaleDateString(undefined, {
    year: 'numeric', month: 'long', day: 'numeric'
  });

  const score = typeof article.score === 'number' ? article.score.toFixed(2) : null;
  // Nothing to explain or to train under latest/random ordering. This also takes
  // the arrow-key votes out with it, since FeedbackButtons binds them.
  const hasRanking = getSortMode() === 'smart';

  const handleBack = () => {
    setIsLeaving(true);
    setTimeout(() => {
      setIsLeaving(false);
      onBack();
    }, 160);
  };

  return (
    <div className={`reader-container${isLeaving ? ' reader-leaving' : ''}`}>
      {toastMsg && (
        <div className="swipe-toast" onClick={onToastDismiss}>{toastMsg}</div>
      )}
      <div className="reader-actions-wrapper">
        <div className="reader-actions">
          {!isDesktop && (
            <button onClick={handleBack} className="action-btn back-btn" style={{ border: 'none', background: 'transparent', padding: '8px 4px' }}>
              <span className="material-symbols-outlined">arrow_back</span>
            </button>
          )}

          <div className="mobile-actions-spacer" style={{ flex: 1, display: !isDesktop ? 'block' : 'none' }}></div>

          <button onClick={onToggleRead} className="action-btn read-toggle-btn">
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>
              {article.is_read ? 'radio_button_unchecked' : 'check_circle'}
            </span>
            <span className="btn-label-unread">{article.is_read ? 'Keep Unread' : 'Mark Read'}</span>
          </button>

          <div className="mobile-actions-spacer" style={{ flex: 1, display: !isDesktop ? 'block' : 'none' }}></div>

          {/* Absolutely centred rather than flexed into place: the bar's two sides
              hold different amounts of content, so an in-flow group would sit off
              centre. On mobile the read toggle owns the centre and this drops back
              into flow — see reader.css. */}
          {hasRanking && (
            <div className="reader-feedback-center">
              <span
                className={`read-recorded ${readRecorded ? 'is-on' : ''}`}
                title={readRecorded
                  ? `Counted as read. Nudges ${[article.topics?.primary, article.topics?.type, article.topics?.region].filter(Boolean).join(', ') || 'this article\'s labels'} up slightly.`
                  : 'Not counted yet — stay 5 seconds'}
                aria-hidden={!readRecorded}
              >
                <span className="material-symbols-outlined">visibility</span>
              </span>
              {/* onVoted is not optional. Without it a vote cast here reaches the
                  server and this button's local state, but never App's articles - and
                  returning to the article reuses that stale entry without refetching,
                  so the thumb comes back blank. */}
              <FeedbackButtons
                article={article}
                buttonClassName="action-btn reader-feedback-btn"
                keyboardShortcuts
                onVoted={onVoted}
              />
            </div>
          )}

          <div className="right-actions" style={{ display: 'flex', gap: 8, marginLeft: isDesktop ? 'auto' : '0' }}>
            <a href={article.url} target="_blank" rel="noopener noreferrer" className="action-btn">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>open_in_new</span>
              <span className="btn-label">Open Original</span>
            </a>

            <button onClick={handleShare} className="action-btn">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>share</span>
              <span className="btn-label">Share</span>
            </button>
          </div>
        </div>
      </div>

      <div className="reader-scroll-area" ref={containerRef}>
        <div className="reader-header">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
            <h1 className="reader-title" style={{ marginBottom: 0, flex: 1 }}>{article.title}</h1>
          </div>
          <div className="reader-meta" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div className="favicon-wrapper" style={{ marginRight: 0 }}>
              <img
                src={faviconSrc || `https://www.google.com/s2/favicons?sz=32&domain_url=${encodeURIComponent(article.url)}`}
                alt=""
                className="reader-favicon"
                onError={(e) => {
                  e.target.style.display = 'none';
                  const fallback = e.target.parentElement?.querySelector('.favicon-fallback');
                  if (fallback) fallback.style.display = 'inline-block';
                }}
              />
              <span className="material-symbols-outlined favicon-fallback" style={{ display: 'none', fontSize: '18px', color: 'var(--text-secondary)' }}>
                rss_feed
              </span>
            </div>
            <span style={{ fontWeight: 600 }}>{article.source_name}</span>
            <span>·</span>
            <span>{pubDate}</span>
            {article.is_cached && (
              <>
                <span className="reader-meta-dot">·</span>
                <span className="cache-badge">Cache</span>
              </>
            )}
            {hasRanking && (
              <button
                onClick={() => setShowInfo((v) => !v)}
                className={`reader-meta-info-btn ${showInfo ? 'active' : ''}`}
                title="Why this is here"
                aria-label="Why this is here"
                aria-expanded={showInfo}
              >
                <span className="material-symbols-outlined">info</span>
              </button>
            )}
          </div>
          {hasRanking && showInfo && (
            <div className="ranking-info-popover reader-info-panel">
              {/* Same component the card popovers use - see ScoreBreakdown.jsx. This
                  panel used to carry only tags, so the reader explained less than a
                  hover on a card did. */}
              <ScoreBreakdown
                article={article}
                breakdown={breakdown}
                breakdownError={breakdownError}
                showReason
              />
            </div>
          )}
        </div>

        <div
          className="article-content"
          dangerouslySetInnerHTML={{ __html: renderedContent }}
        />
      </div>
    </div>
  );
}
