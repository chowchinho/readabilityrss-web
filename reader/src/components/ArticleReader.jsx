import React, { useEffect, useLayoutEffect, useRef, useState, useMemo } from 'react';
import Typed from 'typed.js';
import { useNavigate } from 'react-router-dom';
import '../styles/reader.css';
import { useSwipe } from '../hooks/useSwipe';
import { API_URL, sendEvents, getSortMode, translateArticle, getArticle } from '../api';
import { saveArticlesToDB } from '../db';
import { IMAGES_CACHE, FAVICONS_CACHE } from '../constants/caches';
import { makeSlug } from '../utils/slug';
import { translationLabel, estimateReadingMinutes } from '../utils/articleText';
import { notify } from '../toast';
import FeedbackButtons from './FeedbackButtons';
import ScoreBreakdown, { useScoreBreakdown } from './ScoreBreakdown';
import { findTopLevelBlocks } from '../utils/readDepth';
import { useReadCompletion } from '../hooks/useReadCompletion';
import TranslationBar, { translationDetail } from './TranslationBar';
import ReaderMobileToolbar from './ReaderMobileToolbar';
import { Popover } from '@base-ui/react/popover';
import TextSizeControl from './TextSizeControl';
import {
  buildTranslationView, providerFromContent,
  loadTranslationView, saveTranslationView,
  loadTextScaleIndex, saveTextScaleIndex, TEXT_SCALES,
} from '../utils/translationView';

// >5s is the threshold the dwell-time literature uses to separate an effective click
// from noise; below it, a click says more about navigation than about interest.
const EFFECTIVE_READ_MS = 5000;

// freshIndex marks the block that just arrived, so only it animates in; every
// earlier block is re-rendered here too but must not replay its entrance.
// markPending dims the text blocks still waiting for their translation.
function applyBlockSubstitutions(originalHtml, blockMap, freshIndex = null, markPending = false) {
  if (!originalHtml || (!markPending && Object.keys(blockMap).length === 0)) return originalHtml;
  const doc = new DOMParser().parseFromString(originalHtml, 'text/html');
  const topElements = findTopLevelBlocks(doc.body);
  if (markPending) {
    topElements.forEach((el, i) => {
      if (!blockMap[i] && (el.textContent || '').trim()) el.classList.add('rr-pending-block');
    });
  }
  for (const [idxStr, replacementHtml] of Object.entries(blockMap)) {
    const idx = parseInt(idxStr, 10);
    const target = topElements[idx];
    if (target && replacementHtml) {
      const template = doc.createElement('template');
      template.innerHTML = replacementHtml;
      if (idx === freshIndex) {
        template.content.querySelectorAll(':scope > *').forEach(el => el.classList.add('rr-fresh-block'));
      }
      target.replaceWith(...Array.from(template.content.childNodes));
    }
  }
  return doc.body.innerHTML;
}

// The toolbar floats over the top of the scroller, so text is only readable from
// its lower edge down.
function visibleTop(scroller, bar) {
  return scroller.getBoundingClientRect().top + (bar ? bar.offsetHeight : 0);
}

// What the on-demand stream will send a block for: top-level blocks with text.
// The stream itself announces no total, so the progress ring counts these.
function countTranslatableBlocks(html) {
  if (!html) return 0;
  const doc = new DOMParser().parseFromString(html, 'text/html');
  return findTopLevelBlocks(doc.body).filter((el) => (el.textContent || '').trim()).length;
}

// Identifies one image file across the URL shapes it arrives in: a cached-image
// path, an image-proxy URL carrying the original, or the original itself.
function imageKey(src) {
  if (!src) return null;
  try {
    const u = new URL(src, window.location.origin);
    const original = u.searchParams.get('url');
    if (original) return imageKey(original);
    return u.pathname.split('/').pop() || null;
  } catch {
    return null;
  }
}

// Below this the hero would be an upscale on a phone, so the photo stays in the text.
const MIN_HERO_WIDTH = 600;

