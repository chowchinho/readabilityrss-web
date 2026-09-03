// reader/src/utils/slug.js

/**
 * Generate a URL slug for a feed source or article.
 * Format: "{id}-{ascii-slug}" or just "{id}" if no ASCII content.
 *
 * Examples:
 *   makeSlug(87, "BBC News")              → "87-bbc-news"
 *   makeSlug(56, "AV Watch")              → "56-av-watch"
 *   makeSlug(5,  "ASCII.jp トップ")        → "5-ascii-jp"
 *   makeSlug(18, "T客邦")                  → "18-t"
 *   makeSlug(9,  "日刊電電")               → "9"
 */
export function makeSlug(id, name) {
  const ascii = (name || '')
    .replace(/[^\x00-\x7F]/g, ' ')   // strip non-ASCII (CJK etc.)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')      // non-alphanum → hyphen
    .slice(0, 50)                     // truncate
    .replace(/^-+|-+$/g, '');         // trim leading/trailing hyphens
  return ascii ? `${id}-${ascii}` : `${id}`;
}

/**
 * Extract the numeric ID from a slug.
 * Strips everything after (and including) the first "-".
 *
 * Examples:
 *   parseId("87-bbc-news")  → 87
 *   parseId("9")            → 9
 *   parseId(undefined)      → null
 */
export function parseId(slug) {
  if (slug === null || slug === undefined || slug === '') return null;
  const id = parseInt(slug, 10);
  return Number.isNaN(id) ? null : id;
}
