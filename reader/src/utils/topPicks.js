// Phone list: the articles shown as a full-width card instead of a square thumbnail.
// See docs/superpowers/specs/2026-09-30-mobile-reader-redesign-design.md, Phase 1.

const TOP_SHARE = 0.1;
// Square rows required between two feature cards, so they never sit together.
const MIN_GAP = 4;
// A feature image is about 358pt wide; below this it would be a visible upscale.
export const MIN_FEATURE_IMAGE_WIDTH = 600;

// Decisions are kept per article id for the session. Reading, voting or loading more
// changes the list, and a card that switched between large and square would move
// everything under the reader's thumb.
const decisions = new Map();
const smallImages = new Set();

export function hasCardImage(article) {
  return Boolean(article.main_image_proxy || article.main_image);
}

// Scores cluster near the top of the range, so a fixed cut-off picks too many or none.
// Rank within everything loaded, not the rows rendered so far: the list renders in
// chunks of 20, and judged against the first chunk alone an article would fail a
// stricter cut-off and keep that verdict for the session.
function scoreThreshold(articles) {
  const scores = articles
    .map((a) => a.score)
    .filter((s) => typeof s === 'number')
    .sort((a, b) => b - a);
  if (scores.length === 0) return Infinity;
  const count = Math.max(1, Math.ceil(scores.length * TOP_SHARE));
  return scores[count - 1];
}

export function selectTopPicks(articles, pool = articles) {
  const threshold = scoreThreshold(pool);
  const picks = new Set();
  let sinceLast = MIN_GAP;

  for (const article of articles) {
    let featured = decisions.get(article.id);
    if (featured === undefined) {
      featured = sinceLast >= MIN_GAP
        && typeof article.score === 'number'
        && article.score >= threshold
        && hasCardImage(article)
        && !smallImages.has(article.id);
      decisions.set(article.id, featured);
    }
    if (featured && !smallImages.has(article.id)) {
      picks.add(article.id);
      sinceLast = 0;
    } else {
      sinceLast += 1;
    }
  }
  return picks;
}

export function isSmallFeatureImage(id) {
  return smallImages.has(id);
}

export function markSmallFeatureImage(id) {
  smallImages.add(id);
}
