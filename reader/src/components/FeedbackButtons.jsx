import React, { useEffect, useState } from 'react';
import { setVote } from '../api';
import { queueVote } from '../db';

/**
 * The thumbs-up / thumbs-down pair. Shared so the index cards can float them over
 * the artwork while ArticleCard and ArticleReader keep them inline in the meta row.
 *
 * State comes from article.vote, not from local state alone: the previous version
 * held it in useState and lost it on every refresh and every unmount.
 */
export default function FeedbackButtons({
  article,
  buttonClassName = 'ranking-btn thumb-btn',
  keyboardShortcuts = false,
  onVoted
}) {
  const [feedback, setFeedback] = useState(article?.vote ?? null);

  // A fresh payload (sync, or navigating to another article through the same
  // component) is the authority on what was voted.
  useEffect(() => {
    setFeedback(article?.vote ?? null);
  }, [article?.id, article?.vote]);

  const record = async (type) => {
    if (!article?.id) return;
    const next = feedback === type ? null : type;
    // Optimistic: the button must never bounce back under the user's finger because
    // the network was slow.
    setFeedback(next);
    if (onVoted) onVoted(article.id, next);
    try {
      await setVote(article.id, next);
    } catch (err) {
      console.warn('Vote did not reach the server, queued for retry:', err);
      queueVote(article.id, next).catch(() => {});
    }
  };

  const handleFeedback = (type, e) => {
    e.stopPropagation();
    e.preventDefault();
    record(type);
  };

  // Opt-in, and only where one article is unambiguously in view: a list of cards
  // would bind the same two keys once per card.
  useEffect(() => {
    if (!keyboardShortcuts) return;

    const onKeyDown = (e) => {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      const t = e.target;
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable) return;
      // These are the page's scroll keys, so the vote has to claim them outright.
      e.preventDefault();
      record(e.key === 'ArrowUp' ? 'show_more' : 'show_less');
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [keyboardShortcuts, feedback, article?.id]);

  return (
    <>
      <button
        className={`${buttonClassName} thumb-up ${feedback === 'show_more' ? 'active' : ''}`}
        onClick={(e) => handleFeedback('show_more', e)}
        title="Show more like this"
        aria-label="Show more like this"
        aria-pressed={feedback === 'show_more'}
      >
        <span className="material-symbols-outlined">thumb_up</span>
      </button>
      <button
        className={`${buttonClassName} thumb-down ${feedback === 'show_less' ? 'active' : ''}`}
        onClick={(e) => handleFeedback('show_less', e)}
        title="Show less like this"
        aria-label="Show less like this"
        aria-pressed={feedback === 'show_less'}
      >
        <span className="material-symbols-outlined">thumb_down</span>
      </button>
    </>
  );
}
