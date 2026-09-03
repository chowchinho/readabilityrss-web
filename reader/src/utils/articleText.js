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
