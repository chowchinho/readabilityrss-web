import React, { useEffect, useRef, useState } from 'react';
import { API_URL, getSortMode } from '../api';
import { IMAGES_CACHE } from '../constants/caches';
import { formatRelativeDate, cleanTranslationTag, stripHtml } from '../utils/articleText';
import useHoverDwellPanel from '../hooks/useHoverDwellPanel';
import ArticleCardSlideshow from './ArticleCardSlideshow';
import FeedbackButtons from './FeedbackButtons';
import FloatingInfoPanel from './FloatingInfoPanel';
import ScoreBreakdown, { useScoreBreakdown } from './ScoreBreakdown';
import VoteMark from './VoteMark';

function ArticleCard({ article, isActive, onClick, hideSource, viewMode = 'standard', isOffline = false, onVoted }) {
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
  const displayTitle = cleanTranslationTag(article.title);
  const displaySnippet = cleanTranslationTag(article.snippet) || stripHtml(article.description || article.content).substring(0, 160).trim();
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
      {/* Not gated on hasRanking: a vote cast under smart sort is still a fact about
          the article, and hiding it under latest/random would read as a lost vote. */}
      <VoteMark vote={article.vote} />
      <span className="card-timestamp">{formatRelativeDate(article.pub_date || article.created_at, { short: true })}</span>
      {/* The !showInfo guards are gone with the in-card panel: it used to cover
          .card-content and hide these, so they were duplicated inside it. The panel
          floats now, so the real controls stay put and stay usable while it is open. */}
      {hasRanking && effectiveViewMode === 'standard' && infoSlot}
      {hasRanking && effectiveViewMode === 'standard' && feedbackSlot}
    </div>
  );

  return (
    <div 
      className={`article-card ${isActive ? 'active' : ''} ${article.is_read ? 'is-read' : ''} ${!imageSrc ? 'no-image' : ''} ${effectiveViewMode === 'full_image' ? 'full-image' : ''}`}
      onClick={handleClick}
      onMouseLeave={cancelDwellWithGrace}
      data-id={article.id}
    >
      {imageSrc && (
        <div className="card-image-container">
          <ArticleCardSlideshow
            article={article}
            mainImage={imageSrc}
            focalX={article.focal_x}
            focalY={article.focal_y}
            fallbackIconSize={28}
          />
        </div>
      )}

      {/* Hung off the card, not .card-content — that overlay is pinned to the
          bottom, so a slot inside it could not reach the artwork's top corners. */}
      {hasRanking && effectiveViewMode === 'full_image' && infoSlot}
      {hasRanking && effectiveViewMode === 'full_image' && feedbackSlot}

      <div className="card-content">
        {effectiveViewMode === 'full_image' ? (
          <>
            {footerContent}
            <h3 className="card-title">{displayTitle}</h3>
          </>
        ) : (
          <>
            <h3 className="card-title">{displayTitle}</h3>
            {displaySnippet ? <p className="card-snippet">{displaySnippet}...</p> : null}
            {footerContent}
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
