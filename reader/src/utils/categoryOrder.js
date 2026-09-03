// reader/src/utils/categoryOrder.js

/** Where the settings "Edit Categories Order" modal persists the user's order. */
export const CATEGORY_ORDER_KEY = 'reader_category_order';

/** Saved order as an array of category ids; [] for anything unusable. */
export function parseCategoryOrder(raw) {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    return [];
  }
}

/**
 * Apply the user's custom category order.
 *
 * Categories absent from the saved order sort to the end, keeping their incoming
 * relative order (Array#sort is stable). Compare on the raw `id` — Uncategorized
 * arrives as null and is stored as null, so any display substitution has to happen
 * after this, not before.
 */
export function sortByCategoryOrder(categories, savedOrder) {
  if (!Array.isArray(categories)) return [];
  const order = Array.isArray(savedOrder) ? savedOrder : [];

  return [...categories].sort((a, b) => {
    const idxA = order.indexOf(a.id);
    const idxB = order.indexOf(b.id);
    if (idxA === -1 && idxB === -1) return 0;
    if (idxA === -1) return 1;
    if (idxB === -1) return -1;
    return idxA - idxB;
  });
}
