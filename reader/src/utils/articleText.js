/**
 * Shared article text sanitization and relative date formatting helpers.
 */

export function formatRelativeDate(isoString, { short = false } = {}) {
  if (!isoString) return '';
  const d = new Date(isoString);
  const now = new Date();
  const diffMs = Math.max(0, now - d);
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (short) {
    if (diffMins === 0) return 'now';
    if (diffMins < 60) return `${diffMins}m`;
    if (diffHours < 24) return `${diffHours}h`;
    if (diffDays === 1) return 'yesterday';
    if (diffDays <= 7) return `${diffDays}d`;
  } else {
    if (diffMins === 0) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays === 1) return 'yesterday';
    if (diffDays <= 7) return `${diffDays}d ago`;
  }

  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function cleanTranslationTag(text) {
  if (!text) return '';
  let s = text;
  s = s.replace(/^[🌐\s]*Translated by\s+[a-zA-Z0-9\s\(\)\-\_\,\.]*[\:—\–\-\s]*/gi, '');
  s = s.replace(/🌐\s*Translated by\s+[a-zA-Z0-9\s\(\)\-\_\,\.]*[\:—\–\-\s]*/gi, '');
  return s.trim();
}

// Some feeds open their description with the site's own trail ("TOP > ニュース > ").
// Two separators minimum, so a lone "a > b" in real prose is left alone.
export function stripBreadcrumb(text) {
  return (text || '').replace(/^(?:[^>＞›»。.!?！？\n]{1,24}\s*[>＞›»]\s*){2,}/u, '');
}

export function stripHtml(html) {
  if (!html) return '';
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const elements = doc.querySelectorAll('p, div, span, small');
  elements.forEach(el => {
    const txt = (el.textContent || '').trim();
    if ((txt.startsWith('🌐') || txt.includes('Translated by')) && txt.length < 120) {
      el.remove();
    }
  });
  const rawText = doc.body.textContent || '';
  return cleanTranslationTag(rawText);
}

const LANGUAGE_SHORT = { zh: '中文', ja: '日本語', ko: '한국어' };

function languageLabel(code, { native = false } = {}) {
  const base = String(code || '').toLowerCase().split('-')[0];
  if (!base) return '';
  return native && LANGUAGE_SHORT[base] ? LANGUAGE_SHORT[base] : base.toUpperCase();
}

// "JA → 中文" for an article the pipeline translated, else null.
export function translationLabel(article) {
  if (!article?.translated_from) return null;
  const to = languageLabel(article.translated_to || 'zh', { native: true });
  return `${languageLabel(article.translated_from)} → ${to}`;
}

// Category names carry a leading emoji in the data; the index and sidebar set them
// as plain text. Falls back to the stored name if nothing else is left.
export function displayCategoryName(name) {
  const s = String(name || '');
  const stripped = s.replace(/^[\p{Extended_Pictographic}\u{1F1E6}-\u{1F1FF}️‍\s]+/u, '');
  return stripped || s;
}

// Roughly 230 words a minute for spaced scripts and 500 characters a minute for
// Chinese and Japanese, which carry no word breaks to count. Original-language
// glosses (blockquotes) are skipped so a translated article is not counted twice.
let readingCache = { html: null, minutes: 0 };

export function estimateReadingMinutes(html) {
  if (!html) return 0;
  if (readingCache.html === html) return readingCache.minutes;
  const doc = new DOMParser().parseFromString(html, 'text/html');
  doc.querySelectorAll('blockquote, script, style').forEach(el => el.remove());
  const text = doc.body.textContent || '';
  const cjkPattern = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]/g;
  const cjk = (text.match(cjkPattern) || []).length;
  const words = text.replace(cjkPattern, ' ').split(/\s+/).filter(Boolean).length;
  const minutes = text.trim() ? Math.max(1, Math.round(cjk / 500 + words / 230)) : 0;
  readingCache = { html, minutes };
  return minutes;
}
