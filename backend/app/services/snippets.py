"""Card preview text, derived once per article and stored."""
from bs4 import BeautifulSoup

CONTENT_SCAN_LIMIT = 6000
SNIPPET_CHARS = 200
FEATURED_SNIPPET_CHARS = 1200


def build_snippets(content_html: str | None) -> tuple[str, str]:
    """Return (snippet, featured_snippet) for an article body.

    The input is truncated to CONTENT_SCAN_LIMIT because the read path historically
    parsed SUBSTR(content, 1, 6000). Truncating here rather than at each call site is
    what keeps a stored snippet identical to a fallback-generated one.
    """
    if not content_html:
        return "", ""

    soup = BeautifulSoup(content_html[:CONTENT_SCAN_LIMIT], "html.parser")
    for el in soup.find_all(["p", "div", "span", "small"]):
        txt = el.get_text(strip=True)
        if (txt.startswith("\U0001F310") or "Translated by" in txt) and len(txt) < 120:
            el.decompose()

    text = soup.get_text(separator=" ", strip=True)
    snippet = text[:SNIPPET_CHARS] + ("..." if len(text) > SNIPPET_CHARS else "")
    featured = text[:FEATURED_SNIPPET_CHARS] + ("..." if len(text) > FEATURED_SNIPPET_CHARS else "")
    return snippet, featured
