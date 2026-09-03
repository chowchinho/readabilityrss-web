// reader/src/utils/displayWindow.js

/** Articles added per pagination step in the Pane 2 feed. */
export const DISPLAY_BATCH = 20;

/**
 * Grow a slice window so it includes `index`, rounded up to whole batches.
 *
 * Pane 2 renders `filteredArticles.slice(0, displayLimit)` in date order, but the
 * Sources Index presents articles shuffled — so opening one from the index can
 * select an article far outside that window, leaving it unrendered and therefore
 * unhighlightable. Returns the limit unchanged when the index is already covered
 * or absent (-1).
 */
export function displayLimitCovering(currentLimit, index, batch = DISPLAY_BATCH) {
  if (index < 0 || index < currentLimit) return currentLimit;
  return Math.ceil((index + 1) / batch) * batch;
}
