import React from 'react';
import { Popover } from '@base-ui/react/popover';

/**
 * The floating panel every card surface hangs its score breakdown from.
 *
 * Built on Base UI's Popover, anchored to the caller's info button. The positioner
 * uses fixed positioning in a portal on purpose: pane 2's .feed-container is an
 * overflow-y:auto scroller, so a panel positioned inside a card would be clipped at
 * the pane edge. Base UI keeps it attached while the pane scrolls, flips it above the
 * button when there is no room below, and exposes --transform-origin so it scales
 * out of the button rather than its own centre.
 *
 * Opening stays the caller's job (a hover dwell in useHoverDwellPanel). The panel is
 * not a DOM descendant of the anchor, so a wrapper's mouseleave fires the moment the
 * pointer heads towards it; onPointerEnter / onPointerLeave pair with a grace timer on
 * the anchor side. Escape and outside presses close it through onPointerLeave too.
 */
export default function FloatingInfoPanel({
  anchorRef,
  open,
  className = '',
  onPointerEnter,
  onPointerLeave,
  children,
}) {
  const handleOpenChange = (nextOpen, details) => {
    if (nextOpen) return;
    // A press on the info button is "outside" the panel, but that button already
    // toggles it; closing here as well would reopen it on the same click.
    const target = details?.event?.target;
    if (target && anchorRef?.current?.contains(target)) return;
    if (onPointerLeave) onPointerLeave();
  };

  return (
    <Popover.Root open={open} onOpenChange={handleOpenChange}>
      <Popover.Portal>
        <Popover.Positioner
          anchor={anchorRef}
          side="bottom"
          align="start"
          sideOffset={8}
          collisionPadding={8}
          positionMethod="fixed"
          className="ranking-info-positioner"
        >
          <Popover.Popup
            className={`ranking-info-popover is-floating ${className}`.trim()}
            initialFocus={false}
            finalFocus={false}
            onClick={(e) => e.stopPropagation()}
            onMouseEnter={onPointerEnter}
            onMouseLeave={onPointerLeave}
          >
            {children}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
