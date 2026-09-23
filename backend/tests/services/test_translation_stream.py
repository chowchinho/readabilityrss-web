"""The batch boundary Qwen already uses, exposed as a stream.

_translate_blocks_qwen sends one request per batch. These tests pin that the
iterator yields once per request, in order, and that draining it reproduces the
existing list function exactly.
"""
import re
from unittest.mock import patch

import pytest

from app.services import translation


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    yield


@pytest.fixture(autouse=True)
def qwen_configured(monkeypatch):
    monkeypatch.setattr(translation, "_qwen_settings", lambda: (True, "test-key"))
    yield


def _bracket(text, target_language="zh-TW", *args, **kwargs):
    """Fake translator: brackets each line's payload, preserving batch markers."""
    out = []
    for line in text.split("\n"):
        m = re.match(r"^\[(\d+)\]\s*(.*)$", line)
        out.append(f"[{m.group(1)}] T[{m.group(2)}]" if m else f"T[{line}]")
    return "\n".join(out), {}


def test_iterator_yields_every_block_once_in_order():
    blocks = ["Hello", "World", "Third"]
    with patch.object(translation, "_qwen_call", side_effect=_bracket):
        seen = []
        for indices, translations in translation._translate_blocks_qwen_iter(blocks, "zh-TW"):
            assert len(indices) == len(translations)
            seen.extend(zip(indices, translations))
    assert seen == [(0, "T[Hello]"), (1, "T[World]"), (2, "T[Third]")]


def test_iterator_yields_once_per_batch():
    """A block over the char budget forces its own request."""
    blocks = ["a" * 2000, "b" * 2000, "short"]
    with patch.object(translation, "_qwen_call", side_effect=_bracket) as call:
        chunks = list(translation._translate_blocks_qwen_iter(blocks, "zh-TW"))
    assert len(chunks) == call.call_count
    assert call.call_count >= 2


def test_list_function_matches_its_iterator_drained():
    blocks = ["One", "Two", "Three", "Four"]
    with patch.object(translation, "_qwen_call", side_effect=_bracket):
        listed = translation._translate_blocks_qwen(blocks, "zh-TW")
    with patch.object(translation, "_qwen_call", side_effect=_bracket):
        drained = [None] * len(blocks)
        for indices, translations in translation._translate_blocks_qwen_iter(blocks, "zh-TW"):
            for i, t in zip(indices, translations):
                drained[i] = t
    assert listed == drained


HTML = "<div><p>First para</p><p>Second para</p><p>Third para</p></div>"


def test_translate_html_iter_yields_a_block_then_a_result():
    with patch.object(translation, "_qwen_call", side_effect=_bracket):
        events = list(translation.translate_html_iter(HTML, "zh-TW", "qwen"))

    blocks = [e for e in events if e["type"] == "block"]
    assert [e["index"] for e in blocks] == [0, 1, 2]
    assert events[-1]["type"] == "result"
    assert events[-1]["total"] == 3
    assert events[-1]["provider"] == translation.QWEN_PROVIDER_LABEL


def test_each_streamed_block_carries_original_and_translation():
    with patch.object(translation, "_qwen_call", side_effect=_bracket):
        events = list(translation.translate_html_iter(HTML, "zh-TW", "qwen"))
    first = next(e for e in events if e["type"] == "block")
    assert "<blockquote>" in first["html"]
    assert "First para" in first["html"]
    assert "T[First para]" in first["html"]


def test_translate_html_matches_its_iterator_result():
    with patch.object(translation, "_qwen_call", side_effect=_bracket):
        listed_html, listed_provider = translation.translate_html(HTML, "zh-TW", "qwen")
    with patch.object(translation, "_qwen_call", side_effect=_bracket):
        final = list(translation.translate_html_iter(HTML, "zh-TW", "qwen"))[-1]
    assert final["html"] == listed_html
    assert final["provider"] == listed_provider


def test_untranslatable_article_reports_none_and_streams_nothing():
    """A provider that hands every block back unchanged is not a translation."""
    def echo(text, target_language="zh-TW", *args, **kwargs):
        return text, {}

    with patch.object(translation, "_qwen_call", side_effect=echo):
        events = list(translation.translate_html_iter(HTML, "zh-TW", "qwen"))
    assert [e for e in events if e["type"] == "block"] == []
    assert events[-1]["provider"] == "none"
    assert events[-1]["html"] == HTML
