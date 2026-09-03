"""Canonical form for secondary topic labels.

`secondary` is the one tag axis the classifier fills from an open vocabulary - primary,
region and type are all validated against closed sets in topic_classifier.py. The result
is that the archive holds 4856 distinct secondary strings, 286 of which are only case,
punctuation or plural variants of another: Smartphones/Smartphone, Model Kit/Model Kits,
Fashion & Accessories/Fashion Accessories. Each spelling accumulated its own votes, so a
label could be well liked under one spelling and unknown under the other.

Canonicalising is done at comparison time rather than by rewriting stored rows: the
original spelling stays in feed_articles, article_votes and user_article_events, so this
is reversible and no migration can corrupt existing votes. Display always uses a real
stored spelling - never the canonical key, which is lowercase and sometimes ungrammatical.
"""
import re

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")

# A trailing 's' here belongs to the stem: bus, analysis, chaos, lens.
_STEM_S = ("ss", "us", "is", "as", "os")

# Already singular despite the 'ies'/'es' ending, so the plural rules must skip them.
_ALREADY_SINGULAR = ("series", "species", "rabies", "clothes")


def _singular(word: str) -> str:
    if len(word) <= 3 or word.endswith(_ALREADY_SINGULAR):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    for tail in ("ses", "xes", "zes", "ches", "shes"):
        if word.endswith(tail):
            return word[:-2]
    if word.endswith(_STEM_S):
        return word
    if word.endswith("s"):
        return word[:-1]
    return word


def canonical_label(label) -> str:
    """Matching key for a secondary label. Empty string for anything unusable."""
    if not isinstance(label, str):
        return ""
    s = _PUNCT.sub(" ", label.lower().strip())
    s = _WS.sub(" ", s).strip()
    if not s:
        return ""
    return " ".join(_singular(w) for w in s.split())
