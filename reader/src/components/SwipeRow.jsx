import React, { useEffect, useRef } from 'react';

// Phones: swipe a list row left to reveal Read and Hide, or right to flip read
// state in one move. The row follows the finger 1:1, resists past its limits, and
// lands where the gesture was heading (momentum projection), not where it stopped.
// Transforms are written straight to the element, so a drag never re-renders.

const ACTIONS_W = 156;   // two 78px actions
const ARM_RIGHT = 88;    // a right swipe past this flips read state on release
const HYSTERESIS = 10;
const DECELERATION = 0.99;

let closeOpenRow = null; // one row open at a time

function project(velocityPxPerMs) {
  return velocityPxPerMs * DECELERATION / (1 - DECELERATION);
}

function rubberband(overshoot, dimension = 320, constant = 0.55) {
  return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));
}

function reducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export default function SwipeRow({ isRead, onToggleRead, onHide, children }) {
  const rowRef = useRef(null);
  const contentRef = useRef(null);
  const offsetRef = useRef(0);
  const handlersRef = useRef({ onToggleRead, onHide });
  handlersRef.current = { onToggleRead, onHide };

  const setOffset = (x, animate) => {
    const el = contentRef.current;
    if (!el) return;
    offsetRef.current = x;
    el.style.transition = animate && !reducedMotion() ? 'transform 280ms var(--ease-out)' : 'none';
    el.style.transform = x ? `translate3d(${x}px, 0, 0)` : '';
    rowRef.current?.toggleAttribute('data-open', x < 0);
  };

  const close = () => {
    setOffset(0, true);
    if (closeOpenRow === close) closeOpenRow = null;
  };

  useEffect(() => {
    const row = rowRef.current;
    if (!row) return undefined;
    let g = null;
    let suppressClick = false;

    const onStart = (e) => {
      if (e.touches.length !== 1) { g = null; return; }
      if (closeOpenRow && closeOpenRow !== close) closeOpenRow();
      const t = e.touches[0];
      g = { x: t.clientX, y: t.clientY, base: offsetRef.current, mode: 'pending', armed: false, samples: [] };
    };

    const onMove = (e) => {
      if (!g || g.mode === 'none') return;
      const t = e.touches[0];
      const dx = t.clientX - g.x;
      const dy = t.clientY - g.y;
      if (g.mode === 'pending') {
        if (Math.abs(dx) < HYSTERESIS && Math.abs(dy) < HYSTERESIS) return;
        if (Math.abs(dy) >= Math.abs(dx)) { g.mode = 'none'; return; }
        g.mode = 'drag';
        g.x = t.clientX - Math.sign(dx) * HYSTERESIS;
      }
      e.preventDefault();
      let x = g.base + (t.clientX - g.x);
      if (x < -ACTIONS_W) x = -ACTIONS_W + rubberband(x + ACTIONS_W);
      if (x > ARM_RIGHT) x = ARM_RIGHT + rubberband(x - ARM_RIGHT);
      const armed = x >= ARM_RIGHT;
      if (armed !== g.armed) {
        g.armed = armed;
        row.toggleAttribute('data-armed', armed);
        if (armed && navigator.vibrate) navigator.vibrate(10);
      }
      row.toggleAttribute('data-right', x > 0);
      const now = performance.now();
      g.samples.push({ t: now, x: t.clientX });
      while (g.samples.length > 2 && now - g.samples[0].t > 100) g.samples.shift();
      setOffset(x, false);
    };

    const onEnd = () => {
      if (!g || g.mode !== 'drag') { g = null; return; }
      const { samples, armed } = g;
      g = null;
      suppressClick = true;
      setTimeout(() => { suppressClick = false; }, 0);
      row.removeAttribute('data-armed');
      const x = offsetRef.current;
      const first = samples[0];
      const last = samples[samples.length - 1];
      const v = first && last && last.t > first.t ? (last.x - first.x) / (last.t - first.t) : 0;

      if (x > 0) {
        if (armed || (v > 0.5 && x > 40)) handlersRef.current.onToggleRead?.();
        setOffset(0, true);
        row.removeAttribute('data-right');
        return;
      }
      // Decide from where the gesture was heading, not where the finger let go.
      const landing = x + project(v);
      if (landing < -ACTIONS_W / 2) {
        setOffset(-ACTIONS_W, true);
        closeOpenRow = close;
      } else {
        close();
      }
    };

    // A tap on an open row closes it instead of opening the article, and the click
    // that ends a drag must not open it either.
    const onClickCapture = (e) => {
      if (e.target.closest('.swipe-actions')) return;
      if (suppressClick || offsetRef.current !== 0) {
        e.stopPropagation();
        e.preventDefault();
        if (offsetRef.current !== 0) close();
      }
    };

    row.addEventListener('touchstart', onStart, { passive: true });
    row.addEventListener('touchmove', onMove, { passive: false });
    row.addEventListener('touchend', onEnd);
    row.addEventListener('touchcancel', onEnd);
    row.addEventListener('click', onClickCapture, true);
    return () => {
      row.removeEventListener('touchstart', onStart);
      row.removeEventListener('touchmove', onMove);
      row.removeEventListener('touchend', onEnd);
      row.removeEventListener('touchcancel', onEnd);
      row.removeEventListener('click', onClickCapture, true);
      if (closeOpenRow === close) closeOpenRow = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Scrolling the list puts an open row away.
  useEffect(() => {
    const onScroll = () => { if (offsetRef.current < 0) close(); };
    document.addEventListener('scroll', onScroll, { capture: true, passive: true });
    return () => document.removeEventListener('scroll', onScroll, { capture: true });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const act = (fn) => (e) => {
    e.stopPropagation();
    close();
    fn?.();
  };

  return (
    <div className="swipe-row" ref={rowRef}>
      <div className="swipe-under" aria-hidden="true">
        <span className="material-symbols-outlined">{isRead ? 'radio_button_unchecked' : 'check_circle'}</span>
        <span>{isRead ? 'Unread' : 'Read'}</span>
      </div>
      <div className="swipe-actions">
        <button type="button" className="swipe-action is-read" onClick={act(onToggleRead)} tabIndex={-1}>
          <span className="material-symbols-outlined">{isRead ? 'radio_button_unchecked' : 'check_circle'}</span>
          {isRead ? 'Unread' : 'Read'}
        </button>
        <button type="button" className="swipe-action is-hide" onClick={act(onHide)} tabIndex={-1}>
          <span className="material-symbols-outlined">visibility_off</span>
          Hide
        </button>
      </div>
      <div className="swipe-content" ref={contentRef}>{children}</div>
    </div>
  );
}
