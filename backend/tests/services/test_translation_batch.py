"""Tests for batched Google translation of HTML block content.

The Google path in translate_html historically issued one HTTP request per
block element. For long listicles (300+ blocks) that blew past the per-article
and per-source refresh timeouts. These tests pin the batched behavior:
fewer requests, identical 1:1 output, and a safe per-block fallback.
"""
from unittest.mock import patch
import pytest
from bs4 import BeautifulSoup, NavigableString

from app.services import translation


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Override the conftest autouse fixture.

    These are pure unit tests for the translation service; they must not pull in
    app.main (the full FastAPI app and its heavier dependencies).
    """
    yield


def _bracket_translate(text, target_language="zh-TW", *args, **kwargs):
    """Fake translator: brackets visible text, preserves all markup.

    Simulates a real translator that translates text nodes while leaving HTML
    tags (including the data-i batch markers) untouched.
    """
    soup = BeautifulSoup(text, "html.parser")
    for node in list(soup.find_all(string=True)):
        s = str(node)
        if s.strip():
            node.replace_with(NavigableString(f"T[{s}]"))
    return str(soup)


def test_translate_blocks_returns_one_translation_per_block_in_order():
    blocks = ["Hello", "World", "Third"]
    with patch.object(translation, "_translate_google", side_effect=_bracket_translate):
        out = translation._translate_blocks_google(blocks, "zh-TW")
    assert out == ["T[Hello]", "T[World]", "T[Third]"]


def test_translate_blocks_preserves_inline_tags():
    blocks = ['Go <a href="http://x">here</a> now']
    with patch.object(translation, "_translate_google", side_effect=_bracket_translate):
        out = translation._translate_blocks_google(blocks, "zh-TW")
    assert len(out) == 1
    # Inline tag and its href survive; text is translated.
    assert '<a href="http://x">' in out[0]
    assert "T[here]" in out[0]


def test_translate_blocks_batches_and_respects_char_budget():
    # Blocks sized so a few fit per batch: total far exceeds the budget, forcing
    # multiple batches, but far fewer than one request per block.
    block_size = translation.GOOGLE_BATCH_CHAR_BUDGET // 4
    blocks = ["あ" * block_size for _ in range(10)]
    calls = []

    def recording(text, target_language="zh-TW", *a, **k):
        calls.append(text)
        return _bracket_translate(text, target_language)

    with patch.object(translation, "_translate_google", side_effect=recording):
        out = translation._translate_blocks_google(blocks, "zh-TW")

    # Batching happened: more than one request, but fewer than one-per-block.
    assert 2 <= len(calls) < len(blocks)
    # No batch payload exceeds the budget (no single block does here).
    assert all(len(payload) <= translation.GOOGLE_BATCH_CHAR_BUDGET for payload in calls)
    # Still a correct 1:1 translation for every block, in order.
    assert out == [f"T[{b}]" for b in blocks]


def test_translate_blocks_falls_back_to_per_block_on_mangled_batch():
    blocks = ["x", "y"]
    calls = []

    def flaky(text, target_language="zh-TW", *a, **k):
        calls.append(text)
        if "data-i" in text:
            # Simulate a batch the translator mangled (markers lost).
            return "<p>mangled</p>"
        # Per-block fallback path receives raw block content.
        return _bracket_translate(text, target_language)

    with patch.object(translation, "_translate_google", side_effect=flaky):
        out = translation._translate_blocks_google(blocks, "zh-TW")

    # Output is still correct thanks to the per-block fallback.
    assert out == ["T[x]", "T[y]"]
    # A batch attempt happened, then each block was translated individually.
    assert any("data-i" in c for c in calls)
    assert "x" in calls and "y" in calls


def test_translate_blocks_rejects_batch_with_leaked_markers():
    # Google sometimes merges adjacent blocks and leaks the data-i markers into
    # visible text while keeping the div count. Such a batch is corrupt and must
    # be discarded in favor of per-block translation.
    blocks = ["a", "b"]

    def leaky(text, target_language="zh-TW", *a, **k):
        if "data-i" in text:
            # Correct count (2 divs) but a marker leaked into block 0's content.
            return '<div data-i="0">T[a] data-i="1"</div><div data-i="1">T[b]</div>'
        return _bracket_translate(text, target_language)

    with patch.object(translation, "_translate_google", side_effect=leaky):
        out = translation._translate_blocks_google(blocks, "zh-TW")

    assert out == ["T[a]", "T[b]"]


def test_translate_html_batches_blocks_into_fewer_requests():
    html = "".join(f"<p>Para {i}</p>" for i in range(6))
    calls = []

    def recording(text, target_language="zh-TW", *a, **k):
        calls.append(text)
        return _bracket_translate(text, target_language)

    with patch.object(translation, "_translate_google", side_effect=recording):
        _, provider = translation.translate_html(html, translator="google")

    # Six short blocks fit one batch => a single request, not six.
    assert len(calls) == 1
    assert provider == "Google Translate"


def test_translate_html_preserves_bilingual_interleave_structure():
    html = "<p>Hello</p><p>World</p>"
    with patch.object(translation, "_translate_google", side_effect=_bracket_translate):
        result, _ = translation.translate_html(html, translator="google")

    # Original wrapped in blockquote, translation follows, order preserved.
    assert "<blockquote><p>Hello</p></blockquote>" in result
    assert "<p>T[Hello]</p>" in result
    assert "<blockquote><p>World</p></blockquote>" in result
    assert "<p>T[World]</p>" in result
    assert result.index("Hello") < result.index("World")


def test_translate_html_empty_returns_none():
    assert translation.translate_html("", translator="google") == ("", "none")
