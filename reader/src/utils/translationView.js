// A translated article is stored with each block paired up: the original wrapped in
// a <blockquote>, the translation immediately after it as the same tag, e.g.
//   <blockquote><p>original</p></blockquote><p>translation</p>
// (see _interleave_translation in backend/app/services/translation.py). The three
// reading views are all derived from that one stored form.

export const TRANSLATION_VIEWS = ['original', 'both', 'translation'];
const VIEW_KEY = 'reader_translation_view';
const SCALE_KEY = 'reader_text_scale';

export const TEXT_SCALES = [0.875, 0.9375, 1, 1.125, 1.25];
const DEFAULT_SCALE_INDEX = 2;

const BLOCK_TAGS = new Set(['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'LI', 'BLOCKQUOTE']);

function read(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}

function write(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* private mode */ }
}

export function loadTranslationView() {
  const v = read(VIEW_KEY);
  return TRANSLATION_VIEWS.includes(v) ? v : 'both';
}

export function saveTranslationView(view) {
  write(VIEW_KEY, view);
}

export function loadTextScaleIndex() {
  const i = Number.parseInt(read(SCALE_KEY), 10);
  return Number.isInteger(i) && i >= 0 && i < TEXT_SCALES.length ? i : DEFAULT_SCALE_INDEX;
}

export function saveTextScaleIndex(index) {
  write(SCALE_KEY, String(index));
}

export function isDefaultTextScale(index) {
  return index === DEFAULT_SCALE_INDEX;
}

export function resetTextScaleIndex() {
  return DEFAULT_SCALE_INDEX;
}

export function languageName(code) {
  if (!code) return null;
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) || code;
  } catch {
    return code;
  }
}

function findPairs(root) {
  const pairs = [];
  root.querySelectorAll('blockquote').forEach((bq) => {
    if (bq.children.length !== 1) return;
    const source = bq.firstElementChild;
    const translated = bq.nextElementSibling;
    if (!translated || !BLOCK_TAGS.has(source.tagName) || translated.tagName !== source.tagName) return;
    pairs.push({ gloss: bq, source, translated });
  });
  return pairs;
}

// The "Translated by X" line the pipeline stamps at the top of the body. The bar
// says the same thing, so the line is dropped whenever the bar is shown.
function findBadge(root) {
  const first = root.firstElementChild;
  if (first && first.tagName === 'P' && /Translated by/.test(first.textContent || '')) return first;
  return null;
}

export function providerFromContent(html) {
  const match = /Translated by ([^<]+)</.exec(html || '');
  return match ? match[1].trim() : null;
}

/**
 * Returns the HTML for one reading view plus how many translated pairs it found.
 * Every pair's visible element carries data-pair="i" in all three views, so the
 * reader can hold its place on the same passage when the view changes.
 */
export function buildTranslationView(html, view, sourceLang = null) {
  if (!html) return { html, pairCount: 0 };
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const pairs = findPairs(doc.body);
  if (pairs.length === 0) return { html, pairCount: 0 };

  findBadge(doc.body)?.remove();

  pairs.forEach(({ gloss, source, translated }, i) => {
    if (view === 'translation') {
      gloss.remove();
      translated.setAttribute('data-pair', String(i));
    } else if (view === 'original') {
      // The original takes the translation's place, so lists keep their numbering
      // and headings their level, and the text reads as body copy, not as a gloss.
      source.setAttribute('data-pair', String(i));
      if (sourceLang) source.setAttribute('lang', sourceLang);
      source.classList.remove('rr-fresh-block');
      translated.replaceWith(source);
      gloss.remove();
    } else {
      gloss.setAttribute('data-pair', String(i));
      if (sourceLang) gloss.setAttribute('lang', sourceLang);
    }
  });

  return { html: doc.body.innerHTML, pairCount: pairs.length };
}
