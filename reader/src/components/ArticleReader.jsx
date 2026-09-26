import React, { useEffect, useRef, useState, useMemo } from 'react';
import Typed from 'typed.js';
import { useNavigate } from 'react-router-dom';
import '../styles/reader.css';
import { useSwipe } from '../hooks/useSwipe';
import { API_URL, sendEvents, getSortMode, translateArticle, getArticle } from '../api';
import { saveArticlesToDB } from '../db';
import { IMAGES_CACHE, FAVICONS_CACHE } from '../constants/caches';
import { makeSlug } from '../utils/slug';
import FeedbackButtons from './FeedbackButtons';
import ScoreBreakdown, { useScoreBreakdown } from './ScoreBreakdown';
import { findTopLevelBlocks } from '../utils/readDepth';
import { useReadCompletion } from '../hooks/useReadCompletion';

// >5s is the threshold the dwell-time literature uses to separate an effective click
// from noise; below it, a click says more about navigation than about interest.
const EFFECTIVE_READ_MS = 5000;

function applyBlockSubstitutions(originalHtml, blockMap) {
  if (!originalHtml || Object.keys(blockMap).length === 0) return originalHtml;
  const doc = new DOMParser().parseFromString(originalHtml, 'text/html');
  const topElements = findTopLevelBlocks(doc.body);
  for (const [idxStr, replacementHtml] of Object.entries(blockMap)) {
    const idx = parseInt(idxStr, 10);
    const target = topElements[idx];
    if (target && replacementHtml) {
      const template = doc.createElement('template');
      template.innerHTML = replacementHtml;
      target.replaceWith(...Array.from(template.content.childNodes));
    }
  }
  return doc.body.innerHTML;
}

