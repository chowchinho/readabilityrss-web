import { useEffect, useRef } from 'react';

// Horizontal swipe between articles, tracked 1:1 (apple-design, Designing Fluid
// Interfaces). The content follows the finger from the first move, the decision to
// change article is made from where the gesture is *going* (momentum projection),
// not where it stopped, and the ends of the list resist instead of stopping dead.
//
// Transforms are written straight to the element: a React update per touchmove
// would re-render the whole article.

const HYSTERESIS_PX = 10;        // movement before we commit to an axis
const EDGE_GUARD_PX = 24;        // leave the OS back gesture at the screen edges alone
// Snap points are "stay" (0) and "next article" (one screen width), so the gesture
// goes to whichever the projected landing point is nearer: past half the width.
const COMMIT_FRACTION = 0.5;
// Apple's "snappier" rate. The 0.998 scroll rate projects a gentle 250px/s drag an
// extra 125px, far enough to change article on a drag the reader meant to undo.
const DECELERATION = 0.99;
// The next article arrives from the side the last one left towards, so the pages
// read as a row. The reader remounts when the article changes, so the entrance is
// a CSS animation keyed off this attribute (see rr-page-in in reader.css) rather
// than a transform written to a node that is about to be replaced.
const ENTER_ATTR = 'data-reader-enter';
const ENTER_MS = 300;

function project(velocityPxPerS) {
  return (velocityPxPerS / 1000) * DECELERATION / (1 - DECELERATION);
}

function rubberband(overshoot, dimension, constant = 0.55) {
  return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));
}

function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function useSwipe({
  onSwipeLeft,
  onSwipeRight,
  targetRef = null,
  canSwipeLeft = true,
  canSwipeRight = true,
  enabled = true,
}) {
  const handlersRef = useRef({ onSwipeLeft, onSwipeRight, canSwipeLeft, canSwipeRight });
  handlersRef.current = { onSwipeLeft, onSwipeRight, canSwipeLeft, canSwipeRight };

  useEffect(() => {
    if (!enabled) return undefined;

    let start = null;          // { x, y }
    let axis = null;           // 'x' | 'y' | null until hysteresis is passed
    let history = [];          // recent { x, t } samples for release velocity
    let settleTimer = 0;
    let enterTimer = 0;

    const endEnter = () => {
      window.clearTimeout(enterTimer);
      document.documentElement.removeAttribute(ENTER_ATTR);
    };

    const el = () => targetRef?.current || null;

    const setTransform = (dx, transition) => {
      const node = el();
      if (!node) return;
      node.style.transition = transition || 'none';
      node.style.transform = dx ? `translate3d(${dx}px, 0, 0)` : '';
      node.style.opacity = '';
    };

    const reset = () => {
      start = null;
      axis = null;
      history = [];
    };

    const handleTouchStart = (e) => {
      if (e.touches.length !== 1) { reset(); return; }
      const t = e.touches[0];
      if (t.clientX < EDGE_GUARD_PX || t.clientX > window.innerWidth - EDGE_GUARD_PX) { reset(); return; }
      window.clearTimeout(settleTimer);
      // A running entrance would override the transform this touch is about to set.
      endEnter();
      start = { x: t.clientX, y: t.clientY };
      axis = null;
      history = [{ x: t.clientX, t: performance.now() }];
    };

    const handleTouchMove = (e) => {
      if (!start || e.touches.length !== 1) return;
      const t = e.touches[0];
      const dx = t.clientX - start.x;
      const dy = t.clientY - start.y;

      if (!axis) {
        if (Math.hypot(dx, dy) < HYSTERESIS_PX) return;
        axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
      }
      if (axis !== 'x') return;

      const now = performance.now();
      history.push({ x: t.clientX, t: now });
      while (history.length > 2 && now - history[0].t > 100) history.shift();

      const { canSwipeLeft: canLeft, canSwipeRight: canRight } = handlersRef.current;
      const width = window.innerWidth;
      // No article that way: resist progressively rather than following 1:1.
      const blocked = (dx < 0 && !canLeft) || (dx > 0 && !canRight);
      setTransform(blocked ? rubberband(dx, width) : dx);
    };

    const handleTouchEnd = (e) => {
      if (!start || axis !== 'x') { reset(); return; }
      const t = e.changedTouches[0];
      const dx = t.clientX - start.x;
      const now = performance.now();
      history.push({ x: t.clientX, t: now });
      const first = history[0];
      const dt = Math.max(1, now - first.t);
      const velocity = ((t.clientX - first.x) / dt) * 1000; // px/s
      const width = window.innerWidth;
      const projected = dx + project(velocity);
      const { onSwipeLeft: goLeft, onSwipeRight: goRight, canSwipeLeft: canLeft, canSwipeRight: canRight } = handlersRef.current;
      reset();

      const commitLeft = projected < -width * COMMIT_FRACTION && dx < 0 && canLeft;
      const commitRight = projected > width * COMMIT_FRACTION && dx > 0 && canRight;
      const reduced = prefersReducedMotion();

      if (commitLeft || commitRight) {
        // Carry on in the direction of travel. The duration shrinks with speed so a
        // hard flick does not slow down on the way out.
        const remaining = width - Math.abs(dx);
        const ms = reduced ? 0 : Math.max(120, Math.min(260, (remaining / Math.max(Math.abs(velocity), 1)) * 1000));
        setTransform(commitLeft ? -width : width, ms ? `transform ${ms}ms cubic-bezier(0.23, 1, 0.32, 1)` : 'none');
        settleTimer = window.setTimeout(() => {
          if (!reduced) {
            document.documentElement.setAttribute(ENTER_ATTR, commitLeft ? 'next' : 'prev');
            enterTimer = window.setTimeout(endEnter, ENTER_MS + 100);
          }
          const node = el();
          if (node) {
            node.style.transition = 'none';
            node.style.transform = '';
          }
          if (commitLeft) goLeft && goLeft();
          else goRight && goRight();
        }, ms);
        return;
      }

      // Not far enough, or nothing that way: settle back to rest. A decisive swipe
      // at either end still reaches the handler so it can say "Last article".
      if (projected < -width * COMMIT_FRACTION && !canLeft) goLeft && goLeft();
      if (projected > width * COMMIT_FRACTION && !canRight) goRight && goRight();
      setTransform(0, reduced ? 'none' : 'transform 320ms cubic-bezier(0.23, 1, 0.32, 1)');
    };

    const handleTouchCancel = () => {
      if (axis === 'x') setTransform(0, 'transform 200ms ease');
      reset();
    };

    document.addEventListener('touchstart', handleTouchStart, { passive: true });
    document.addEventListener('touchmove', handleTouchMove, { passive: true });
    document.addEventListener('touchend', handleTouchEnd, { passive: true });
    document.addEventListener('touchcancel', handleTouchCancel, { passive: true });

    return () => {
      window.clearTimeout(settleTimer);
      // enterTimer is left to run: this cleanup fires while the next article is
      // still loading, and ending the entrance here would cancel it unseen.
      document.removeEventListener('touchstart', handleTouchStart);
      document.removeEventListener('touchmove', handleTouchMove);
      document.removeEventListener('touchend', handleTouchEnd);
      document.removeEventListener('touchcancel', handleTouchCancel);
    };
  }, [enabled, targetRef]);
}
