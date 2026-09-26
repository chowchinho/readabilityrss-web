import { useEffect, useRef } from 'react';
import { sendEvents } from '../api';
import { measureReadDepth } from '../utils/readDepth';

// Sends one 'read_complete' per article once the reader has both seen the paragraph at 80%
// of the text and spent half the estimated reading time with the tab visible. Scroll
// position answers "did you get there", time answers "did you read on the way"; for an
// article that fits on screen the anchor is visible at once and only the time half counts.
export function useReadCompletion({ article, scrollRef, contentRef, contentKey }) {
  const stateRef = useRef(null);
  const id = article?.id;

  useEffect(() => {
    if (!id) return undefined;
    const tags = {
      primary_topic: article.topics?.primary,
      secondary_topics: article.topics?.secondary,
      region: article.topics?.region,
      article_type: article.topics?.type,
    };
    const state = { id, activeSeconds: 0, anchorSeen: false, requiredSeconds: null, fired: false };
    stateRef.current = state;

    const tick = setInterval(() => {
      if (state.fired || document.visibilityState !== 'visible') return;
      state.activeSeconds += 1;
      if (state.anchorSeen && state.requiredSeconds !== null
          && state.activeSeconds >= state.requiredSeconds) {
        state.fired = true;
        clearInterval(tick);
        sendEvents([{
          article_id: id,
          event_type: 'read_complete',
          dwell_seconds: state.activeSeconds,
          ...tags,
        }]).catch(() => {});
      }
    }, 1000);

    return () => clearInterval(tick);
  }, [id]);

  // Re-measured whenever the body changes - an on-demand translation streams in block by
  // block and moves the anchor. Once seen, the anchor stays seen.
  useEffect(() => {
    const state = stateRef.current;
    const root = contentRef.current;
    if (!state || state.id !== id || state.fired || !root) return undefined;
    const depth = measureReadDepth(root);
    if (!depth) return undefined;
    state.requiredSeconds = depth.requiredSeconds;
    if (state.anchorSeen) return undefined;

    // "Reached" means the anchor's top has risen above the bottom of the viewport, not that
    // it intersected: a jump to the end (End key, a hard fling) carries it from below the
    // screen to above it without ever being on screen, and IntersectionObserver never fires.
    const scroller = scrollRef.current;
    if (!scroller) return undefined;
    const check = () => {
      if (depth.anchor.getBoundingClientRect().top < scroller.getBoundingClientRect().bottom) {
        state.anchorSeen = true;
        scroller.removeEventListener('scroll', check);
      }
    };
    scroller.addEventListener('scroll', check, { passive: true });
    // Deferred so images above the anchor have taken their height; measured at once, the
    // text sits packed together and the anchor of a long illustrated article looks on screen.
    const initial = setTimeout(check, 2000);
    return () => {
      clearTimeout(initial);
      scroller.removeEventListener('scroll', check);
    };
  }, [id, contentKey]);
}
