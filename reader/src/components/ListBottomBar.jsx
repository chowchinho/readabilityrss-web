import React, { useEffect, useRef, useState } from 'react';

const CONFIRM_MS = 3000;

// Phones: the article list's actions, within thumb reach. Mark all read is the
// one mass change in the reader, so it asks once more in place (the same
// tap-twice pattern as the sidebar badge) instead of acting on the first tap.
export default function ListBottomBar({ showRead, onToggleShowRead, onMarkAllRead, markAllLabel }) {
  const barRef = useRef(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!confirming) return undefined;
    const t = setTimeout(() => setConfirming(false), CONFIRM_MS);
    return () => clearTimeout(t);
  }, [confirming]);

  // Same behaviour as the reading toolbar: out of the way while scrolling down the
  // list, back on any upward scroll and near the top. Scroll events do not bubble,
  // so the listener sits on the pane in the capture phase.
  useEffect(() => {
    const bar = barRef.current;
    const pane = bar?.parentElement;
    if (!pane) return undefined;
    // Each list remembers its own last position. A list seen for the first time is
    // assumed to start at the top, so a fling that arrives as one event still counts.
    const lastTop = new WeakMap();
    let frame = 0;
    let target = null;
    const update = () => {
      frame = 0;
      const top = target.scrollTop;
      const max = target.scrollHeight - target.clientHeight;
      const delta = top - (lastTop.get(target) ?? 0);
      if (top < 80 || max - top < 40 || delta < -6) bar.removeAttribute('data-hidden');
      else if (delta > 6) bar.setAttribute('data-hidden', '');
      lastTop.set(target, top);
    };
    const onScroll = (e) => {
      if (!(e.target instanceof Element)) return;
      target = e.target;
      if (!frame) frame = requestAnimationFrame(update);
    };
    pane.addEventListener('scroll', onScroll, { capture: true, passive: true });
    return () => {
      pane.removeEventListener('scroll', onScroll, { capture: true });
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  const unreadOnly = !showRead;

  return (
    <div className="list-bar" ref={barRef}>
      <button
        type="button"
        className={`list-bar-btn${unreadOnly ? ' is-on' : ''}`}
        onClick={() => onToggleShowRead(!showRead)}
        aria-pressed={unreadOnly}
        aria-label={unreadOnly ? 'Showing unread only. Tap to show read articles too' : 'Showing read articles too. Tap to show unread only'}
        title={unreadOnly ? 'Unread only' : 'Unread and read'}
      >
        <span className="material-symbols-outlined">filter_list</span>
      </button>

      <button
        type="button"
        className={`list-bar-btn is-mark${confirming ? ' is-confirming' : ''}`}
        onClick={() => {
          if (confirming) {
            setConfirming(false);
            onMarkAllRead();
          } else {
            setConfirming(true);
          }
        }}
        aria-label={confirming ? `Confirm: ${markAllLabel}` : markAllLabel}
        title={markAllLabel}
      >
        <span className="material-symbols-outlined">done_all</span>
        {confirming && <span className="list-bar-confirm">{markAllLabel}?</span>}
      </button>
    </div>
  );
}
