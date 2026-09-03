"""Common utilities and attribute constants for extracting and resolving lazy-loaded images."""
import re

LAZY_IMAGE_ATTRIBUTES: list[str] = [
    "data-src",
    "data-lazy-src",
    "data-original",
    "data-lazy",
    "data-actualsrc",
    "data-lazyload",
    "data-full-url",
    "data-image",
    "data-hi-res",
    "data-zoom-target",
]

SRCSET_ATTRIBUTES: list[str] = [
    "data-srcset",
    "data-lazy-srcset",
    "srcset",
]


def is_placeholder_src(src: str) -> bool:
    """Return True if src is empty or matches known transparent/placeholder image patterns."""
    if not src:
        return True
    s = src.strip().lower()
    return (
        s.startswith("data:")
        or s.endswith("blank.gif")
        or s.endswith("spacer.gif")
        or s.endswith("placeholder.gif")
        or s.endswith("placeholder.png")
    )


def extract_srcset_candidate(srcset: str) -> str:
    """Parse srcset candidates and return the preferred image URL (<= 1600w bounded max)."""
    if not srcset:
        return ""

    candidates = []
    pattern = re.compile(
        r"(?P<url>\S+)(?:\s+(?P<descriptor>\d+w|\d+(?:\.\d+)?x))?(?=,\s*(?:https?:|//|/)|$)"
    )
    for match in pattern.finditer(srcset):
        # Strip a separator comma the greedy \S+ may absorb when srcset
        # entries are comma-separated without a following space (e.g. BBC:
        # "...240w,https://...320w"), which would yield ",https://..." URLs.
        url = (match.group("url") or "").lstrip(", \t\n").rstrip(",")
        descriptor = match.group("descriptor") or ""
        if not url:
            continue
        width = 0
        if descriptor.endswith("w"):
            try:
                width = int(descriptor[:-1])
            except ValueError:
                width = 0
        candidates.append((width, url))

    if not candidates:
        return ""

    bounded = [candidate for candidate in candidates if 0 < candidate[0] <= 1600]
    if bounded:
        return max(bounded, key=lambda item: item[0])[1]
    return max(candidates, key=lambda item: item[0])[1] if any(c[0] for c in candidates) else candidates[-1][1]
