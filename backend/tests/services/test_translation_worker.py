"""The deferred worker that finishes what the inline path could not.

A body cannot be translated inside the scheduler's 240s per-article budget by the
local model — 8.5s per sentence puts a normal article at about 4.5 minutes — so when
a remote provider fails, the article is stored untranslated and flagged. This worker
picks those up later and translates them with LMT-60-1.7B, off the refresh path.

It is also what clears the 1,434 articles the dead Google endpoint damaged: same
shape of work, same badge.
"""
from unittest.mock import patch
import pytest

from app.services import translation
from app.services import translation_worker as W


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Override the conftest autouse fixture — these are pure unit tests."""
    yield


@pytest.fixture(autouse=True)
def lmt_configured(monkeypatch):
    """LMT_URL has no default, so tests that exercise the provider must set one.

    An unset URL is its own behaviour — the provider reports itself unavailable — and
    has its own test; everything else here assumes a configured server.
    """
    monkeypatch.setattr(translation, "LMT_URL", "http://localhost:8099/v1/chat/completions")
    yield


BANNER = ('<div style="color: #c62828; background-color: #ffebee; padding: 10px;">'
          '(Translation Error)</div>')


# --- recovering the source --------------------------------------------------


def test_the_damage_marker_is_stripped_from_a_title():
    assert W.clean_title("週刊アスキー No.1614 (Translation Error)") == "週刊アスキー No.1614"


def test_a_clean_title_is_untouched():
    assert W.clean_title("無印良品の新作") == "無印良品の新作"


def test_the_error_banner_is_stripped_from_a_body():
    assert W.clean_content(BANNER + "<p>本文です</p>") == "<p>本文です</p>"


def test_an_already_translated_body_yields_its_original_blocks():
    """A bilingual body stores <blockquote>original</blockquote> + translation.

    Re-translating the translation would compound the errors, so the worker reads
    the originals back out.
    """
    bilingual = ('<p style="color:#888">🌐 Translated by Google Translate</p>'
                 "<blockquote><p>日本語の本文</p></blockquote><p>中文譯文</p>")
    out = W.clean_content(bilingual)
    assert "日本語の本文" in out
    assert "中文譯文" not in out
    assert "Translated by" not in out


# --- the badge --------------------------------------------------------------


def test_the_badge_names_the_provider_that_failed():
    html = W.badge_html("LMT-60-1.7B (fell back from Google Translate — HTTP 429)")
    assert "LMT-60-1.7B (fell back from Google Translate — HTTP 429)" in html
    assert html.startswith("<p")
    assert "🌐" in html


# --- selection --------------------------------------------------------------


def test_a_pending_row_is_work():
    assert W.needs_work({"translation_pending": 1, "title": "x", "content": "<p>y</p>"})


def test_a_damaged_row_is_work_even_without_the_flag():
    """The 1,434 damaged rows predate the flag."""
    assert W.needs_work({"translation_pending": 0, "title": "x (Translation Error)",
                         "content": "<p>y</p>"})


def test_a_translated_row_is_not_work():
    assert not W.needs_work({"translation_pending": 0, "title": "標題",
                             "content": "<p>🌐 Translated by Qwen-MT-flash</p><p>譯</p>"})


def test_a_row_with_no_body_is_not_work():
    """Nothing to translate; the parse failed, not the translation."""
    assert not W.needs_work({"translation_pending": 1, "title": "x", "content": ""})


# --- translating one article ------------------------------------------------


def _fake_blocks(blocks, target_language, source_language="ja"):
    return ["譯[%s]" % b for b in blocks]


def test_translating_an_article_interleaves_and_badges_it():
    row = {"id": 7, "title": "見出し", "content": "<p>本文です</p>",
           "original_title": None, "translation_pending": 1,
           "translation_note": "Google Translate — HTTP 429", "detected_language": "ja"}

    with patch.object(W.translation, "_translate_blocks_lmt", side_effect=_fake_blocks), \
         patch.object(W.translation, "_lmt_block", return_value="標題"):
        result = W.translate_article(row, target_language="zh-TW")

    assert result["title"] == "標題"
    assert "譯[本文です]" in result["content"]
    assert "<blockquote>" in result["content"], "the original must be kept alongside"
    assert "fell back from Google Translate — HTTP 429" in result["content"]
    assert result["provider"].startswith("LMT-60-1.7B")


def test_an_article_with_no_recorded_reason_still_gets_a_plain_badge():
    row = {"id": 8, "title": "見出し", "content": "<p>本文です</p>", "original_title": None,
           "translation_pending": 1, "translation_note": None, "detected_language": "ja"}

    with patch.object(W.translation, "_translate_blocks_lmt", side_effect=_fake_blocks), \
         patch.object(W.translation, "_lmt_block", return_value="標題"):
        result = W.translate_article(row, target_language="zh-TW")

    assert result["provider"] == "LMT-60-1.7B"
    assert "fell back" not in result["content"]


def test_the_damage_marker_never_survives():
    row = {"id": 9, "title": "週刊アスキー (Translation Error)",
           "content": BANNER + "<p>本文です</p>", "original_title": None,
           "translation_pending": 0, "translation_note": None, "detected_language": "ja"}

    with patch.object(W.translation, "_translate_blocks_lmt", side_effect=_fake_blocks), \
         patch.object(W.translation, "_lmt_block", return_value="週刊ASCII"):
        result = W.translate_article(row, target_language="zh-TW")

    assert "Translation Error" not in result["title"]
    assert "Translation Error" not in result["content"]
    assert "c62828" not in result["content"]


def test_a_dead_local_model_raises_rather_than_returning_none():
    """An outage and an untranslatable article must not look the same to the caller.

    None means "this article will never translate, take it out of the queue". A dead
    server is not that: the article is fine and has to stay queued. Returning None
    here made the worker dequeue three good articles per pass while the model was down.
    """
    row = {"id": 10, "title": "見出し", "content": "<p>本文です</p>", "original_title": None,
           "translation_pending": 1, "translation_note": None, "detected_language": "ja"}

    with patch.object(W.translation, "_lmt_block",
                      side_effect=W.translation.LMTError("server down")):
        with pytest.raises(W.translation.LMTError):
            W.translate_article(row, target_language="zh-TW")


def test_untranslatable_blocks_do_not_produce_a_doubled_article():
    """If the model hands every block back unchanged, there is no translation."""
    row = {"id": 11, "title": "見出し", "content": "<p>本文です</p>", "original_title": None,
           "translation_pending": 1, "translation_note": None, "detected_language": "ja"}

    with patch.object(W.translation, "_translate_blocks_lmt",
                      side_effect=lambda b, *a, **k: list(b)), \
         patch.object(W.translation, "_lmt_block", return_value="見出し"):
        assert W.translate_article(row, target_language="zh-TW") is None
