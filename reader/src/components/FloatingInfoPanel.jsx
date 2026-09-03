import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const PANEL_MARGIN = 8;

/**
 * The floating panel every card surface hangs its score breakdown from.
 *
 * Portalled to document.body and fixed-positioned on purpose: pane 2's
 * .feed-container is an overflow-y:auto scroller, so a panel positioned inside a card
 * is clipped at the pane edge and scrolls away with the content. Fixed coordinates
 * derived from the anchor's own rect let it float above the interface - including over
 * the pane's scrollbar - and a portal stays immune if some ancestor later gains a
 * transform and becomes the containing block.
 *
 * The panel is not a DOM descendant of the anchor, so a wrapper's mouseleave fires the
 * moment the pointer heads towards it. Closing is therefore the caller's job via
 * onPointerEnter / onPointerLeave, which pair with a grace timer on the anchor side.
 */
export default function FloatingInfoPanel({
  anchorRef,
  open,
  className = '',
  onPointerEnter,
  onPointerLeave,
  children,
}) {
  const panelRef = useRef(null);
  const [anchor, setAnchor] = useState(null);

  const place = useCallback(() => {
    const el = anchorRef?.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setAnchor({ left: r.left, top: r.top, bottom: r.bottom });
  }, [anchorRef]);

  useEffect(() => {
    if (!open) return;
    place();
    const onMove = () => place();
    // Capture phase: it is pane 2 that scrolls, not the window.
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [open, place]);

  if (!open) return null;

  const style = (() => {
    if (!anchor) return { visibility: 'hidden' };
    const h = panelRef.current?.offsetHeight || 240;
    const w = panelRef.current?.offsetWidth || 460;
    const roomBelow = window.innerHeight - anchor.bottom;
    const above = roomBelow < h + PANEL_MARGIN && anchor.top > roomBelow;
    const left = Math.min(
      Math.max(PANEL_MARGIN, anchor.left),
      Math.max(PANEL_MARGIN, window.innerWidth - w - PANEL_MARGIN)
    );
    return above
      ? { left, bottom: window.innerHeight - anchor.top + PANEL_MARGIN }
      : { left, top: anchor.bottom + PANEL_MARGIN };
  })();

  return createPortal(
    <div
      ref={panelRef}
      className={`ranking-info-popover is-floating ${className}`.trim()}
      style={style}
      onClick={(e) => e.stopPropagation()}
      onMouseEnter={onPointerEnter}
      onMouseLeave={onPointerLeave}
    >
      {children}
    </div>,
    document.body
  );
}
