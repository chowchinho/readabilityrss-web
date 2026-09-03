import React, { useEffect, useRef } from 'react';
import FeedbackButtons from './FeedbackButtons';
import FloatingInfoPanel from './FloatingInfoPanel';
import ScoreBreakdown, { useScoreBreakdown } from './ScoreBreakdown';
import useHoverDwellPanel from '../hooks/useHoverDwellPanel';
import '../styles/ranking_controls.css';

/**
 * The info / thumbs-up / thumbs-down row shown on every article surface.
 *
 * showFeedback is off on the index cards, which float the thumbs over the artwork
 * instead; every other surface keeps all three controls together in the meta row.
 */
export default function RankingControls({ article, compact = false, showFeedback = true }) {
  const {
    showInfo,
    setShowInfo,
    startDwell,
    cancelDwellWithGrace,
    keepPanelOpen,
    closePanelNow,
  } = useHoverDwellPanel();
  const { breakdown, breakdownError } = useScoreBreakdown(article?.id, showInfo);
  const btnRef = useRef(null);

  // The dismiss listener that SourcesIndex installs for the row also has to close
  // this, or a collapsed row would leave an orphaned panel on screen.
  useEffect(() => {
    if (!showInfo) return;
    const dismiss = () => setShowInfo(false);
    document.addEventListener('click', dismiss);
    return () => document.removeEventListener('click', dismiss);
  }, [showInfo, setShowInfo]);

  const handleInfoClick = (e) => {
    e.stopPropagation();
    e.preventDefault();
    if (window.matchMedia('(hover: none)').matches) {
      setShowInfo((v) => !v);
    }
  };

  const score = typeof article?.score === 'number' ? article.score.toFixed(2) : null;

  return (
    <span className="ranking-controls" onMouseLeave={cancelDwellWithGrace}>
      <div className={`card-ranking-actions${compact ? ' is-compact' : ''}`}>
        <button
          ref={btnRef}
          className={`ranking-btn info-btn ${showInfo ? 'active' : ''}`}
          onMouseEnter={startDwell}
          onClick={handleInfoClick}
          title="Why this is here"
          aria-label="Why this is here"
          aria-expanded={showInfo}
        >
          <span className="material-symbols-outlined">info</span>
        </button>
        {showFeedback && <FeedbackButtons article={article} />}
      </div>

      <FloatingInfoPanel
        anchorRef={btnRef}
        open={showInfo}
        onPointerEnter={keepPanelOpen}
        onPointerLeave={closePanelNow}
      >
        <ScoreBreakdown
          article={article}
          breakdown={breakdown}
          breakdownError={breakdownError}
        />
      </FloatingInfoPanel>
    </span>
  );
}
