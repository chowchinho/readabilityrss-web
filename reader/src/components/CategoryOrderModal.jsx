import React, { useState, useEffect, useLayoutEffect, useRef } from 'react';
import { Dialog } from '@base-ui/react/dialog';
import '../styles/CategoryOrderModal.css';
import { CATEGORY_ORDER_KEY, parseCategoryOrder, sortByCategoryOrder } from '../utils/categoryOrder';
import { displayCategoryName } from '../utils/articleText';

// Reordering uses move up / move down buttons rather than drag and drop: native
// HTML drag events never fire on touch screens, so the old handles did nothing in
// the Android app. Base UI's Dialog provides focus trapping, Escape and outside
// clicks, and keeps the dialog mounted until its exit transition finishes.
export default function CategoryOrderModal({ categories, onClose, onSave }) {
  const [open, setOpen] = useState(true);
  const [items, setItems] = useState([]);
  const rowRefs = useRef(new Map());
  const lastRects = useRef(null);
  const focusAfterMove = useRef(null);

  useEffect(() => {
    const savedOrder = parseCategoryOrder(localStorage.getItem(CATEGORY_ORDER_KEY));
    setItems(sortByCategoryOrder(categories, savedOrder));
  }, [categories]);

  const keyOf = (cat) => String(cat.id ?? 'uncat');

  const move = (index, delta) => {
    const target = index + delta;
    if (target < 0 || target >= items.length) return;
    // Record where every row is now, so the reorder can glide instead of jump.
    const rects = new Map();
    rowRefs.current.forEach((el, key) => { if (el) rects.set(key, el.getBoundingClientRect().top); });
    lastRects.current = rects;
    focusAfterMove.current = { key: keyOf(items[index]), dir: delta < 0 ? 'up' : 'down' };
    const next = [...items];
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved);
    setItems(next);
  };

  // FLIP: each row starts at its old position and eases into the new one.
  useLayoutEffect(() => {
    const before = lastRects.current;
    if (!before) return;
    lastRects.current = null;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    rowRefs.current.forEach((el, key) => {
      if (!el || !before.has(key)) return;
      const dy = before.get(key) - el.getBoundingClientRect().top;
      if (!dy || reduced) return;
      el.animate(
        [{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0)' }],
        { duration: 200, easing: 'cubic-bezier(0.23, 1, 0.32, 1)' }
      );
    });
    // Keep focus on the row that moved, so repeated presses keep moving it.
    const f = focusAfterMove.current;
    focusAfterMove.current = null;
    if (f) {
      const row = rowRefs.current.get(f.key);
      const btn = row?.querySelector(`[data-move="${f.dir}"]:not(:disabled)`) || row?.querySelector('[data-move]:not(:disabled)');
      btn?.focus();
    }
  }, [items]);

  const handleSave = () => {
    const order = items.map(cat => cat.id);
    localStorage.setItem(CATEGORY_ORDER_KEY, JSON.stringify(order));
    if (onSave) onSave(order);
    setOpen(false);
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={setOpen}
      onOpenChangeComplete={(isOpen) => { if (!isOpen) onClose(); }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="cat-modal-overlay" />
        <Dialog.Viewport className="cat-modal-viewport">
          <Dialog.Popup className="cat-modal-content">
            <div className="cat-modal-header">
              <Dialog.Title className="cat-modal-title">Category order</Dialog.Title>
              <Dialog.Close className="cat-close-btn" aria-label="Close">
                <span className="material-symbols-outlined">close</span>
              </Dialog.Close>
            </div>
            <div className="cat-modal-body">
              <Dialog.Description className="cat-modal-help">
                Move categories up or down. The sidebar and the index follow this order.
              </Dialog.Description>
              <ol className="cat-list">
                {items.map((cat, index) => (
                  <li
                    key={keyOf(cat)}
                    ref={(el) => { if (el) rowRefs.current.set(keyOf(cat), el); else rowRefs.current.delete(keyOf(cat)); }}
                    className="cat-list-item"
                  >
                    <span className="cat-position" aria-hidden="true">{index + 1}</span>
                    <span className="cat-name">{displayCategoryName(cat.name) || 'Uncategorized'}</span>
                    <span className="cat-move">
                      <button
                        type="button"
                        className="cat-move-btn"
                        data-move="up"
                        onClick={() => move(index, -1)}
                        disabled={index === 0}
                        aria-label={`Move ${displayCategoryName(cat.name) || 'Uncategorized'} up`}
                      >
                        <span className="material-symbols-outlined">keyboard_arrow_up</span>
                      </button>
                      <button
                        type="button"
                        className="cat-move-btn"
                        data-move="down"
                        onClick={() => move(index, 1)}
                        disabled={index === items.length - 1}
                        aria-label={`Move ${displayCategoryName(cat.name) || 'Uncategorized'} down`}
                      >
                        <span className="material-symbols-outlined">keyboard_arrow_down</span>
                      </button>
                    </span>
                  </li>
                ))}
                {items.length === 0 && <li className="cat-empty">No categories available.</li>}
              </ol>
            </div>
            <div className="cat-modal-footer">
              <Dialog.Close className="btn-cancel-sm">Cancel</Dialog.Close>
              <button type="button" className="btn-success-sm" onClick={handleSave}>Save order</button>
            </div>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
