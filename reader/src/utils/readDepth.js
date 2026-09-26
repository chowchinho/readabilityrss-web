const BLOCK_TAG_NAMES = new Set(['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE', 'LI', 'FIGCAPTION']);

export function findTopLevelBlocks(root) {
  const allElements = Array.from(root.querySelectorAll('p, h1, h2, h3, h4, h5, h6, blockquote, li, figcaption'));
  return allElements.filter(el => {
    if (!el.textContent.trim()) return false;
    let parent = el.parentElement;
    while (parent && parent !== root) {
      if (BLOCK_TAG_NAMES.has(parent.tagName)) return false;
      parent = parent.parentElement;
    }
    return Boolean(el.innerHTML.trim());
  });
}

// Translation stores each block as <blockquote><p>original</p></blockquote><p>translation</p>.
// The original is skipped so reading time reflects the text actually read. An ordinary
// pull quote of the same shape is skipped too, which only slightly under-estimates length.
function isInterleavedOriginal(el) {
  if (el.tagName !== 'BLOCKQUOTE' || el.children.length !== 1) return false;
  const next = el.nextElementSibling;
  return Boolean(next && next.tagName === el.children[0].tagName);
}

const CJK = /[぀-ヿ㐀-鿿豈-﫿가-힯]/g;
const CJK_PER_MINUTE = 450;
const WORDS_PER_MINUTE = 230;

export function estimateReadSeconds(text) {
  const cjk = (text.match(CJK) || []).length;
  const words = text.replace(CJK, ' ').split(/\s+/).filter(Boolean).length;
  return (cjk / CJK_PER_MINUTE + words / WORDS_PER_MINUTE) * 60;
}

export const MIN_READ_SECONDS = 15;
export const MAX_READ_SECONDS = 90;

export function requiredReadSeconds(text) {
  const half = estimateReadSeconds(text) / 2;
  return Math.min(MAX_READ_SECONDS, Math.max(MIN_READ_SECONDS, half));
}

// The block at which 80% of the article's text has been passed. Measured on text rather
// than scroll pixels: scrollHeight includes the header, lead image, galleries and footer,
// and a short article is already past a pixel ratio of 0.8 before any scroll.
export function measureReadDepth(root, fraction = 0.8) {
  const blocks = findTopLevelBlocks(root).filter(el => !isInterleavedOriginal(el));
  const lengths = blocks.map(el => el.textContent.trim().length);
  const total = lengths.reduce((a, b) => a + b, 0);
  if (!total) return null;
  let running = 0;
  let anchor = blocks[blocks.length - 1];
  for (let i = 0; i < blocks.length; i++) {
    running += lengths[i];
    if (running >= total * fraction) {
      anchor = blocks[i];
      break;
    }
  }
  const text = blocks.map(el => el.textContent).join(' ');
  return { anchor, requiredSeconds: requiredReadSeconds(text) };
}
