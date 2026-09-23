"""A failed translation must leave the article untouched, not damage it.

Between ~2026-09-14 and the Qwen-MT cutover the dead Google endpoint wrote a literal
"(Translation Error)" into the title and body of 1,524 articles. The reader builds its
URLs from the title, so the marker reached the slugs too
(…/7557298-part-120-translation-error) and nothing re-translates an existing article,
so every one of those rows stayed damaged until a manual backfill.

Any provider can fail. These tests pin the rule that makes that survivable: on failure
the caller gets the original text back with provider "none", and nothing writes a
marker into content a reader will see.
"""
from unittest.mock import patch
import pytest

from app.services import translation

MARKER = "Translation Error"


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Override the conftest autouse fixture — these are pure unit tests."""
    yield


def _boom(*args, **kwargs):
    raise translation.QwenError("provider down")


@pytest.fixture(autouse=True)
def lmt_offline(monkeypatch):
    """These tests are about every provider being down, local model included.

    Without this they would reach the real LMT server on the Pi, which both slows the
    suite and makes it depend on that box being up.
    """
    def _down(*args, **kwargs):
        raise translation.LMTError("local model offline")
    monkeypatch.setattr(translation, "_lmt_call", _down)
    yield


def test_translate_text_returns_the_original_title_unchanged():
    with patch.object(translation, "_qwen_block", side_effect=_boom):
        out, provider = translation.translate_text("週刊アスキー No.1614", "zh-TW")

    assert out == "週刊アスキー No.1614"
    assert MARKER not in out
    assert provider == "none"


def test_translate_html_returns_the_original_body_unchanged():
    html = "<p>本文です</p>"
    with patch.object(translation, "_translate_blocks_qwen", side_effect=_boom):
        out, provider = translation.translate_html(html, "zh-TW")

    assert out == html
    assert MARKER not in out
    assert provider == "none"


def test_translate_html_adds_no_error_banner():
    """The old failure path prepended a red banner div into the stored body."""
    html = "<p>本文です</p>"
    with patch.object(translation, "_translate_blocks_qwen", side_effect=_boom):
        out, _ = translation.translate_html(html, "zh-TW")

    assert "#c62828" not in out
    assert "<div" not in out


def test_article_translation_returns_the_original_when_every_provider_fails():
    title, content = "週刊アスキー No.1614", "<p>本文です</p>"
    with patch.object(translation, "_translate_deepseek_article", side_effect=RuntimeError("down")), \
         patch.object(translation, "_qwen_block", side_effect=_boom), \
         patch.object(translation, "_translate_blocks_qwen", side_effect=_boom):
        out_title, out_content, provider, usage = translation.translate_article_deepseek(
            title, content, "zh-TW")

    assert out_title == title
    assert out_content == content
    assert MARKER not in out_title
    assert MARKER not in out_content
    assert provider == "none"


def test_qwen_article_translation_returns_the_original_on_failure():
    title, content = "週刊アスキー No.1614", "<p>本文です</p>"
    with patch.object(translation, "_qwen_block", side_effect=_boom), \
         patch.object(translation, "_translate_blocks_qwen", side_effect=_boom):
        out_title, out_content, provider, _ = translation.translate_article_qwen(
            title, content, "zh-TW")

    assert out_title == title
    assert out_content == content
    assert provider == "none"


def test_google_failure_also_leaves_the_original_intact():
    with patch.object(translation, "_google_call",
                      side_effect=translation.GoogleError("429 /sorry/")):
        out, provider = translation.translate_text("見出し", "zh-TW", translator="google")
        body, body_provider = translation.translate_html(
            "<p>本文です</p>", "zh-TW", translator="google")

    assert out == "見出し"
    assert provider == "none"
    assert body == "<p>本文です</p>"
    assert body_provider == "none"


def test_a_partial_failure_still_translates_what_it_can():
    """One dead block must not discard the rest of a working article."""
    html = "<p>一</p><p>二</p>"

    def one_batch(blocks, target_language):
        yield list(range(len(blocks))), ["譯一", "譯二"]

    with patch.object(translation, "_translate_blocks_qwen_iter", side_effect=one_batch):
        out, provider = translation.translate_html(html, "zh-TW")

    assert "譯一" in out and "譯二" in out
    assert provider == translation.QWEN_PROVIDER_LABEL
