"""Tests for batched Qwen-MT translation of HTML block content.

Qwen-MT accepts no system prompt and caps input at 8k tokens, so blocks go out as
marker-wrapped batches ("[0] ...\\n[1] ...") rather than one request per block. These
tests pin the batched behaviour: fewer requests, identical 1:1 output, a per-block
fallback when markers do not round-trip, and the split-and-resend recovery for the
~0.9% of responses that come back in Simplified Chinese.
"""
import re
from unittest.mock import patch
import pytest

from app.services import translation


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Override the conftest autouse fixture.

    These are pure unit tests for the translation service; they must not pull in
    app.main (the full FastAPI app and its heavier dependencies).
    """
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


def test_translate_blocks_returns_one_translation_per_block_in_order():
    blocks = ["Hello", "World", "Third"]
    with patch.object(translation, "_qwen_call", side_effect=_bracket):
        out = translation._translate_blocks_qwen(blocks, "zh-TW")
    assert out == ["T[Hello]", "T[World]", "T[Third]"]


def test_translate_blocks_preserves_inline_tags():
    blocks = ['Go <a href="http://x">here</a> now']
    with patch.object(translation, "_qwen_call", side_effect=_bracket):
        out = translation._translate_blocks_qwen(blocks, "zh-TW")
    assert len(out) == 1
    assert '<a href="http://x">' in out[0]
    assert "here" in out[0]


def test_translate_blocks_batches_and_respects_char_budget():
    block_size = translation.QWEN_BATCH_CHAR_BUDGET // 4
    blocks = ["あ" * block_size for _ in range(10)]
    calls = []

    def recording(text, target_language="zh-TW", *a, **k):
        calls.append(text)
        return _bracket(text, target_language)

    with patch.object(translation, "_qwen_call", side_effect=recording):
        out = translation._translate_blocks_qwen(blocks, "zh-TW")

    # Batching happened: more than one request, but fewer than one per block.
    assert 2 <= len(calls) < len(blocks)
    assert out == [f"T[{b}]" for b in blocks]


def test_translate_blocks_falls_back_to_per_block_on_mangled_batch():
    blocks = ["x", "y"]
    calls = []

    def flaky(text, target_language="zh-TW", *a, **k):
        calls.append(text)
        if text.startswith("[0]"):
            return "the model ignored the markers entirely", {}
        return f"T[{text}]", {}

    with patch.object(translation, "_qwen_call", side_effect=flaky):
        out = translation._translate_blocks_qwen(blocks, "zh-TW")

    assert out == ["T[x]", "T[y]"]
    assert any(c.startswith("[0]") for c in calls)
    assert "x" in calls and "y" in calls


def test_translate_blocks_rejects_batch_with_duplicate_markers():
    blocks = ["a", "b"]

    def duplicated(text, target_language="zh-TW", *a, **k):
        if text.startswith("[0]"):
            return "[0] T[a]\n[0] T[b]", {}
        return f"T[{text}]", {}

    with patch.object(translation, "_qwen_call", side_effect=duplicated):
        out = translation._translate_blocks_qwen(blocks, "zh-TW")

    assert out == ["T[a]", "T[b]"]


def test_simplified_batch_is_split_and_resent():
    """A Simplified response poisons the whole batch, so it is re-sent per block.

    Measured on 5,983 blocks: re-sending the same batch reproduces the Simplified
    output every time, while splitting it recovers every block.
    """
    blocks = ["一", "二"]
    calls = []

    def flips(text, target_language="zh-TW", *a, **k):
        calls.append(text)
        if text.startswith("[0]"):
            return "[0] 这个软件的质量\n[1] 网络连线", {}
        return "這個軟件的質素", {}

    with patch.object(translation, "_qwen_call", side_effect=flips):
        out = translation._translate_blocks_qwen(blocks, "zh-TW")

    assert len(calls) == 3  # one batch, then one call per block
    assert all(not translation.hk_glossary.looks_simplified(o) for o in out)


def test_translate_html_batches_blocks_into_fewer_requests():
    html = "".join(f"<p>Para {i}</p>" for i in range(6))
    calls = []

    def recording(text, target_language="zh-TW", *a, **k):
        calls.append(text)
        return _bracket(text, target_language)

    with patch.object(translation, "_qwen_call", side_effect=recording):
        _, provider = translation.translate_html(html, translator="qwen")

    assert len(calls) == 1
    assert provider == translation.QWEN_PROVIDER_LABEL


def test_translate_html_preserves_bilingual_interleave_structure():
    html = "<p>Hello</p><p>World</p>"
    with patch.object(translation, "_qwen_call", side_effect=_bracket):
        result, _ = translation.translate_html(html, translator="qwen")

    assert "<blockquote><p>Hello</p></blockquote>" in result
    assert "<p>T[Hello]</p>" in result
    assert "<blockquote><p>World</p></blockquote>" in result
    assert "<p>T[World]</p>" in result
    assert result.index("Hello") < result.index("World")


def test_translate_html_empty_returns_none():
    assert translation.translate_html("", translator="qwen") == ("", "none")


def test_hk_glossary_applied_to_traditional_output():
    blocks = ["x"]
    with patch.object(translation, "_qwen_call", side_effect=lambda *a, **k: ("[0] 連線到網路的智慧型手機", {})):
        out = translation._translate_blocks_qwen(blocks, "zh-TW")
    assert out == ["連接到網絡的智能手機"]


def test_hk_glossary_not_applied_to_other_targets():
    blocks = ["x"]
    with patch.object(translation, "_qwen_call", side_effect=lambda *a, **k: ("[0] the network wallet", {})):
        out = translation._translate_blocks_qwen(blocks, "en")
    assert out == ["the network wallet"]


def test_failed_translation_reports_no_calls():
    """A wholly failed article must not log a usage row.

    The tally is a dict of zeros when every call failed, and a non-empty dict is
    truthy, so the scheduler gates on usage['calls'] rather than on the dict.
    """
    def dead(*a, **k):
        raise translation.QwenError("no api key")

    with patch.object(translation, "_qwen_call", side_effect=dead):
        title, content, provider, usage = translation.translate_article_qwen(
            "題", "<p>本文</p>", "zh-TW")

    assert usage.get("calls") == 0
    assert not usage.get("prompt_tokens")


def test_successful_translation_reports_its_tokens():
    def ok(text, target_language="zh-TW", *a, **k):
        return f"[0] T[{text}]", {"prompt_tokens": 30, "completion_tokens": 12}

    with patch.object(translation, "_qwen_call", side_effect=ok):
        _t, _c, _p, usage = translation.translate_article_qwen("題", "<p>本文</p>", "zh-TW")

    assert usage["calls"] >= 1
    assert usage["prompt_tokens"] >= 30
