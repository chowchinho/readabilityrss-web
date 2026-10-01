import React from 'react';
import { Popover } from '@base-ui/react/popover';
import { TEXT_SCALES, isDefaultTextScale, resetTextScaleIndex } from '../utils/translationView';

// Body text size for the reading pane. Steps rather than a slider: five sizes are
// enough to cover comfortable reading, and each press is a visible, undoable change.
export default function TextSizeControl({ index, onChange, triggerClassName = 'action-btn icon-only', side = 'bottom' }) {
  const last = TEXT_SCALES.length - 1;
  return (
    <Popover.Root>
      <Popover.Trigger className={triggerClassName} title="Text size" aria-label="Text size">
        <span className="material-symbols-outlined">text_fields</span>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side={side} align="end" sideOffset={8} collisionPadding={8} className="text-size-positioner">
          <Popover.Popup className="text-size-popup">
            <div className="text-size-row">
              <button
                type="button"
                className="text-size-step is-smaller"
                onClick={() => onChange(Math.max(0, index - 1))}
                disabled={index === 0}
                aria-label="Smaller text"
              >
                A
              </button>
              <div className="text-size-scale" aria-hidden="true">
                {TEXT_SCALES.map((_, i) => (
                  <i key={i} className={i <= index ? 'is-on' : ''} />
                ))}
              </div>
              <button
                type="button"
                className="text-size-step is-larger"
                onClick={() => onChange(Math.min(last, index + 1))}
                disabled={index === last}
                aria-label="Larger text"
              >
                A
              </button>
            </div>
            <button
              type="button"
              className="text-size-reset"
              onClick={() => onChange(resetTextScaleIndex())}
              disabled={isDefaultTextScale(index)}
            >
              Default size
            </button>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
