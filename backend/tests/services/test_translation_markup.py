"""Translation must never alter markup — only human-readable text.

Google's free endpoint is a plain-text translator: it localises punctuation
inside attribute values (srcset commas become full-width, quoted URLs pick up
CJK brackets), which breaks image src. These tests pin the repair.
"""

from bs4 import BeautifulSoup

from app.services import translation as tr


def _src(html, index=0):
    return BeautifulSoup(html, "html.parser").find_all("img")[index].get("src")


def _srcset(html, index=0):
    return BeautifulSoup(html, "html.parser").find_all("img")[index].get("srcset")


def test_media_is_never_sent_to_the_translator():
    block = '<p>山です<img src="https://x.com/a.jpg" srcset="https://x.com/a.jpg 2x">の写真</p>'
    stripped = tr._strip_media_for_translation(block)
    assert "<img" not in stripped
    assert "https://x.com" not in stripped
    assert "山です" in stripped and "の写真" in stripped


def test_strip_media_removes_embeds_but_keeps_prose():
    block = (
        "<p>前文"
        '<picture><source srcset="https://x.com/a.webp"><img src="https://x.com/a.jpg"></picture>'
        '<iframe src="https://youtube.com/embed/1"></iframe>'
        "<b>強調</b>後文</p>"
    )
    stripped = tr._strip_media_for_translation(block)
    for tag in ("<img", "<picture", "<source", "<iframe"):
        assert tag not in stripped
    assert "<b>強調</b>" in stripped
    assert "前文" in stripped and "後文" in stripped


def test_strip_media_is_a_noop_for_plain_text():
    assert tr._strip_media_for_translation("ただのテキスト") == "ただのテキスト"
    assert tr._strip_media_for_translation("") == ""


def test_translated_block_does_not_duplicate_the_image():
    """The image must appear once — in the original block, not twice."""
    html = '<div><p>写真です<img src="https://x.com/a.jpg"></p></div>'
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find("p")
    # What Google returns for the stripped block: prose only, no media.
    tr._interleave_translation(soup, el, "這是照片")

    imgs = soup.find_all("img")
    assert len(imgs) == 1
    assert imgs[0]["src"] == "https://x.com/a.jpg"
    assert imgs[0].find_parent("blockquote") is not None
    assert "這是照片" in soup.get_text()


def test_no_image_is_lost_or_duplicated_by_translation(monkeypatch):
    """Every image survives translation exactly once, with its URL intact.

    Images must still reach the reader and the image cache — they are only kept
    out of the translation request itself.
    """
    sent_to_translator = []

    def fake_batch(blocks, target_language):
        sent_to_translator.extend(blocks)
        return [f"[zh]{BeautifulSoup(b, 'html.parser').get_text()}" for b in blocks]

    monkeypatch.setattr(tr, "_translate_blocks_google", fake_batch)

    html = (
        '<div><p>導入文<img src="https://x.com/in-paragraph.jpg"></p>'
        '<figure><img src="https://x.com/standalone.jpg"></figure>'
        '<p>本文です</p>'
        '<div><img src="https://x.com/bare-div.jpg"></div></div>'
    )
    original_srcs = sorted(i["src"] for i in BeautifulSoup(html, "html.parser").find_all("img"))

    out, provider = tr.translate_html(html, "zh-TW", translator="google")
    out_srcs = sorted(i["src"] for i in BeautifulSoup(out, "html.parser").find_all("img"))

    assert provider == "Google Translate"
    assert out_srcs == original_srcs, "images must survive exactly once, unchanged"
    # The translator never saw an image URL.
    assert not any("x.com" in block or "<img" in block for block in sent_to_translator)
    assert "[zh]導入文" in out and "[zh]本文です" in out


def test_images_remain_cacheable_after_translation(monkeypatch):
    """Remote URLs must still be present for cache_article_images to rewrite."""
    monkeypatch.setattr(
        tr, "_translate_blocks_google", lambda blocks, target_language: ["譯文"] * len(blocks)
    )
    html = '<div><p>文<img src="https://cdn.example.com/a.jpg"></p></div>'
    out, _ = tr.translate_html(html, "zh-TW", translator="google")
    img = BeautifulSoup(out, "html.parser").find("img")
    assert img is not None
    assert img["src"] == "https://cdn.example.com/a.jpg"
    assert img["src"].startswith("http"), "must stay remote so the cache step can fetch it"


def test_restores_srcset_localised_by_translator():
    original = (
        '<img src="https://x.com/a.jpg" srcset="https://x.com/a-300.jpg 300w, https://x.com/a-1024.jpg 1024w">'
    )
    # Google returns a full-width comma and drops the last descriptor.
    translated = (
        '<img src="https://x.com/a.jpg" srcset="https://x.com/a-300.jpg 300w，https://x.com/a-1024.jpg">'
    )
    fixed = tr._restore_markup_attributes(original, translated)
    assert _srcset(fixed) == "https://x.com/a-300.jpg 300w, https://x.com/a-1024.jpg 1024w"


def test_restores_src_wrapped_in_cjk_quotes():
    original = '<img src="https://cdn.example.com/photo.jpg" title="" width="1200"/>'
    translated = '<img src="「https://cdn.example.com/photo.jpg」標題=「」寬度=「1200」/"/>'
    fixed = tr._restore_markup_attributes(original, translated)
    assert _src(fixed) == "https://cdn.example.com/photo.jpg"


