import React from 'react';
import '../styles/ranking_controls.css';

/**
 * The read-only counterpart to FeedbackButtons: it reports what was voted without
 * offering to change it, for the card footers where the thumbs themselves are
 * hidden (the index cards) or only revealed on hover (the feed cards).
 *
 * Deliberately carries no margin of its own. Every host footer pushes its
 * timestamp right with `margin-left: auto`, and the mark has to take that margin
 * over to stay beside it — which is a fact about each layout, not about the mark.
 * See .card-vote-mark in feed.css and sources_index.css.
 */
export default function VoteMark({ vote }) {
  if (!vote) return null;

  const isUp = vote === 'show_more';

  return (
    <span
      className={`card-vote-mark ${isUp ? 'up' : 'down'}`}
      title={isUp ? 'You voted: show more like this' : 'You voted: show less like this'}
    >
      <span className="material-symbols-outlined">
        {isUp ? 'thumb_up' : 'thumb_down'}
      </span>
    </span>
  );
}
