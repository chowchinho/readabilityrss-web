import React from 'react';
import RankingControls from './RankingControls';
import VoteMark from './VoteMark';
import { getSortMode } from '../api';

/**
 * The meta strip at the foot of every index card. All four SourcesIndex layouts
 * render this so one hover/tap target can drive the ranking controls everywhere.
 *
 * The controls are taken out of flow (see .monocle-meta-controls) rather than
 * hidden with display:none, because revealing an in-flow element would push
 * .monocle-card-time off the card's right edge.
 */
export default function CardMeta({
  article,
  timeLabel,
  faviconUrl = null,
  showIdentity = false,
  compact = false,
  isRevealed = false,
  onRevealToggle = null
}) {
  const hasRanking = getSortMode() === 'smart';

  const handleClick = (e) => {
    // Desktop reveals on hover, and swallowing the click there would cost the
    // user the ability to open the article from the meta strip.
    if (!hasRanking || !onRevealToggle) return;
    if (!window.matchMedia('(hover: none)').matches) return;
    e.stopPropagation();
    onRevealToggle(article.id);
  };

  const classes = [
    'monocle-card-meta',
    hasRanking ? 'has-ranking' : '',
    isRevealed ? 'is-revealed' : ''
  ].filter(Boolean).join(' ');

  return (
    <div className={classes} onClick={handleClick}>
      {showIdentity && (
        <div className="monocle-meta-identity">
          {faviconUrl && (
            <img
              src={faviconUrl}
              alt=""
              className="monocle-meta-favicon"
              onError={(e) => { e.target.style.display = 'none'; }}
            />
          )}
          <div className="monocle-source-wrap">
            <span className="monocle-source-name">{article.source_name || 'News'}</span>
          </div>
        </div>
      )}
      {hasRanking && (
        <div className="monocle-meta-controls">
          <RankingControls article={article} compact={compact} showFeedback={false} />
        </div>
      )}
      {/* RankingControls runs here with showFeedback={false}, so this is the only
          place a vote is visible on an index card. Outside .monocle-meta-controls
          on purpose: that box is revealed on hover and would hide the mark the
          rest of the time. */}
      <VoteMark vote={article.vote} />
      <span className="monocle-card-time">{timeLabel}</span>
      {!article.is_read && <span className="unread-dot" />}
    </div>
  );
}
