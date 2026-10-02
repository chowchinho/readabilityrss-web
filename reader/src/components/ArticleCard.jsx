import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { API_URL, getSortMode } from '../api';
import { IMAGES_CACHE } from '../constants/caches';
import { formatRelativeDate, cleanTranslationTag, stripBreadcrumb, stripHtml } from '../utils/articleText';
import useHoverDwellPanel from '../hooks/useHoverDwellPanel';
import ArticleCardSlideshow from './ArticleCardSlideshow';
import FeedbackButtons from './FeedbackButtons';
import FloatingInfoPanel from './FloatingInfoPanel';
import ScoreBreakdown, { useScoreBreakdown } from './ScoreBreakdown';
import VoteMark from './VoteMark';
import { MIN_FEATURE_IMAGE_WIDTH, isSmallFeatureImage, markSmallFeatureImage } from '../utils/topPicks';

// Kana and CJK ideographs have no case, and tracking spreads them apart.
const HAS_CJK = /[぀-ヿ㐀-鿿]/;

function ArticleCard({ article, isActive, onClick, hideSource, viewMode = 'standard', featured = false, isOffline = false, onVoted }) {
  const baseImageSrc = article.main_image_proxy
    ? (article.main_image_proxy.startsWith('http') ? article.main_image_proxy : `${API_URL}${article.main_image_proxy}`)
    : article.main_image;
  const objectUrlRef = useRef(null);
  const [imageSrc, setImageSrc] = useState(isOffline ? null : baseImageSrc);
  const infoBtnRef = useRef(null);
  const {
    showInfo,
    startDwell,
    stopDwell,
    cancelDwellWithGrace,
    keepPanelOpen,
    closePanelNow,
    toggleInfo,
  } = useHoverDwellPanel();
  const { breakdown, breakdownError } = useScoreBreakdown(article?.id, showInfo);

  useEffect(() => {
    const clearObjectUrl = () => {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };

    let cancelled = false;
    clearObjectUrl();

    if (!baseImageSrc) {
      setImageSrc(null);
      return undefined;
    }

    if (!isOffline || !('caches' in window)) {
      setImageSrc(baseImageSrc);
      return undefined;
    }

    const hydrateFromCache = async () => {
      try {
        const imageCache = await caches.open(IMAGES_CACHE);
        const cached = await imageCache.match(baseImageSrc);
        if (!cached) {
          if (!cancelled) setImageSrc(null);
          return;
        }

        const blob = await cached.blob();
        const blobUrl = URL.createObjectURL(blob);
        objectUrlRef.current = blobUrl;
        if (!cancelled) setImageSrc(blobUrl);
      } catch (err) {
        if (!cancelled) setImageSrc(null);
      }
    };

    hydrateFromCache();

    return () => {
      cancelled = true;
      clearObjectUrl();
    };
  }, [baseImageSrc, isOffline]);

  const effectiveViewMode = (viewMode === 'full_image' && imageSrc) ? 'full_image' : 'standard';

  // The list has no image sizes, so a small one is only found once it loads. The card
  // then drops back to the square layout, and the verdict is kept for the session.
  const [smallImage, setSmallImage] = useState(() => isSmallFeatureImage(article.id));
  const isFeature = featured && !smallImage && effectiveViewMode === 'standard' && Boolean(imageSrc);

  useEffect(() => {
    if (!featured || !imageSrc || smallImage) return undefined;
    let cancelled = false;
    const probe = new Image();
    probe.onload = () => {
      if (!cancelled && probe.naturalWidth < MIN_FEATURE_IMAGE_WIDTH) {
        markSmallFeatureImage(article.id);
        setSmallImage(true);
      }
    };
    probe.src = imageSrc;
    return () => { cancelled = true; };
  }, [featured, imageSrc, smallImage, article.id]);

  const topPickLabel = isFeature && (
    <span className="card-top-pick">
      <span className="material-symbols-outlined" aria-hidden="true">star</span>
      Top pick
    </span>
  );
  // A single-feed list has no source to name, so the identity row would hold the
  // time alone. The time moves to the end of the snippet and the row is dropped.
  const inlineMeta = hideSource && effectiveViewMode === 'standard';
  const displayTitle = cleanTranslationTag(article.title);
  // The 1,200-character featured snippet, not the 200-character one: a wide phone or
  // small tablet fits more lines than 200 characters fill. The line clamp does the cutting.
  const displaySnippet = stripBreadcrumb(cleanTranslationTag(article.featured_snippet || article.snippet)) || stripHtml(article.description || article.content).substring(0, 160).trim();
  // The thumbnail fixes the card's height, so a headline that fits on one line leaves
  // a line free for the snippet. CSS cannot count wrapped lines, hence the measuring;
  // the observer catches the pane being dragged wider or narrower.
  const titleRef = useRef(null);
  const [titleOneLine, setTitleOneLine] = useState(false);

  // Phones measure every standard card: a one-line headline lets the summary run to
  // four lines instead of three.
  const measureTitle = inlineMeta || effectiveViewMode === 'standard';
  useLayoutEffect(() => {
    const el = titleRef.current;
    if (!measureTitle || !el) return undefined;
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 22;
    const measure = () => setTitleOneLine(el.offsetHeight < lineHeight * 1.5);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [measureTitle, displayTitle]);

  // Square rows, phone and desktop pane 2: the summary takes as many lines as fit
  // between its top and the photo's bottom edge, so its last line ends level with the
  // photo. Phones vary the line height within 18-22px; any remainder short of a line
  // goes above the summary rather than below it.
  const cardRef = useRef(null);
  const imageBoxRef = useRef(null);
  const snippetRef = useRef(null);
  const fitsToPhoto = effectiveViewMode === 'standard' && !isFeature && Boolean(imageSrc) && Boolean(displaySnippet);
  useLayoutEffect(() => {
    const card = cardRef.current;
    if (!card) return undefined;
    const clear = () => {
      delete card.dataset.fit;
      card.style.removeProperty('--snip-lines');
      card.style.removeProperty('--snip-lh');
      card.style.removeProperty('--snip-slack');
    };
    if (!fitsToPhoto) { clear(); return undefined; }
    const phone = window.matchMedia('(max-width: 767px)');
    const measure = () => {
      const img = imageBoxRef.current;
      const snip = snippetRef.current;
      if (!img || !snip) { clear(); return; }
      const slack = parseFloat(card.style.getPropertyValue('--snip-slack')) || 0;
      const avail = img.getBoundingClientRect().bottom - (snip.getBoundingClientRect().top - slack);
      if (avail < 18) { clear(); return; }
      let fitLines;
      let lh;
      if (phone.matches) {
        const lines = Math.max(1, Math.round(avail / 20));
        lh = Math.min(22, Math.max(18, avail / lines));
        fitLines = Math.max(1, Math.min(lines, Math.floor((avail + 0.5) / lh)));
      } else {
        // Desktop pane 2 keeps its 1.25rem leading and fills whole lines: three under a
        // two-line headline, four under a one-line one on a single-feed list.
        lh = parseFloat(getComputedStyle(document.documentElement).fontSize) * 1.25 || 20;
        fitLines = Math.max(1, Math.floor((avail + 0.5) / lh));
      }
      const nextSlack = Math.max(0, avail - fitLines * lh);
      const prevLines = card.style.getPropertyValue('--snip-lines');
      const prevLh = parseFloat(card.style.getPropertyValue('--snip-lh')) || 0;
      if (prevLines === String(fitLines) && Math.abs(prevLh - lh) < 0.1 && Math.abs(slack - nextSlack) < 0.1) return;
      card.dataset.fit = '';
      card.style.setProperty('--snip-lines', String(fitLines));
      card.style.setProperty('--snip-lh', `${lh.toFixed(2)}px`);
      card.style.setProperty('--snip-slack', `${nextSlack.toFixed(2)}px`);
    };
    measure();
    // Later passes run on the next frame, once per frame. Writing the line count inside
    // the observer's own callback resized what it watches in the same frame, and the
    // browser reported "ResizeObserver loop completed with undelivered notifications"
    // on every step of a pane-divider drag.
    let frame = 0;
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; measure(); });
    };
    phone.addEventListener('change', schedule);
    // The source row settles once its fonts and favicon load, moving the summary
    // without resizing the card, so it is watched too.
    let cancelled = false;
    document.fonts?.ready.then(() => { if (!cancelled) schedule(); });
    let observer;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(schedule);
      observer.observe(card);
      if (titleRef.current) observer.observe(titleRef.current);
      const sourceRow = card.querySelector('.card-footer');
      if (sourceRow) observer.observe(sourceRow);
    }
    return () => {
      cancelled = true;
      if (frame) cancelAnimationFrame(frame);
      phone.removeEventListener('change', schedule);
      observer?.disconnect();
    };
  }, [fitsToPhoto, displayTitle, displaySnippet]);

  const score = typeof article.score === 'number' ? article.score.toFixed(2) : null;
  // Nothing to explain or to train under latest/random ordering.
  const hasRanking = getSortMode() === 'smart';
  
  const infoSlot = (
    <div className="card-info-slot">
      <button
        ref={infoBtnRef}
        className={`card-rank-btn info-btn ${showInfo ? 'active' : ''}`}
        onMouseEnter={startDwell}
        onMouseLeave={stopDwell}
        onClick={toggleInfo}
        title="Why this is here"
        aria-label="Why this is here"
        aria-expanded={showInfo}
      >
        <span className="material-symbols-outlined">info</span>
      </button>
    </div>
  );

  const feedbackSlot = (
    <div className="card-feedback-slot">
      <FeedbackButtons article={article} buttonClassName="card-rank-btn" onVoted={onVoted} />
    </div>
  );

  // Both slots are out of flow, so revealing them cannot shift the source name or
  // push .card-timestamp off the footer's right edge.
  const handleClick = (e) => {
    if (onClick) onClick(article.id, e);
  };

  const timeAndState = (
    <>
      {/* Not gated on hasRanking: a vote cast under smart sort is still a fact about
          the article, and hiding it under latest/random would read as a lost vote. */}
      <VoteMark vote={article.vote} />
      <span className="card-timestamp">{formatRelativeDate(article.pub_date || article.created_at, { short: true })}</span>
      {!article.is_read && <span className="unread-dot" />}
    </>
  );

  const footerContent = (
    <div className="card-footer">
      {!hideSource && (
        <div className="card-meta-identity">
          <div className="favicon-wrapper" style={{ marginRight: 0 }}>
            <img
              src={`https://www.google.com/s2/favicons?sz=32&domain_url=${encodeURIComponent(article.url)}`}
              alt=""
              className="card-favicon"
              onError={(e) => {
                e.target.style.display = 'none';
                const fallback = e.target.parentElement?.querySelector('.favicon-fallback');
                if (fallback) fallback.style.display = 'inline-block';
              }}
            />
            <span className="material-symbols-outlined favicon-fallback" style={{ display: 'none', fontSize: '14px', color: 'var(--text-muted)' }}>
              rss_feed
            </span>
          </div>
          <span className={`card-source-name${HAS_CJK.test(article.source_name || '') ? '' : ' latin'}`}>{article.source_name}</span>
        </div>
      )}
      {timeAndState}
      {topPickLabel}
      {/* The !showInfo guards are gone with the in-card panel: it used to cover
          .card-content and hide these, so they were duplicated inside it. The panel
          floats now, so the real controls stay put and stay usable while it is open. */}
      {hasRanking && effectiveViewMode === 'standard' && infoSlot}
      {hasRanking && effectiveViewMode === 'standard' && feedbackSlot}
    </div>
  );

  return (
    <div
      ref={cardRef}
      className={`article-card ${isActive ? 'active' : ''} ${article.is_read ? 'is-read' : ''} ${!imageSrc ? 'no-image' : ''} ${effectiveViewMode === 'full_image' ? 'full-image' : ''} ${inlineMeta ? 'inline-meta' : ''} ${inlineMeta && titleOneLine ? 'title-one-line' : ''} ${titleOneLine && effectiveViewMode === 'standard' ? 'title-single' : ''} ${isFeature ? 'featured' : ''}`}
      onClick={handleClick}
      onMouseLeave={cancelDwellWithGrace}
      data-id={article.id}
    >
      {/* Standard cards lay the identity row across the full width, so the timestamp
          ends on the thumbnail's right edge and the thumbnail starts level with the
          headline. The full-image card keeps it inside the scrim with the headline. */}
      {effectiveViewMode === 'standard' && !inlineMeta && footerContent}

      {imageSrc && (
        <div className="card-image-container" ref={imageBoxRef}>
          <ArticleCardSlideshow
            article={article}
            mainImage={imageSrc}
            focalX={article.focal_x}
            focalY={article.focal_y}
            fallbackIconSize={28}
            showDots={isFeature}
          />
        </div>
      )}

      {/* Hung off the card, not .card-content — that overlay is pinned to the
          bottom, so a slot inside it could not reach the artwork's top corners. */}
      {hasRanking && (effectiveViewMode === 'full_image' || inlineMeta) && infoSlot}
      {hasRanking && (effectiveViewMode === 'full_image' || inlineMeta) && feedbackSlot}

      <div className="card-content">
        {effectiveViewMode === 'full_image' ? (
          <>
            {footerContent}
            <h3 className="card-title">{displayTitle}</h3>
          </>
        ) : (
          <>
            {inlineMeta && topPickLabel}
            <h3 className="card-title" ref={titleRef}>{displayTitle}</h3>
            {inlineMeta ? (
              // .card-when floats, so it has to come before the text it sits at the end of.
              <p className="card-snippet" ref={snippetRef}>
                <span className="card-when">{timeAndState}</span>
                {displaySnippet ? `${displaySnippet}…` : null}
              </p>
            ) : (
              displaySnippet ? <p className="card-snippet" ref={snippetRef}>{displaySnippet}…</p> : null
            )}
          </>
        )}
        
        {/* Floats above the pane rather than covering .card-content, and renders the
            same ScoreBreakdown the index popover and the reader panel use - this card
            previously showed tags only, so pane 2 explained less than the index did. */}
        {hasRanking && (
          <FloatingInfoPanel
            anchorRef={infoBtnRef}
            open={showInfo}
            onPointerEnter={keepPanelOpen}
            onPointerLeave={closePanelNow}
          >
            <ScoreBreakdown
              article={article}
              breakdown={breakdown}
              breakdownError={breakdownError}
              showReason
            />
          </FloatingInfoPanel>
        )}
      </div>
    </div>
  );
}

export default React.memo(ArticleCard);
