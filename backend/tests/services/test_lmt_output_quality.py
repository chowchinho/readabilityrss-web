"""Cleaning up what the local model gives back.

LMT-60-1.7B is a plain-text sentence translator with no HTML awareness and no system
prompt, so two defects reach the reader unless they are handled here.

Markup: measured against the live server, `<a href="...">` survives a round trip
intact, `<strong>` is silently dropped, and `<span id="...">` is echoed into the
visible text as `< span id="jin_huawo">`. Three articles on 2026-09-23 carried that
leak. So everything except a link is unwrapped before the block is sent.

Script: the model mixes Traditional and Simplified inside one block — 来临 beside
抵達. The existing guards only fire on text that is overwhelmingly Simplified
(`looks_simplified` needs four Simplified-only characters, `force_traditional`
no-ops below a 0.15 ratio), and the real cases measure 0.005-0.13, so they slip
through both. Conversion is safe at any ratio here: s2t left all 140 blocks of
known-good output byte-identical.
"""
import pytest

from app.services import translation
from app.utils import hk_glossary


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


# --- markup sanitising ------------------------------------------------------


def test_a_span_with_an_id_is_unwrapped():
    """The id leaked into the visible text as `< span id="...">`."""
    out = translation._lmt_sanitize('<span id="jin_huawo">進化を遂げる美術館</span>')
    assert out == "進化を遂げる美術館"
    assert "span" not in out
    assert "jin_huawo" not in out


def test_a_link_keeps_its_href():
    """Measured: the model round-trips <a href> correctly, so it is worth keeping."""
    out = translation._lmt_sanitize('詳しくは<a href="https://example.com/x">こちら</a>をご覧ください。')
    assert '<a href="https://example.com/x">' in out
    assert "こちら" in out


def test_a_link_loses_everything_except_href():
    out = translation._lmt_sanitize('<a href="/x" class="btn" id="cta" target="_blank">こちら</a>')
    assert 'href="/x"' in out
    assert "btn" not in out and "cta" not in out and "_blank" not in out


def test_other_inline_tags_are_unwrapped_keeping_their_text():
    out = translation._lmt_sanitize("これは<strong>重要</strong>です。")
    assert out == "これは重要です。"


def test_plain_text_is_untouched():
    assert translation._lmt_sanitize("ルート概要") == "ルート概要"


def test_empty_input_is_safe():
    assert translation._lmt_sanitize("") == ""
    assert translation._lmt_sanitize(None) == ""


# --- script normalisation ---------------------------------------------------


SIMPLIFIED_BLOCK = "最近，我尽量不带衣服去住的地方，因为我不想增加行李。"
MIXED_BLOCK = "2023 年 3 月，他穿越了 Drake Passage，在正式的冬季来临之前抵達南極"
GOOD_BLOCK = "博物館和美術館成為智慧人士的約會場所"


def test_the_existing_gate_still_protects_other_providers():
    """Qwen and DeepSeek keep the 0.15 floor; only the local path lowers it."""
    assert hk_glossary.force_traditional(SIMPLIFIED_BLOCK) == SIMPLIFIED_BLOCK


def test_a_lowered_floor_converts_a_simplified_block():
    """Assert no Simplified survives, not which variant OpenCC picks.

    s2t renders 尽量 as 儘量 (the Taiwan standard) rather than the 盡量 more usual in
    Hong Kong. That is a house-form question for the glossary, not a conversion bug.
    """
    out = hk_glossary.force_traditional(SIMPLIFIED_BLOCK, min_ratio=0.0)
    assert hk_glossary.simplified_ratio(out) == 0
    assert "不带" not in out and "不帶" in out
    assert "因为" not in out and "因為" in out


def test_a_lowered_floor_converts_the_simplified_half_of_a_mixed_block():
    out = hk_glossary.force_traditional(MIXED_BLOCK, min_ratio=0.0)
    assert "来临" not in out
    assert "來臨" in out
    assert "抵達南極" in out, "the Traditional half must survive untouched"


def test_conversion_leaves_correct_traditional_alone():
    """s2t was byte-identical on all 140 blocks of known-good output."""
    assert hk_glossary.force_traditional(GOOD_BLOCK, min_ratio=0.0) == GOOD_BLOCK


def test_japanese_names_are_not_converted():
    """A Han character welded to kana belongs to a name; s2t would corrupt it.

    The guard extends a few characters past the kana, so Simplified sitting directly
    after a Japanese particle survives too. That is the price of not corrupting 里 in
    花里みのり, and it is the right trade.
    """
    text = "花里みのりと上田優紀の对话です。这是简体。"
    out = hk_glossary.force_traditional(text, min_ratio=0.0)
    assert "花里みのり" in out
    assert "这是简体" not in out, "Simplified clear of the kana run must convert"


def test_the_local_path_normalises_script_end_to_end():
    from unittest.mock import patch
    with patch.object(translation, "_lmt_call", return_value=SIMPLIFIED_BLOCK):
        out = translation._translate_blocks_lmt(["原文"], "zh-TW")
    assert hk_glossary.simplified_ratio(out[0]) == 0
    assert "不帶" in out[0]


