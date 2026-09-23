"""Tests for the Google Translate provider, restored 2026-09-21 for second-tier feeds.

The provider calls translate.googleapis.com/translate_a/single — the endpoint the
googletrans package uses — not the translate.google.com/m page that has been
CAPTCHA-walled since ~2026-09-14. Two properties of that endpoint shape this code and
are pinned here: the text goes in the POST body (a GET carries it in the URL, which
414s above ~3k characters), and HTML survives the round trip, so blocks are batched
with <div data-i="N"> markers rather than the "[N] " line markers Qwen-MT needs.
"""
import json
from unittest.mock import patch, Mock
import pytest

from app.services import translation


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Override the conftest autouse fixture — these are pure unit tests."""
    yield


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    """Drop the inter-call spacing; pacing has its own test module."""
    monkeypatch.setattr(translation, "GOOGLE_MIN_INTERVAL_SECONDS", 0.0)
    translation._google_throttle_reset()
    yield
    translation._google_throttle_reset()


def _response(status=200, segments=None, body=None):
    r = Mock()
    r.status_code = status
    r.text = body if body is not None else ""
    r.json = Mock(return_value=[[[s, "", None, None, 3] for s in (segments or [])], None, "ja"])
    return r


def _echo_call(text, target_language="zh-TW", *args, **kwargs):
    """Fake _google_call: wraps each block's inner text, keeping the markers."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(text, "html.parser")
    divs = soup.find_all(attrs={"data-i": True})
    if not divs:
        return f"T[{text}]"
    for div in divs:
        inner = div.decode_contents()
        div.clear()
        # .string would escape the inline tags the real endpoint preserves.
        div.append(BeautifulSoup(f"T[{inner}]", "html.parser"))
    return str(soup)


# --- the endpoint contract -------------------------------------------------


