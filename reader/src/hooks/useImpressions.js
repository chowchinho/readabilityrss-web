import { useEffect, useRef } from 'react';
import { sendEvents } from '../api';

// Every article surface. The column-2 feed card and the two SourcesIndex layouts all
// need to report, or the impression count is a denominator for only part of the app
// and read-rate comes out wrong for whichever surface is missing.
const CARD_SELECTOR = '.article-card[data-id], .monocle-grid-card[data-id], .monocle-featured-card[data-id]';

const IMPRESSION_MS = 500;
// Hovering is far cheaper than opening an article, so it only counts after a long
// deliberate pause and is recorded as its own weaker event rather than as a read.
const HOVER_MS = 5000;

const sessionSeenIds = new Set();
const sessionHoveredIds = new Set();
let pendingEvents = [];

export function recordEvent(event) {
  pendingEvents.push(event);
}

export function flushEvents() {
  if (pendingEvents.length === 0) return;
  const batch = [...pendingEvents];
  pendingEvents = [];
  sendEvents(batch).catch((err) => {
    console.warn('Failed to flush events:', err);
  });
}

export function useImpressions() {
  const timersRef = useRef(new Map());
  const hoverTimerRef = useRef(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const articleId = parseInt(entry.target.getAttribute('data-id'), 10);
          if (!articleId || sessionSeenIds.has(articleId)) return;

          if (entry.isIntersecting) {
            if (!timersRef.current.has(articleId)) {
              const timer = setTimeout(() => {
                sessionSeenIds.add(articleId);
                recordEvent({ article_id: articleId, event_type: 'impression' });
                timersRef.current.delete(articleId);
              }, IMPRESSION_MS);
              timersRef.current.set(articleId, timer);
            }
          } else if (timersRef.current.has(articleId)) {
            clearTimeout(timersRef.current.get(articleId));
            timersRef.current.delete(articleId);
          }
        });
      },
      { threshold: 0.5 }
    );

    const observeNode = (node) => {
      if (!node || node.nodeType !== Node.ELEMENT_NODE) return;
      if (node.matches?.(CARD_SELECTOR)) {
        observer.observe(node);
      }
      if (node.querySelectorAll) {
        node.querySelectorAll(CARD_SELECTOR).forEach((card) => observer.observe(card));
      }
    };

    document.querySelectorAll(CARD_SELECTOR).forEach((card) => observer.observe(card));

    const mutationObserver = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const addedNode of mutation.addedNodes) {
          observeNode(addedNode);
        }
      }
    });

    mutationObserver.observe(document.body, { childList: true, subtree: true });

    // Touch devices synthesise a pointerover on tap, which would log a hover for
    // every article opened on mobile - a signal about the input method, not interest.
    const hasRealPointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

    const clearHoverTimer = () => {
      if (hoverTimerRef.current) {
        clearTimeout(hoverTimerRef.current);
        hoverTimerRef.current = null;
      }
    };

    const handlePointerOver = (e) => {
      const card = e.target.closest?.(CARD_SELECTOR);
      if (!card) return;
      const articleId = parseInt(card.getAttribute('data-id'), 10);
      if (!articleId || sessionHoveredIds.has(articleId)) return;
      clearHoverTimer();
      hoverTimerRef.current = setTimeout(() => {
        sessionHoveredIds.add(articleId);
        recordEvent({
          article_id: articleId,
          event_type: 'hover',
          dwell_seconds: Math.round(HOVER_MS / 1000),
        });
        hoverTimerRef.current = null;
      }, HOVER_MS);
    };

    const handlePointerOut = (e) => {
      // Moving between children of the same card is not leaving it.
      const from = e.target.closest?.(CARD_SELECTOR);
      const to = e.relatedTarget?.closest?.(CARD_SELECTOR);
      if (from && from === to) return;
      clearHoverTimer();
    };

    if (hasRealPointer) {
      document.addEventListener('pointerover', handlePointerOver);
      document.addEventListener('pointerout', handlePointerOut);
    }

    const flushInterval = setInterval(flushEvents, 10000);

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        clearHoverTimer();
        flushEvents();
      }
    };

    window.addEventListener('beforeunload', flushEvents);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      observer.disconnect();
      mutationObserver.disconnect();
      clearInterval(flushInterval);
      clearHoverTimer();
      if (hasRealPointer) {
        document.removeEventListener('pointerover', handlePointerOver);
        document.removeEventListener('pointerout', handlePointerOut);
      }
      window.removeEventListener('beforeunload', flushEvents);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      timersRef.current.forEach((t) => clearTimeout(t));
      timersRef.current.clear();
      flushEvents();
    };
  }, []);
}
