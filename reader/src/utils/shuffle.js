// reader/src/utils/shuffle.js

/** Fisher-Yates. `random` is injectable so callers can make it deterministic in tests. */
export function shuffleArray(items, random = Math.random) {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

/**
 * Order articles for the index, keeping already-placed ones exactly where they are
 * and shuffling only the newcomers onto the end.
 *
 * A plain reshuffle of the whole list would be wrong here: infinite scroll keeps
 * growing the array, and re-shuffling n+8 items rearranges the cards the reader is
 * currently looking at. Pass an empty `previousIds` to deal a fresh random order —
 * that is what a category change does.
 */
export function mergeShuffledOrder(previousIds, items, random = Math.random) {
  const byId = new Map(items.map(a => [a.id, a]));
  const placed = [];
  const seen = new Set();

  for (const id of previousIds) {
    if (seen.has(id)) continue;
    const article = byId.get(id);
    if (!article) continue;
    placed.push(article);
    seen.add(id);
  }

  const arrivals = items.filter(a => !seen.has(a.id));
  return [...placed, ...shuffleArray(arrivals, random)];
}