export default function ArticleReader({ article, loading, error, onBack, onSwipeLeft, onSwipeRight, onToggleRead, onRetry, isDesktop, articles, toastMsg, onToastDismiss, isOffline, onVoted }) {
  const containerRef = useRef(null);
  const contentRef = useRef(null);
  const navigate = useNavigate();
  const typedElement = useRef(null);
  const typedInstance = useRef(null);
  const objectUrlsRef = useRef([]);
  const [renderedContent, setRenderedContent] = useState('');
  const [faviconSrc, setFaviconSrc] = useState(null);
  const [isLeaving, setIsLeaving] = useState(false);
  const [showInfo, setShowInfo] = useState(false);
  const [readRecorded, setReadRecorded] = useState(false);

  // On-demand translation state
  const [localArticle, setLocalArticle] = useState(null);
  const [translatedTitle, setTranslatedTitle] = useState(null);
  const [isTranslating, setIsTranslating] = useState(false);
  const [translatedBlockCount, setTranslatedBlockCount] = useState(0);
  const [translateError, setTranslateError] = useState(null);
  const blocksRef = useRef({});
  const baseContentRef = useRef('');
  const activeArticleIdRef = useRef(null);
  const errorTimerRef = useRef(null);

  const effectiveArticle = localArticle || article;

  useEffect(() => {
    setLocalArticle(null);
    setTranslatedTitle(null);
    setIsTranslating(false);
    setTranslatedBlockCount(0);
    setTranslateError(null);
    blocksRef.current = {};
    activeArticleIdRef.current = article?.id;
  }, [article?.id]);

  useEffect(() => {
    return () => {
      if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    };
  }, []);

  // Fetched on demand, exactly as the card popovers do - not shipped with the article.
  const { breakdown, breakdownError } = useScoreBreakdown(effectiveArticle?.id, showInfo);

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
    const id = effectiveArticle?.id;
    if (!id || reportedIdRef.current === id) return;

    const tags = {
      primary_topic: effectiveArticle.topics?.primary,
      secondary_topics: effectiveArticle.topics?.secondary,
      region: effectiveArticle.topics?.region,
      article_type: effectiveArticle.topics?.type,
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
  }, [effectiveArticle?.id]);

  useReadCompletion({
    article: effectiveArticle,
    scrollRef: containerRef,
    contentRef,
    contentKey: renderedContent,
  });

  useSwipe({
    onSwipeLeft,
    onSwipeRight: () => { onSwipeRight && onSwipeRight(); },
    enabled: !!effectiveArticle
  });

  useEffect(() => {
    if (effectiveArticle && containerRef.current) {
      containerRef.current.scrollTop = 0;
    }
  }, [effectiveArticle?.id]);

  useEffect(() => {
    const revokeObjectUrls = () => {
      objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      objectUrlsRef.current = [];
    };

    if (!effectiveArticle) {
      revokeObjectUrls();
      setRenderedContent('');
      setFaviconSrc(null);
      baseContentRef.current = '';
      return undefined;
    }

    let cancelled = false;
    const baseContent = effectiveArticle.content
      ?.replace(/src="\/api\/reader\/image-proxy\?/g, `src="${API_URL}/api/reader/image-proxy?`)
      ?.replace(/src="\/api\/reader\/cached-image\//g, `src="${API_URL}/api/reader/cached-image/`)
      ?.replace(/srcset="\/api\/reader\/cached-image\//g, `srcset="${API_URL}/api/reader/cached-image/`)
      || '<p style="color: var(--text-muted); font-style: italic;">No content available for this article.</p>';
    baseContentRef.current = baseContent;
    const baseFavicon = effectiveArticle.source_favicon
      ? `${API_URL}${effectiveArticle.source_favicon}`
      : `https://www.google.com/s2/favicons?sz=32&domain_url=${encodeURIComponent(effectiveArticle.url)}`;

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
  }, [effectiveArticle, isOffline]);

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

  const showTranslateError = (msg) => {
    setTranslateError(msg);
    if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    errorTimerRef.current = setTimeout(() => setTranslateError(null), 4000);
  };

  const handleTranslate = async () => {
    if (!effectiveArticle?.id || isTranslating) return;
    const targetId = effectiveArticle.id;
    activeArticleIdRef.current = targetId;
    setIsTranslating(true);
    setTranslatedBlockCount(0);
    setTranslateError(null);
    blocksRef.current = {};

    try {
      await translateArticle(targetId, async (event) => {
        if (activeArticleIdRef.current !== targetId) return;

        if (event.type === 'start') {
          setIsTranslating(true);
        } else if (event.type === 'title') {
          if (event.html) {
            setTranslatedTitle(event.html);
          }
        } else if (event.type === 'block') {
          const preparedHtml = API_URL
            ? event.html?.replace(/src="\/api\/reader\/image-proxy\?/g, `src="${API_URL}/api/reader/image-proxy?`)
            : event.html;
          const nextBlocks = { ...blocksRef.current, [event.index]: preparedHtml };
          blocksRef.current = nextBlocks;
          setTranslatedBlockCount(Object.keys(nextBlocks).length);
          const newBody = applyBlockSubstitutions(baseContentRef.current, nextBlocks);
          setRenderedContent(newBody);
        } else if (event.type === 'error') {
          setIsTranslating(false);
          showTranslateError(event.message || 'Translation failed');
        } else if (event.type === 'done') {
          try {
            const fresh = await getArticle(targetId);
            if (activeArticleIdRef.current === targetId && fresh) {
              setLocalArticle(fresh);
              await saveArticlesToDB([fresh]).catch(e => console.warn('Failed to cache translated article', e));
            }
          } catch (e) {
            console.warn('Failed to refetch article on done', e);
          } finally {
            if (activeArticleIdRef.current === targetId) {
              setIsTranslating(false);
            }
          }
        }
      });
    } catch (err) {
      if (activeArticleIdRef.current === targetId) {
        setIsTranslating(false);
        showTranslateError(err.message || 'Translation failed');
      }
    }
  };

  const CHINESE_LANGS = ['zh', 'zh-tw', 'zh-cn', 'zh-hk', 'zh-hant', 'zh-hans'];
  const isChineseFeed = Boolean(
    effectiveArticle?.detected_language &&
    CHINESE_LANGS.includes(effectiveArticle.detected_language.toLowerCase().trim())
  );
  const isAlreadyTranslated = Boolean(
    effectiveArticle?.content &&
    effectiveArticle.content.slice(0, 400).includes('Translated by')
  );
  const canTranslate = Boolean(effectiveArticle && !isAlreadyTranslated && !isChineseFeed);

  const handleShare = async () => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: effectiveArticle.title,
          url: effectiveArticle.url,
        });
      } catch (err) {
        console.error('Share failed', err);
      }
    } else {
      navigator.clipboard.writeText(effectiveArticle.url);
      alert('Link copied to clipboard');
    }
  };

  const pubDate = new Date(effectiveArticle.pub_date || effectiveArticle.created_at).toLocaleDateString(undefined, {
    year: 'numeric', month: 'long', day: 'numeric'
  });

  const score = typeof effectiveArticle.score === 'number' ? effectiveArticle.score.toFixed(2) : null;
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
      {(toastMsg || translateError) && (
        <div
          className="swipe-toast"
          onClick={() => {
            if (onToastDismiss) onToastDismiss();
            setTranslateError(null);
          }}
        >
          {translateError || toastMsg}
        </div>
      )}
      <div className="reader-actions-wrapper">
        <div className="reader-actions">
          {!isDesktop && (
            <button onClick={handleBack} className="action-btn back-btn" style={{ border: 'none', background: 'transparent', padding: '8px 4px' }}>
              <span className="material-symbols-outlined">arrow_back</span>
            </button>
          )}

          <button
            onClick={onToggleRead}
            className="action-btn read-toggle-btn"
            title={effectiveArticle.is_read ? 'Keep Unread' : 'Mark Read'}
            aria-label={effectiveArticle.is_read ? 'Keep Unread' : 'Mark Read'}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>
              {effectiveArticle.is_read ? 'radio_button_unchecked' : 'check_circle'}
            </span>
            <span className="btn-label-unread">{effectiveArticle.is_read ? 'Keep Unread' : 'Mark Read'}</span>
          </button>

          <div className="mobile-actions-spacer" style={{ flex: 1, display: !isDesktop ? 'block' : 'none' }}></div>

          {/* Absolutely centred rather than flexed into place: the bar's two sides
              hold different amounts of content, so an in-flow group would sit off
              centre. */}
          {hasRanking && (
            <div className="reader-feedback-center">
              <span
                className={`read-recorded ${readRecorded ? 'is-on' : ''}`}
                title={readRecorded
                  ? `Counted as read. Nudges ${[effectiveArticle.topics?.primary, effectiveArticle.topics?.type, effectiveArticle.topics?.region].filter(Boolean).join(', ') || 'this article\'s labels'} up slightly.`
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
                article={effectiveArticle}
                buttonClassName="action-btn reader-feedback-btn"
                keyboardShortcuts
                onVoted={onVoted}
              />
            </div>
          )}

          <div className="right-actions" style={{ display: 'flex', gap: 8, marginLeft: isDesktop ? 'auto' : '0' }}>
            {canTranslate && (
              <button
                onClick={handleTranslate}
                disabled={isTranslating}
                className="action-btn"
                title={isTranslating ? `Translating (${translatedBlockCount})...` : 'Translate'}
                aria-label="Translate"
              >
                <span className="material-symbols-outlined" style={{ fontSize: 18 }}>translate</span>
                {isTranslating ? (
                  <span style={{ fontSize: '11px', fontWeight: 600, marginLeft: 4 }}>
                    {translatedBlockCount > 0 ? translatedBlockCount : '...'}
                  </span>
                ) : (
                  <span className="btn-label">Translate</span>
                )}
              </button>
            )}

            <a href={effectiveArticle.url} target="_blank" rel="noopener noreferrer" className="action-btn" title="Open Original" aria-label="Open Original">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>open_in_new</span>
              <span className="btn-label">Open Original</span>
            </a>

            <button onClick={handleShare} className="action-btn" title="Share" aria-label="Share">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>share</span>
              <span className="btn-label">Share</span>
            </button>
          </div>
        </div>
      </div>

      <div className="reader-scroll-area" ref={containerRef}>
        <div className="reader-header">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
            <h1 className="reader-title" style={{ marginBottom: 0, flex: 1 }}>{translatedTitle || effectiveArticle.title}</h1>
          </div>
          <div className="reader-meta" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div className="favicon-wrapper" style={{ marginRight: 0 }}>
              <img
                src={faviconSrc || `https://www.google.com/s2/favicons?sz=32&domain_url=${encodeURIComponent(effectiveArticle.url)}`}
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
            <span style={{ fontWeight: 600 }}>{effectiveArticle.source_name}</span>
            <span>·</span>
            <span>{pubDate}</span>
            {effectiveArticle.is_cached && (
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
                article={effectiveArticle}
                breakdown={breakdown}
                breakdownError={breakdownError}
                showReason
              />
            </div>
          )}
        </div>

        <div
          ref={contentRef}
          className="article-content"
          dangerouslySetInnerHTML={{ __html: renderedContent }}
        />
      </div>
    </div>
  );
}
