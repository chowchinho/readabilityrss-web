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
  const displaySnippet = stripBreadcrumb(cleanTranslationTag(article.snippet)) || stripHtml(article.description || article.content).substring(0, 160).trim();
  // The thumbnail fixes the card's height, so a headline that fits on one line leaves
  // a line free for the snippet. CSS cannot count wrapped lines, hence the measuring;
  // the observer catches the pane being dragged wider or narrower.
  const titleRef = useRef(null);
  const [titleOneLine, setTitleOneLine] = useState(false);

  useLayoutEffect(() => {
    const el = titleRef.current;
    if (!inlineMeta || !el) return undefined;
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 22;
    const measure = () => setTitleOneLine(el.offsetHeight < lineHeight * 1.5);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [inlineMeta, displayTitle]);

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
          <span className="card-source-name">{article.source_name}</span>
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
      className={`article-card ${isActive ? 'active' : ''} ${article.is_read ? 'is-read' : ''} ${!imageSrc ? 'no-image' : ''} ${effectiveViewMode === 'full_image' ? 'full-image' : ''} ${inlineMeta ? 'inline-meta' : ''} ${inlineMeta && titleOneLine ? 'title-one-line' : ''} ${isFeature ? 'featured' : ''}`}
      onClick={handleClick}
      onMouseLeave={cancelDwellWithGrace}
      data-id={article.id}
    >
      {/* Standard cards lay the identity row across the full width, so the timestamp
          ends on the thumbnail's right edge and the thumbnail starts level with the
          headline. The full-image card keeps it inside the scrim with the headline. */}
      {effectiveViewMode === 'standard' && !inlineMeta && footerContent}

      {imageSrc && (
        <div className="card-image-container">
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
              <p className="card-snippet">
                <span className="card-when">{timeAndState}</span>
                {displaySnippet ? `${displaySnippet}…` : null}
              </p>
            ) : (
              displaySnippet ? <p className="card-snippet">{displaySnippet}…</p> : null
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