def test_call_posts_the_text_in_the_body_not_the_url():
    """A GET puts q in the query string and 414s on a real batch; POST does not."""
    captured = {}

    def fake_post(url, params=None, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["data"] = data
        return _response(segments=["你好"])

    with patch.object(translation.requests, "post", side_effect=fake_post):
        out = translation._google_call("こんにちは", "zh-TW")

    assert out == "你好"
    assert captured["url"] == translation.GOOGLE_URL
    assert captured["data"] == {"q": "こんにちは"}
    assert "q" not in captured["params"]
    assert captured["params"]["tl"] == "zh-TW"


def test_call_uses_the_chrome_extension_client_id():
    """client=gtx is throttled to 429 after ~60 calls; dict-chrome-ex is not.

    Both return the same shape, so this one string is the difference between the
    provider working and the provider being walled.
    """
    captured = {}

    def fake_post(url, params=None, data=None, headers=None, timeout=None):
        captured.update(params)
        return _response(segments=["你好"])

    with patch.object(translation.requests, "post", side_effect=fake_post):
        translation._google_call("こんにちは", "zh-TW")

    assert captured["client"] == "dict-chrome-ex"


def test_call_joins_every_segment_of_the_response():
    """Long input comes back split across segments; dropping any truncates the text."""
    with patch.object(translation.requests, "post",
                      return_value=_response(segments=["第一段。", "第二段。", "第三段。"])):
        out = translation._google_call("...", "zh-TW")
    assert out == "第一段。第二段。第三段。"


def test_call_retries_on_http_500_then_succeeds():
    """Measured over 54 live calls, ~9% return HTTP 500; a retry clears them."""
    responses = [_response(status=500), _response(segments=["译文"])]
    with patch.object(translation.requests, "post", side_effect=responses), \
         patch.object(translation.time, "sleep"):
        out = translation._google_call("x", "zh-TW")
    assert out == "译文"


def test_call_raises_after_exhausting_retries():
    with patch.object(translation.requests, "post", return_value=_response(status=429)), \
         patch.object(translation.time, "sleep"):
        with pytest.raises(translation.GoogleError):
            translation._google_call("x", "zh-TW")


def test_call_raises_on_an_unparseable_body():
    r = Mock()
    r.status_code = 200
    r.text = "<!DOCTYPE html><html>sorry</html>"
    r.json = Mock(side_effect=json.JSONDecodeError("no", "", 0))
    with patch.object(translation.requests, "post", return_value=r), \
         patch.object(translation.time, "sleep"):
        with pytest.raises(translation.GoogleError):
            translation._google_call("x", "zh-TW")


# --- batching --------------------------------------------------------------


def test_translate_blocks_returns_one_translation_per_block_in_order():
    blocks = ["Hello", "World", "Third"]
    with patch.object(translation, "_google_call", side_effect=_echo_call):
        out = translation._translate_blocks_google(blocks, "zh-TW")
    assert out == ["T[Hello]", "T[World]", "T[Third]"]


def test_translate_blocks_batches_under_the_char_budget():
    block_size = translation.GOOGLE_BATCH_CHAR_BUDGET // 4
    blocks = ["あ" * block_size for _ in range(10)]
    calls = []

    def recording(text, target_language="zh-TW", *a, **k):
        calls.append(text)
        return _echo_call(text, target_language)

    with patch.object(translation, "_google_call", side_effect=recording):
        out = translation._translate_blocks_google(blocks, "zh-TW")

    assert 2 <= len(calls) < len(blocks)
    assert out == [f"T[{b}]" for b in blocks]


def test_translate_blocks_falls_back_to_per_block_when_markers_are_lost():
    blocks = ["x", "y"]
    calls = []

    def flaky(text, target_language="zh-TW", *a, **k):
        calls.append(text)
        if 'data-i="0"' in text and 'data-i="1"' in text:
            return "两块合并了"          # markers gone: the batch must be discarded
        return _echo_call(text, target_language)

    with patch.object(translation, "_google_call", side_effect=flaky):
        out = translation._translate_blocks_google(blocks, "zh-TW")

    assert out == ["T[x]", "T[y]"]
    assert len(calls) == 3               # one failed batch, then one call per block


def test_translate_blocks_keeps_the_original_when_a_block_fails_outright():
    def always_fails(text, target_language="zh-TW", *a, **k):
        raise translation.GoogleError("boom")

    with patch.object(translation, "_google_call", side_effect=always_fails):
        out = translation._translate_blocks_google(["keep me"], "zh-TW")

    assert out == ["keep me"]


def test_translate_blocks_preserves_inline_tags():
    blocks = ['Go <a href="http://x">here</a> now']
    with patch.object(translation, "_google_call", side_effect=_echo_call):
        out = translation._translate_blocks_google(blocks, "zh-TW")
    assert '<a href="http://x">' in out[0]


# --- Hong Kong localisation ------------------------------------------------


def test_simplified_response_is_converted_for_a_traditional_target():
    """The free endpoint drops to Simplified on short segments (measured: 路线概览)."""
    with patch.object(translation, "_google_call", side_effect=lambda *a, **k: "路线概览"):
        out = translation._translate_blocks_google(["ルート概要"], "zh-TW")
    assert "路线" not in out[0]
    assert "路線" in out[0]


def test_simplified_response_is_left_alone_for_a_simplified_target():
    with patch.object(translation, "_google_call", side_effect=lambda *a, **k: "路线概览"):
        out = translation._translate_blocks_google(["ルート概要"], "zh-CN")
    assert out[0] == "路线概览"


# --- selection -------------------------------------------------------------


def test_translate_text_routes_to_google_when_selected():
    with patch.object(translation, "_google_call", side_effect=lambda *a, **k: "標題"):
        out, provider = translation.translate_text("見出し", "zh-TW", translator="google")
    assert out == "標題"
    assert provider == translation.GOOGLE_PROVIDER_LABEL


def test_translate_html_routes_to_google_when_selected():
    html = "<p>こんにちは</p>"
    with patch.object(translation, "_google_call", side_effect=_echo_call):
        out, provider = translation.translate_html(html, "zh-TW", translator="google")
    assert provider == translation.GOOGLE_PROVIDER_LABEL
    assert "T[こんにちは]" in out
    assert "<blockquote>" in out          # the original is kept alongside


def test_qwen_is_still_the_default_translator():
    """Restoring Google must not change what an unspecified translator does."""
    def one_batch(blocks, target_language):
        yield list(range(len(blocks))), ["译"]

    with patch.object(translation, "_translate_blocks_qwen_iter", side_effect=one_batch) as qwen, \
         patch.object(translation, "_translate_blocks_google") as google:
        translation.translate_html("<p>x</p>", "zh-TW")
    assert qwen.called
    assert not google.called