export default function ArticleReader({ article, loading, error, onBack, onSwipeLeft, onSwipeRight, onToggleRead, onHide, onRetry, isDesktop, articles, isOffline, onVoted }) {
  const containerRef = useRef(null);
  const contentRef = useRef(null);
  const pageRef = useRef(null);
  const barRef = useRef(null);
  const progressRef = useRef(null);
  const navigate = useNavigate();
  const typedElement = useRef(null);
  const typedInstance = useRef(null);
  const objectUrlsRef = useRef([]);
  const [renderedContent, setRenderedContent] = useState('');
  const [faviconSrc, setFaviconSrc] = useState(null);
  const [isLeaving, setIsLeaving] = useState(false);
  const [showInfo, setShowInfo] = useState(false);
  const [readRecorded, setReadRecorded] = useState(false);
  const mobileBarRef = useRef(null);
  const [switchOpen, setSwitchOpen] = useState(false);
  const [translateTotal, setTranslateTotal] = useState(0);
  // 'pending' keeps the hero's space while it loads, so the text does not jump down
  // once it arrives; 'none' collapses it when the image is small or fails.
  const [heroState, setHeroState] = useState('pending');

  // On-demand translation state
  const [localArticle, setLocalArticle] = useState(null);
  const [translatedTitle, setTranslatedTitle] = useState(null);
  const [isTranslating, setIsTranslating] = useState(false);
  const [translatedBlockCount, setTranslatedBlockCount] = useState(0);
  const blocksRef = useRef({});
  const baseContentRef = useRef('');
  const activeArticleIdRef = useRef(null);

  const effectiveArticle = localArticle || article;

  // Phones show two sets of thumbs (the shortcut at the top and the end card), and
  // the article object here is not refreshed after a vote, so both read from this.
  const [localVote, setLocalVote] = useState(undefined);
  const voteArticle = useMemo(
    () => (effectiveArticle && localVote !== undefined ? { ...effectiveArticle, vote: localVote } : effectiveArticle),
    [effectiveArticle, localVote]
  );
  const handleVote = (id, vote) => {
    setLocalVote(vote);
    if (onVoted) onVoted(id, vote);
  };

  // Reading preferences, not article state: they survive moving between articles
  // and reloads, so they live in this component's state seeded from storage.
  const [translationView, setTranslationView] = useState(loadTranslationView);
  const [textScaleIndex, setTextScaleIndex] = useState(loadTextScaleIndex);
  const viewAnchorRef = useRef(null);

  const sourceLang = effectiveArticle?.translated_from || effectiveArticle?.detected_language || null;
  const view = useMemo(
    () => buildTranslationView(renderedContent, translationView, sourceLang),
    [renderedContent, translationView, sourceLang]
  );
  const hasPairs = view.pairCount > 0;
  const provider = useMemo(() => providerFromContent(effectiveArticle?.content), [effectiveArticle?.content]);

  // Changing view reflows the whole article. Remember which passage is at the top
  // of the pane, then put that same passage back at the same height afterwards,
  // so the reader keeps their place instead of being thrown up or down the page.
  const changeTranslationView = (next) => {
    if (next === translationView) return;
    const scroller = containerRef.current;
    const content = contentRef.current;
    if (scroller && content) {
      const top = visibleTop(scroller, barRef.current);
      const anchor = Array.from(content.querySelectorAll('[data-pair]'))
        .find((el) => el.getBoundingClientRect().bottom > top + 8);
      viewAnchorRef.current = anchor
        ? { pair: anchor.getAttribute('data-pair'), offset: anchor.getBoundingClientRect().top - top }
        : { pair: null };
    }
    setTranslationView(next);
    saveTranslationView(next);
  };

  useLayoutEffect(() => {
    const anchor = viewAnchorRef.current;
    viewAnchorRef.current = null;
    const scroller = containerRef.current;
    const content = contentRef.current;
    // Only a user's change animates; the first render of an article does not.
    if (!anchor || !scroller || !content) return;
    if (anchor.pair !== null) {
      const el = content.querySelector(`[data-pair="${anchor.pair}"]`);
      if (el) {
        const top = visibleTop(scroller, barRef.current);
        scroller.scrollTop += (el.getBoundingClientRect().top - top) - anchor.offset;
      }
    }
    // A short fade bridges the swap; opacity only, so it is safe with reduced motion.
    content.animate?.([{ opacity: 0.35 }, { opacity: 1 }], { duration: 180, easing: 'ease-out' });
  }, [translationView]);

  const changeTextScale = (index) => {
    setTextScaleIndex(index);
    saveTextScaleIndex(index);
  };

  // Photos at least as wide as the text column break out of it to the pane's
  // reading edges (see .article-content img[data-wide]). Decided per image once it
  // has loaded, because a small inline graphic must never be stretched.
  useEffect(() => {
    const content = contentRef.current;
    if (!content) return undefined;
    const mark = (img) => {
      const column = content.clientWidth;
      if (img.naturalWidth >= Math.min(column * 0.8, 600)) img.setAttribute('data-wide', '');
    };
    const imgs = Array.from(content.querySelectorAll('img'));
    imgs.forEach((img) => {
      if (img.complete && img.naturalWidth) mark(img);
      else img.addEventListener('load', () => mark(img), { once: true });
    });
    return undefined;
  }, [view.html]);

  const heroSrc = !isDesktop && !isOffline && effectiveArticle
    ? (effectiveArticle.main_image_proxy
        ? (effectiveArticle.main_image_proxy.startsWith('http') ? effectiveArticle.main_image_proxy : `${API_URL}${effectiveArticle.main_image_proxy}`)
        : effectiveArticle.main_image)
    : null;
  const showHero = Boolean(heroSrc) && heroState !== 'none';

  // The hero is the article's main image; the same file in the text below would
  // show the photo twice.
  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const key = showHero ? imageKey(heroSrc) : null;
    content.querySelectorAll('img[data-hero-dup]').forEach((img) => img.removeAttribute('data-hero-dup'));
    if (!key) return;
    const dup = Array.from(content.querySelectorAll('img')).find((img) => imageKey(img.getAttribute('src')) === key);
    if (dup) dup.setAttribute('data-hero-dup', '');
  }, [view.html, heroSrc, showHero]);

  useEffect(() => {
    setLocalArticle(null);
    setTranslatedTitle(null);
    setIsTranslating(false);
    setTranslatedBlockCount(0);
    setTranslateTotal(0);
    setSwitchOpen(false);
    setHeroState('pending');
    setLocalVote(undefined);
    blocksRef.current = {};
    activeArticleIdRef.current = article?.id;
  }, [article?.id]);


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

  // Written straight to the element's transform: a state update per scroll event
  // would re-render the whole article.
  useEffect(() => {
    const scroller = containerRef.current;
    const bar = progressRef.current;
    if (!scroller || !bar) return undefined;
    let frame = 0;
    let lastTop = scroller.scrollTop;
    const update = () => {
      frame = 0;
      const top = scroller.scrollTop;
      const max = scroller.scrollHeight - scroller.clientHeight;
      const ratio = max > 0 ? Math.min(1, top / max) : 0;
      bar.style.transform = `scaleX(${ratio})`;

      // Phone toolbar: out of the way while reading down, back on any upward
      // scroll, near the top, and at the end of the article.
      const mbar = mobileBarRef.current;
      if (mbar) {
        const delta = top - lastTop;
        if (top < 80 || max - top < 120 || delta < -6) mbar.removeAttribute('data-hidden');
        else if (delta > 6) mbar.setAttribute('data-hidden', '');
      }
      lastTop = top;
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };
    update();
    scroller.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      scroller.removeEventListener('scroll', onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [article?.id, renderedContent]);

  useReadCompletion({
    article: effectiveArticle,
    scrollRef: containerRef,
    contentRef,
    contentKey: view.html,
  });

  const swipeIndex = articles && effectiveArticle
    ? articles.findIndex(a => String(a.id) === String(effectiveArticle.id))
    : -1;

  useSwipe({
    onSwipeLeft,
    onSwipeRight: () => { onSwipeRight && onSwipeRight(); },
    targetRef: pageRef,
    canSwipeLeft: swipeIndex >= 0 && swipeIndex < articles.length - 1,
    canSwipeRight: swipeIndex > 0,
    enabled: !!effectiveArticle && !isDesktop
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
          <div className="reader-empty-error">
            <div>{error}</div>
            {onRetry && (
              <button onClick={onRetry} className="action-btn reader-retry-btn">
                Try again
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  const showTranslateError = (msg) => {
    notify(msg, { type: 'error', timeout: 4000 });
  };

  const handleTranslate = async () => {
    if (!effectiveArticle?.id || isTranslating) return;
    const targetId = effectiveArticle.id;
    activeArticleIdRef.current = targetId;
    setIsTranslating(true);
    setTranslatedBlockCount(0);
    setTranslateTotal(countTranslatableBlocks(baseContentRef.current));
    blocksRef.current = {};
    setRenderedContent(applyBlockSubstitutions(baseContentRef.current, {}, null, true));

    try {
      await translateArticle(targetId, async (event) => {
        if (activeArticleIdRef.current !== targetId) return;

        if (event.type === 'start') {
          setIsTranslating(true);
        } else if (event.type === 'source') {
          // The server's own block count replaces the client's estimate.
          if (event.total > 0) setTranslateTotal(event.total);
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
          const newBody = applyBlockSubstitutions(baseContentRef.current, nextBlocks, Number(event.index), true);
          setRenderedContent(newBody);
        } else if (event.type === 'error') {
          setIsTranslating(false);
          setRenderedContent(applyBlockSubstitutions(baseContentRef.current, blocksRef.current));
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
            if (activeArticleIdRef.current === targetId) {
              setRenderedContent(applyBlockSubstitutions(baseContentRef.current, blocksRef.current));
            }
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
        setRenderedContent(applyBlockSubstitutions(baseContentRef.current, blocksRef.current));
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

  const handleCopyLink = async () => {
    try {
      await navigator.clipboard.writeText(effectiveArticle.url);
      notify('Link copied');
    } catch {
      notify('Could not copy the link', { type: 'error' });
    }
  };

  const handleWhy = () => {
    setShowInfo(true);
    containerRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const nextArticle = swipeIndex >= 0 && articles && swipeIndex < articles.length - 1
    ? articles[swipeIndex + 1]
    : null;
  const nextImage = nextArticle
    ? (nextArticle.main_image_proxy
        ? (nextArticle.main_image_proxy.startsWith('http') ? nextArticle.main_image_proxy : `${API_URL}${nextArticle.main_image_proxy}`)
        : nextArticle.main_image)
    : null;
  const detail = translationDetail(sourceLang, provider);

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
      notify('Link copied');
    }
  };

  const langPill = translationLabel(effectiveArticle);
  const readingMinutes = estimateReadingMinutes(effectiveArticle.content);
  const pubDate = new Date(effectiveArticle.pub_date || effectiveArticle.created_at).toLocaleDateString(undefined, {
    // The short month keeps the meta line to one row on a phone.
    year: 'numeric', month: isDesktop ? 'long' : 'short', day: 'numeric'
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
      {!isDesktop && (
        <>
          <div className="reader-progress is-phone" aria-hidden="true"><i ref={progressRef} /></div>
          <button className="reader-float-btn is-back" onClick={handleBack} aria-label="Back" title="Back">
            <span className="material-symbols-outlined">chevron_left</span>
          </button>
          {hasRanking && (
            <Popover.Root>
              <Popover.Trigger className="reader-float-btn is-votes" aria-label="Rate this article" title="Rate this article">
                <span className="material-symbols-outlined">thumbs_up_down</span>
              </Popover.Trigger>
              <Popover.Portal>
                <Popover.Positioner side="bottom" align="end" sideOffset={8} collisionPadding={12} className="mbar-positioner">
                  <Popover.Popup className="mbar-sheet votes-sheet">
                    <FeedbackButtons
                      article={voteArticle}
                      buttonClassName="end-vote-btn"
                      onVoted={handleVote}
                    />
                  </Popover.Popup>
                </Popover.Positioner>
              </Popover.Portal>
            </Popover.Root>
          )}
        </>
      )}
      <div className="reader-scroll-area" ref={containerRef}>
      {/* Inside the scroller and sticky, so the article passes beneath the bar's
          translucent material and the scrollbar stays clear of it. */}
      {isDesktop && (
      <div className="reader-actions-wrapper" ref={barRef}>
        <div className="reader-progress" aria-hidden="true"><i ref={progressRef} /></div>
        <div className="reader-actions">
          {!isDesktop && (
            <button onClick={handleBack} className="action-btn back-btn" style={{ border: 'none', background: 'transparent', padding: '8px 4px' }}>
              <span className="material-symbols-outlined">arrow_back</span>
            </button>
          )}
          {isDesktop && (
            <>
              <button onClick={onBack} className="action-btn icon-only" title="Back to index" aria-label="Back to index">
                <span className="material-symbols-outlined">arrow_back</span>
              </button>
              <button onClick={onSwipeRight} className="action-btn icon-only" title="Previous article" aria-label="Previous article">
                <span className="material-symbols-outlined">keyboard_arrow_up</span>
              </button>
              <button onClick={onSwipeLeft} className="action-btn icon-only" title="Next article" aria-label="Next article">
                <span className="material-symbols-outlined">keyboard_arrow_down</span>
              </button>
              <span className="action-sep" aria-hidden="true" />
            </>
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
                className={`action-btn${isTranslating ? '' : ' icon-only'}`}
                title={isTranslating ? `Translating (${translatedBlockCount})...` : 'Translate'}
                aria-label="Translate"
              >
                <span className="material-symbols-outlined" style={{ fontSize: 18 }}>translate</span>
                {isTranslating && (
                  <span style={{ fontSize: '0.6875rem', fontWeight: 600, marginLeft: 4 }}>
                    {translatedBlockCount > 0 ? translatedBlockCount : '...'}
                  </span>
                )}
              </button>
            )}

            <TextSizeControl index={textScaleIndex} onChange={changeTextScale} />

            <a href={effectiveArticle.url} target="_blank" rel="noopener noreferrer" className="action-btn icon-only" title="Open Original" aria-label="Open Original">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>open_in_new</span>
            </a>

            <button onClick={handleShare} className="action-btn icon-only" title="Share" aria-label="Share">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>share</span>
            </button>
          </div>
        </div>
      </div>
      )}

      <div className="reader-page" ref={pageRef}>
        {showHero && (
          <div className={`reader-hero${heroState === 'ok' ? ' is-loaded' : ''}`}>
            <img
              key={heroSrc}
              src={heroSrc}
              alt=""
              ref={(el) => {
                if (el && el.complete && el.naturalWidth && heroState === 'pending') {
                  setHeroState(el.naturalWidth >= MIN_HERO_WIDTH ? 'ok' : 'none');
                }
              }}
              onLoad={(e) => setHeroState(e.currentTarget.naturalWidth >= MIN_HERO_WIDTH ? 'ok' : 'none')}
              onError={() => setHeroState('none')}
              style={{ objectPosition: `${effectiveArticle.focal_x ?? 50}% ${effectiveArticle.focal_y ?? 50}%` }}
            />
          </div>
        )}
        <div className={`reader-header${!isDesktop && !showHero ? ' has-float-chrome' : ''}`}>
          <div className="reader-meta">
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
            <span className="reader-source">{effectiveArticle.source_name}</span>
            <span className="reader-meta-dot" aria-hidden="true">·</span>
            <span>{pubDate}</span>
            {readingMinutes > 0 && (
              <>
                <span className="reader-meta-dot" aria-hidden="true">·</span>
                <span>{readingMinutes} min read</span>
              </>
            )}
            {langPill && <span className="lang-pill" title="Translated">{langPill}</span>}
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
          {hasPairs && translationView === 'original' && effectiveArticle.original_title && !translatedTitle ? (
            <h1 className="reader-title" lang={sourceLang || undefined}>{effectiveArticle.original_title}</h1>
          ) : (
            <h1 className="reader-title">{translatedTitle || effectiveArticle.title}</h1>
          )}
          {effectiveArticle.original_title && !translatedTitle && (!hasPairs || translationView === 'both') && (
            <p className="reader-original-title" lang={sourceLang || undefined}>
              {effectiveArticle.original_title}
            </p>
          )}
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
          {isDesktop && hasPairs && (
            <TranslationBar
              sourceLang={sourceLang}
              provider={provider}
              view={translationView}
              onViewChange={changeTranslationView}
            />
          )}
          {/* Phones: the label stays here and opens the switch in the toolbar. */}
          {!isDesktop && hasPairs && !isTranslating && (
            <button type="button" className="reader-attribution" onClick={() => setSwitchOpen(true)}>
              <span className="material-symbols-outlined" aria-hidden="true">translate</span>
              <span><b>{detail.lead}</b>{detail.provider && ` · ${detail.provider}`}</span>
            </button>
          )}
          {!isDesktop && isTranslating && (
            <div className="reader-attribution is-progress" role="status">
              <span className="material-symbols-outlined" aria-hidden="true">translate</span>
              <span>
                Translating · {translateTotal > 0 && translatedBlockCount <= translateTotal
                  ? `${translatedBlockCount} of ${translateTotal}`
                  : `${translatedBlockCount} done`}
              </span>
              <span className="reader-attribution-bar" aria-hidden="true">
                <i style={{
                  transform: `scaleX(${translateTotal > 0 && translatedBlockCount <= translateTotal ? translatedBlockCount / translateTotal : 0.15})`,
                }} />
              </span>
            </div>
          )}
          {!hasPairs && !(isTranslating && !isDesktop) && <div className="reader-header-rule" />}
        </div>

        <div
          ref={contentRef}
          className="article-content"
          data-view={hasPairs ? translationView : undefined}
          style={{ '--reader-scale': TEXT_SCALES[textScaleIndex] }}
          dangerouslySetInnerHTML={{ __html: view.html }}
        />

        {!isDesktop && (hasRanking || nextArticle) && (
          <div className="reader-end">
            {hasRanking && (
              <div className="reader-end-card">
                <p className="reader-end-question">Worth reading?</p>
                <div className="reader-end-votes">
                  <FeedbackButtons
                    article={voteArticle}
                    buttonClassName="end-vote-btn"
                    labels={{ up: 'More like this', down: 'Less like this' }}
                    onVoted={handleVote}
                  />
                </div>
              </div>
            )}
            {nextArticle && (
              <button type="button" className="reader-end-card reader-next" onClick={onSwipeLeft}>
                {nextImage && <img src={nextImage} alt="" loading="lazy" />}
                <span className="reader-next-text">
                  <span className="reader-next-label">Next · {nextArticle.source_name}</span>
                  <span className="reader-next-title">{nextArticle.title}</span>
                </span>
                <span className="material-symbols-outlined" aria-hidden="true">chevron_right</span>
              </button>
            )}
          </div>
        )}
      </div>
      </div>

      {!isDesktop && (
        <ReaderMobileToolbar
          ref={mobileBarRef}
          isRead={Boolean(effectiveArticle.is_read)}
          onToggleRead={onToggleRead}
          translate={{
            canTranslate,
            isTranslating,
            done: translatedBlockCount,
            total: translateTotal,
            hasPairs,
            isChinese: isChineseFeed,
            view: translationView,
            onViewChange: changeTranslationView,
            onTranslate: handleTranslate,
            sourceLang,
            provider,
            switchOpen,
            setSwitchOpen,
          }}
          textScaleIndex={textScaleIndex}
          onTextScale={changeTextScale}
          onShare={handleShare}
          onCopyLink={handleCopyLink}
          originalUrl={effectiveArticle.url}
          onWhy={handleWhy}
          showWhy={hasRanking}
          onHide={onHide}
        />
      )}
    </div>
  );
}
