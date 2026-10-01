import React, { useEffect, useRef, useState } from 'react';
import '../styles/pull_to_refresh.css';

// Pull down at the top of a list to refresh it (phones only). The page switches off
// overscroll and the lists scroll inside their own containers, so the browser's own
// pull-to-refresh can never fire here; this replaces it.
//
// The list follows the finger 1:1 with rising resistance, the indicator turns with
// the pull, and crossing the threshold is marked once with a short vibration. A
// release past the threshold, or a quick flick, refreshes. Transforms are written
// straight to the elements so a pull never re-renders the list.

const SCROLLERS = '.feed-container, .sources-index-container';
const HYSTERESIS = 10;   // movement before deciding the gesture is a pull
const ARM = 64;          // resisted distance at which a release refreshes (~100px of finger)
const HOLD = 56;         // where the list rests while the refresh runs
const FLICK_SPEED = 0.6; // px/ms: a fast downward flick refreshes from a short pull
const FLICK_MIN = 28;
const MIN_SPIN_MS = 450; // a refresh that returns at once still reads as having run

function rubberband(distance, dimension, constant = 0.7) {
  return (distance * dimension * constant) / (dimension + constant * distance);
}

function currentTranslateY(el) {
  const t = getComputedStyle(el).transform;
  if (!t || t === 'none') return 0;
  const m = t.match(/matrix(3d)?\(([^)]+)\)/);
  if (!m) return 0;
  const v = m[2].split(',').map(Number);
  return m[1] ? v[13] : v[5];
}

function reducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export default function PullToRefresh({ enabled, onRefresh, children }) {
  const rootRef = useRef(null);
  const indicatorRef = useRef(null);
  const onRefreshRef = useRef(onRefresh);
  onRefreshRef.current = onRefresh;
  const [refreshing, setRefreshing] = useState(false);
  const refreshingRef = useRef(false);
  const [announce, setAnnounce] = useState('');

  useEffect(() => {
    const root = rootRef.current;
    const indicator = indicatorRef.current;
    if (!enabled || !root || !indicator) return undefined;

    let g = null; // the gesture in progress

    const paint = (scroller, d, { animate = false } = {}) => {
      const still = reducedMotion();
      const progress = Math.min(1, d / ARM);
      const ease = animate ? 'transform 220ms var(--ease-out), opacity 220ms ease' : 'none';
      if (!still) {
        scroller.style.transition = animate ? 'transform 220ms var(--ease-out)' : 'none';
        scroller.style.transform = d > 0 ? `translate3d(0, ${d}px, 0)` : '';
      }
      indicator.style.transition = ease;
      const y = still ? 12 : d / 2 - 18;
      indicator.style.transform = `translate3d(-50%, ${y}px, 0)`;
      indicator.style.opacity = refreshingRef.current ? '1' : String(progress);
      indicator.style.setProperty('--ptr-turn', `${progress * 270}deg`);
    };

    const settle = (scroller, to) => {
      paint(scroller, to, { animate: true });
      if (to === 0) {
        // A lingering transform would make the list a containing block for
        // fixed-position descendants, so it is cleared once the list is home.
        const done = () => {
          if (!g) { scroller.style.transition = ''; scroller.style.transform = ''; }
        };
        scroller.addEventListener('transitionend', done, { once: true });
      }
    };

    const onStart = (e) => {
      if (refreshingRef.current || e.touches.length !== 1) { g = null; return; }
      const scroller = e.target.closest?.(SCROLLERS);
      if (!scroller || !root.contains(scroller) || scroller.scrollTop > 0) { g = null; return; }
      const t = e.touches[0];
      // Grabbing a list that is still settling continues from where it is.
      const base = currentTranslateY(scroller);
      g = { scroller, x: t.clientX, y: t.clientY, base, d: base, mode: 'pending', armed: false, samples: [] };
    };

    const onMove = (e) => {
      if (!g) return;
      if (e.touches.length !== 1) { settle(g.scroller, 0); g = null; return; }
      const t = e.touches[0];
      const dx = t.clientX - g.x;
      const dy = t.clientY - g.y;

      if (g.mode === 'pending') {
        if (Math.abs(dx) < HYSTERESIS && Math.abs(dy) < HYSTERESIS) return;
        if (dy <= 0 || Math.abs(dx) > Math.abs(dy) || g.scroller.scrollTop > 0) { g = null; return; }
        g.mode = 'pull';
        g.y = t.clientY - HYSTERESIS;
      }

      const travel = Math.max(0, t.clientY - g.y);
      const d = g.base + rubberband(travel, root.clientHeight || 700);
      if (d <= 0 && dy < 0) return; // moving back up: let the list scroll again
      e.preventDefault();

      g.d = d;
      const now = performance.now();
      g.samples.push({ t: now, y: t.clientY });
      while (g.samples.length > 2 && now - g.samples[0].t > 100) g.samples.shift();

      const armed = d >= ARM;
      if (armed !== g.armed) {
        g.armed = armed;
        indicator.toggleAttribute('data-armed', armed);
        if (armed && navigator.vibrate) navigator.vibrate(10);
      }
      paint(g.scroller, d);
    };

    const onEnd = async () => {
      if (!g || g.mode !== 'pull') { g = null; return; }
      const { scroller, d, samples, armed } = g;
      g = null;

      const first = samples[0];
      const last = samples[samples.length - 1];
      const speed = first && last && last.t > first.t ? (last.y - first.y) / (last.t - first.t) : 0;
      const commit = armed || (d >= FLICK_MIN && speed >= FLICK_SPEED);

      indicator.removeAttribute('data-armed');
      if (!commit) { settle(scroller, 0); return; }

      refreshingRef.current = true;
      setRefreshing(true);
      setAnnounce('Refreshing');
      settle(scroller, HOLD);
      const started = Date.now();
      try {
        await onRefreshRef.current?.();
      } finally {
        const wait = MIN_SPIN_MS - (Date.now() - started);
        if (wait > 0) await new Promise((r) => setTimeout(r, wait));
        refreshingRef.current = false;
        setRefreshing(false);
        setAnnounce('Updated');
        settle(scroller, 0);
      }
    };

    root.addEventListener('touchstart', onStart, { passive: true });
    root.addEventListener('touchmove', onMove, { passive: false });
    root.addEventListener('touchend', onEnd);
    root.addEventListener('touchcancel', onEnd);
    return () => {
      root.removeEventListener('touchstart', onStart);
      root.removeEventListener('touchmove', onMove);
      root.removeEventListener('touchend', onEnd);
      root.removeEventListener('touchcancel', onEnd);
    };
  }, [enabled]);

  return (
    <div className="ptr-root" ref={rootRef}>
      <div className={`ptr-indicator${refreshing ? ' is-refreshing' : ''}`} ref={indicatorRef} aria-hidden="true">
        <span className="material-symbols-outlined">refresh</span>
      </div>
      <span className="ptr-status" role="status" aria-live="polite">{announce}</span>
      {children}
    </div>
  );
}
