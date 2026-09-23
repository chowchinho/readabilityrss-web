"""LMT-60-1.7B: the local model that backs up every remote provider.

It runs on the Pi through llama.cpp behind an OpenAI-compatible server, so it cannot
be rate limited, walled or billed — but it is slow: 8.5s for a single sentence
measured through the container. That is fine for a title and far too slow for a body
inside the scheduler's 240s per-article budget, which is why only titles fall back
inline and bodies are deferred.

Two details of the model shape these tests. Its prompt template is line-delimited, so
a newline inside the source ends the segment and the rest is silently dropped. And it
is a translation model with no system prompt, so the Hong Kong vocabulary pass is the
only lever on register.
"""
from unittest.mock import patch, Mock
import pytest

from app.services import translation


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


def _reply(text, status=200):
    r = Mock()
    r.status_code = status
    r.text = "" if status == 200 else "server error"
    r.json = Mock(return_value={
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 20},
    })
    return r


# --- the call ---------------------------------------------------------------


def test_call_posts_the_documented_prompt_template():
    """LMT takes 'Translate the following text from X into Y:' with the names, not codes."""
    captured = {}

    def fake_post(url, json=None, timeout=None, **kw):
        captured["url"] = url
        captured["body"] = json
        return _reply("譯文")

    with patch.object(translation.requests, "post", side_effect=fake_post):
        out = translation._lmt_call("こんにちは", "zh-TW", source_language="ja")

    assert out == "譯文"
    prompt = captured["body"]["messages"][0]["content"]
    assert prompt.startswith("Translate the following text from Japanese into Traditional Chinese:")
    assert "Japanese: こんにちは" in prompt
    assert captured["body"]["model"] == translation.LMT_MODEL


def test_cantonese_target_is_named_the_way_the_model_expects():
    """'Yue Chinese' produces real Cantonese; 'cht' returns Japanese back."""
    captured = {}

    with patch.object(translation.requests, "post",
                      side_effect=lambda url, json=None, **kw: captured.update(json) or _reply("係")):
        translation._lmt_call("x", "zh-HK-yue", source_language="ja")

    assert "Yue Chinese" in captured["messages"][0]["content"]


def test_call_raises_on_a_server_error():
    with patch.object(translation.requests, "post", return_value=_reply("", status=500)), \
         patch.object(translation.time, "sleep"):
        with pytest.raises(translation.LMTError):
            translation._lmt_call("x", "zh-TW")


# --- the line-delimited prompt ----------------------------------------------


def test_a_multi_line_block_is_translated_line_by_line():
    """One request for the whole block stops after the first line and loses the rest."""
    sent = []

    def record(url, json=None, **kw):
        sent.append(json["messages"][0]["content"])
        return _reply("譯")

    with patch.object(translation.requests, "post", side_effect=record):
        out = translation._lmt_block("一行目\n二行目\n三行目", "zh-TW")

    assert len(sent) == 3, "each line needs its own request"
    assert out == "譯\n譯\n譯"


def test_blank_lines_are_preserved_without_a_request():
    sent = []

    def record(url, json=None, **kw):
        sent.append(json)
        return _reply("譯")

    with patch.object(translation.requests, "post", side_effect=record):
        out = translation._lmt_block("一行目\n\n二行目", "zh-TW")

    assert len(sent) == 2
    assert out == "譯\n\n譯"


# --- register ---------------------------------------------------------------


def test_simplified_output_is_converted_for_a_traditional_target():
    with patch.object(translation.requests, "post", return_value=_reply("路线概览")):
        out = translation._translate_blocks_lmt(["ルート概要"], "zh-TW")
    assert "路线" not in out[0]


def test_blocks_come_back_one_for_one_in_order():
    outs = iter(["一", "二", "三"])
    with patch.object(translation.requests, "post",
                      side_effect=lambda *a, **k: _reply(next(outs))):
        out = translation._translate_blocks_lmt(["a", "b", "c"], "zh-TW")
    assert out == ["一", "二", "三"]


def test_a_failed_block_keeps_its_source():
    with patch.object(translation.requests, "post", return_value=_reply("", status=500)), \
         patch.object(translation.time, "sleep"):
        out = translation._translate_blocks_lmt(["keep me"], "zh-TW")
    assert out == ["keep me"]


# --- the fallback badge -----------------------------------------------------


def test_fallback_label_names_the_provider_and_the_reason():
    label = translation.lmt_fallback_label("Google Translate", "HTTP 429")
    assert label == "LMT-60-1.7B (fell back from Google Translate — HTTP 429)"


def test_fallback_label_without_a_reason():
    assert translation.lmt_fallback_label("Qwen-MT-flash", "") == \
        "LMT-60-1.7B (fell back from Qwen-MT-flash)"


def test_fallback_reason_is_trimmed_to_something_readable():
    """A provider's exception carries an HTML body; the badge must not."""
    long_reason = "HTTP 429: <!DOCTYPE html PUBLIC '-//W3C//DTD HTML 4.01 Transitional//EN'><html>" * 3
    label = translation.lmt_fallback_label("Google Translate", long_reason)
    assert len(label) < 120
    assert "<!DOCTYPE" not in label
    assert "HTTP 429" in label


# --- titles fall back inline ------------------------------------------------


def test_a_title_falls_back_to_lmt_when_the_primary_fails():
    with patch.object(translation, "_qwen_block", side_effect=translation.QwenError("down")), \
         patch.object(translation.requests, "post", return_value=_reply("標題")):
        out, provider = translation.translate_text("見出し", "zh-TW", translator="qwen")

    assert out == "標題"
    assert provider.startswith("LMT-60-1.7B (fell back from Qwen-MT-flash")


def test_a_title_that_both_providers_fail_is_returned_untouched():
    with patch.object(translation, "_qwen_block", side_effect=translation.QwenError("down")), \
         patch.object(translation, "_lmt_call", side_effect=translation.LMTError("offline")):
        out, provider = translation.translate_text("見出し", "zh-TW", translator="qwen")

    assert out == "見出し"
    assert provider == "none"
    assert "Translation Error" not in out


def test_the_primary_is_used_when_it_works():
    with patch.object(translation, "_qwen_block", return_value="標題"), \
         patch.object(translation, "_lmt_call") as lmt:
        out, provider = translation.translate_text("見出し", "zh-TW", translator="qwen")

    assert provider == translation.QWEN_PROVIDER_LABEL
    assert not lmt.called


def test_an_unset_lmt_url_reports_unavailable_rather_than_calling_anything():
    """No default address: the public build must not point at anyone's machine."""
    with patch.object(translation, "LMT_URL", ""), \
         patch.object(translation.requests, "post") as post:
        with pytest.raises(translation.LMTError) as e:
            translation._lmt_call("こんにちは", "zh-TW")

    assert not post.called, "nothing should be sent when the provider is unconfigured"
    assert "not set" in str(e.value).lower() or "not configured" in str(e.value).lower()


def test_an_unconfigured_local_model_leaves_a_title_untouched():
    """Every caller already handles the provider being unavailable."""
    with patch.object(translation, "LMT_URL", ""), \
         patch.object(translation, "_qwen_block", side_effect=translation.QwenError("down")):
        out, provider = translation.translate_text("見出し", "zh-TW", translator="qwen")

    assert out == "見出し"
    assert provider == "none"