def test_a_simplified_target_is_left_simplified():
    from unittest.mock import patch
    with patch.object(translation, "_lmt_call", return_value=SIMPLIFIED_BLOCK):
        out = translation._translate_blocks_lmt(["原文"], "zh-CN")
    assert out[0] == SIMPLIFIED_BLOCK


# --- leaked link tags -------------------------------------------------------

# An observed leak. The source block's link spans two lines, so the closer went
# out on the second request and the model dropped it; the opener came back as text.
OBSERVED_SOURCE_BLOCK = (
    '<a href="/magazine/article/100/"><strong>「山の会」</strong><br/>\n'
    '<strong>入会・詳細はこちら</strong></a><br/>\n'
    '※入会月は無料お試し期間です。会費は翌月1日から発生します。'
)
OBSERVED_LEAKED_OUTPUT = (
    '< a href = " /magazine/article/100/ " > 「 山の会 」 詳情請見 '
    '※入會月為免費試用期，下個月1日起收取會費'
)


def test_the_observed_leak_is_removed_from_visible_text():
    out = translation._lmt_repair_links(OBSERVED_LEAKED_OUTPUT)
    assert out == ' 「 山の会 」 詳情請見 ※入會月為免費試用期，下個月1日起收取會費'


@pytest.mark.parametrize("leaked", [
    '&lt; a href = " /magazine/article/100/ " &gt; 「 山の会 」 詳情請見',
    '&lt; a href = “ /magazine/article/100/ ” &gt; 「 山の会 」 詳情請見',
    '&lt; a href= " https://www.example.com/magazine/article/200/"&gt; 「 山の会 」 詳情請見',
])
def test_escaped_and_curly_quoted_forms_are_removed(leaked):
    out = translation._lmt_repair_links(leaked)
    assert "href" not in out and "&lt;" not in out and "&gt;" not in out
    assert out.strip() == "「 山の会 」 詳情請見"


def test_a_paired_leak_is_rebuilt_as_a_link():
    out = translation._lmt_repair_links('詳情請見 < a href = " /x/ " > 這裡 < / a > 。')
    assert out == '詳情請見 <a href="/x/"> 這裡 </a> 。'


def test_an_escaped_closer_pairs_with_an_escaped_opener():
    out = translation._lmt_repair_links('&lt; a href = “ /x/ ” &gt;這裡&lt; / a &gt;')
    assert out == '<a href="/x/">這裡</a>'


def test_a_stray_closer_is_dropped():
    assert translation._lmt_repair_links("詳情請見 < / a > 。") == "詳情請見  。"


def test_an_intact_link_is_left_as_a_link():
    out = translation._lmt_repair_links('詳情請見<a href="https://example.com/x">這裡</a>。')
    assert out == '詳情請見<a href="https://example.com/x">這裡</a>。'


def test_text_without_markup_is_untouched():
    assert translation._lmt_repair_links("A < B 而且 C > D") == "A < B 而且 C > D"


def test_the_observed_block_end_to_end():
    """Through the real sanitise -> per-line call -> interleave path."""
    from unittest.mock import patch

    from bs4 import BeautifulSoup

    replies = iter([
        '< a href = " /magazine/article/100/ " > 「 山の会 」',
        "詳情請見",
        "※入會月為免費試用期，下個月1日起收取會費",
    ])
    with patch.object(translation, "_lmt_call", side_effect=lambda *a, **k: next(replies)):
        translated = translation._translate_blocks_lmt([OBSERVED_SOURCE_BLOCK], "zh-TW")[0]

    soup = BeautifulSoup(f"<p>{OBSERVED_SOURCE_BLOCK}</p>", "html.parser")
    translation._interleave_translation(soup, soup.find("p"), translated)
    translated_p = soup.find("blockquote").find_next_sibling("p")

    assert "href" not in translated_p.get_text()
    assert "山の会" in translated_p.get_text()
    assert "詳情請見" in translated_p.get_text()
    assert soup.find("blockquote").find("a")["href"] == "/magazine/article/100/"


def test_a_rebuilt_link_gets_the_original_href_back():
    """The model's copy of the URL is not trusted; the original's is restored."""
    from unittest.mock import patch

    from bs4 import BeautifulSoup

    source = '詳しくは<a href="https://www.example.com/magazine/article/200/">前編</a>へ'
    reply = '詳情見 < a href = " https：//www.example.com/magazine/article/200/ " > 前篇 < / a >'
    with patch.object(translation, "_lmt_call", return_value=reply):
        translated = translation._translate_blocks_lmt([source], "zh-TW")[0]

    soup = BeautifulSoup(f"<p>{source}</p>", "html.parser")
    translation._interleave_translation(soup, soup.find("p"), translated)
    link = soup.find("blockquote").find_next_sibling("p").find("a")
    assert link["href"] == "https://www.example.com/magazine/article/200/"
    assert link.get_text(strip=True) == "前篇"