def test_restores_src_with_repeated_path_segments():
    original = '<img src="https://cdn.example.com/images/20260622/20260622195.jpg">'
    translated = '<img src="https://cdn.example.com/images/20260622202602220260622/20260622195.jpg">'
    fixed = tr._restore_markup_attributes(original, translated)
    assert _src(fixed) == "https://cdn.example.com/images/20260622/20260622195.jpg"


def test_keeps_translated_alt_and_title():
    """alt/title are human-readable — translating them is correct, don't revert."""
    original = '<p>山です<img src="https://x.com/a.jpg" alt="山の写真"></p>'
    translated = '<p>這是山<img src="https://x.com/a.jpg" alt="山的照片"></p>'
    fixed = tr._restore_markup_attributes(original, translated)
    soup = BeautifulSoup(fixed, "html.parser")
    assert soup.find("img").get("alt") == "山的照片"
    assert "這是山" in soup.get_text()


def test_restores_href():
    original = '<p>詳しくは<a href="https://example.com/a/b/page.html">こちら</a></p>'
    translated = '<p>詳情<a href="https://example.com/a/b/page.html 」">此處</a></p>'
    fixed = tr._restore_markup_attributes(original, translated)
    assert BeautifulSoup(fixed, "html.parser").find("a")["href"] == "https://example.com/a/b/page.html"


def test_repairs_media_positionally_when_structure_diverges():
    """Translator dropped an inline <b>, so tag sequences differ — img must still be fixed."""
    original = '<p><b>強調</b>と<img src="https://x.com/a.jpg"></p>'
    translated = '<p>強調と<img src="https://x.com/a.jpg 」"></p>'
    fixed = tr._restore_markup_attributes(original, translated)
    assert _src(fixed) == "https://x.com/a.jpg"


def test_repairs_by_url_prefix_when_translator_drops_an_image():
    """Real pattern: <img><noscript><img></noscript> collapses to one <img>.

    Counts no longer line up, but the surviving src still shares a long prefix
    with its original, which is enough to identify it.
    """
    original = (
        '<img src="https://funq.jp/uploads/2026/06/175740f492c75888724c53394502de.jpg">'
        "<noscript><img src=\"https://funq.jp/uploads/2026/06/other-image-entirely.jpg\"></noscript>"
    )
    translated = '<img src="https://funq.jp/uploads/2026/06/175740f492c75888724c53394502de.jp，">'
    fixed = tr._restore_markup_attributes(original, translated)
    assert _src(fixed) == "https://funq.jp/uploads/2026/06/175740f492c75888724c53394502de.jpg"


def test_prefix_repair_refuses_ambiguous_match():
    """Two originals sharing the same long prefix must not be guessed between."""
    original = (
        '<img src="https://cdn.example.com/very/long/shared/path/aaaa.jpg">'
        '<img src="https://cdn.example.com/very/long/shared/path/bbbb.jpg">'
        '<img src="https://cdn.example.com/very/long/shared/path/cccc.jpg">'
    )
    translated = '<img src="https://cdn.example.com/very/long/shared/path/「">'
    fixed = tr._restore_markup_attributes(original, translated)
    # Left as-is rather than silently attaching the wrong image.
    assert _src(fixed) == "https://cdn.example.com/very/long/shared/path/「"


def test_prefix_repair_ignores_unrelated_urls():
    original = '<img src="https://a.example.com/one.jpg"><img src="https://b.example.com/two.jpg">'
    translated = '<img src="https://totally-different.net/x「">'
    fixed = tr._restore_markup_attributes(original, translated)
    assert _src(fixed) == "https://totally-different.net/x「"


def test_extra_image_invented_by_translator_is_left_alone():
    original = '<p><img src="https://x.com/a.jpg"></p>'
    translated = '<p><img src="https://x.com/a.jpg"><img src="https://x.com/b.jpg"></p>'
    fixed = tr._restore_markup_attributes(original, translated)
    assert len(BeautifulSoup(fixed, "html.parser").find_all("img")) == 2


def test_drops_attribute_the_translator_invented():
    original = '<img src="https://x.com/a.jpg">'
    translated = '<img src="https://x.com/a.jpg" srcset="https://x.com/hallucinated.jpg">'
    fixed = tr._restore_markup_attributes(original, translated)
    assert _srcset(fixed) is None


def test_handles_empty_and_plain_text():
    assert tr._restore_markup_attributes("", "") == ""
    assert tr._restore_markup_attributes("<p>あ</p>", "<p>啊</p>") == "<p>啊</p>"


def test_interleave_translation_repairs_image_src():
    """End-to-end through the real stitching helper used by every Google/DeepL path."""
    html = '<div><p>写真です<img src="https://x.com/a.jpg" srcset="https://x.com/a.jpg 2x"></p></div>'
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find("p")
    corrupted = '這是照片<img src="https://x.com/a.jpg 」" srcset="https://x.com/a.jpg 2x，">'

    tr._interleave_translation(soup, el, corrupted)

    imgs = soup.find_all("img")
    assert len(imgs) == 2  # original in <blockquote> + translated copy
    assert all(i["src"] == "https://x.com/a.jpg" for i in imgs)
    assert all(i.get("srcset") == "https://x.com/a.jpg 2x" for i in imgs)
    assert "這是照片" in soup.get_text()
