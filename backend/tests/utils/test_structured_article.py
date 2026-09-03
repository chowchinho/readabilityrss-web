import json

from bs4 import BeautifulSoup

from app.utils.structured_article import extract_structured_article

URL = "https://www.hk01.com/即時中國/60377162/some-slug"


def _page(article: dict) -> str:
    payload = {"props": {"initialProps": {"pageProps": {"article": article}}}}
    # Next.js escapes "<" as < so embedded markup cannot close the script tag.
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return (
        "<html><body><article>server rendered text, no img tags</article>"
        '<script id="__NEXT_DATA__" type="application/json">'
        + encoded
        + "</script></body></html>"
    )


def _article(blocks, main="https://cdn.hk01.com/di/media/images/dw/1/main.jpeg/x"):
    return {"title": "標題", "mainImage": {"cdnUrl": main}, "blocks": blocks}


def _img_block(url, caption=""):
    return {"blockType": "image", "htmlTokens": [], "image": {"cdnUrl": url, "caption": caption}}


def _text_block(paragraphs):
    return {"blockType": "text", "htmlTokens": [[{"type": t, "content": c}] for t, c in paragraphs]}


def test_rebuilds_body_with_images_in_original_order():
    blocks = [
        _img_block("https://cdn.hk01.com/a.png/x", "第一張圖"),
        _text_block([("text", "第一段文字")]),
        _img_block("https://cdn.hk01.com/b.png/y", "第二張圖"),
        _text_block([("h2", "小標題"), ("text", "第二段文字")]),
    ]
    result = extract_structured_article(_page(_article(blocks)), URL)
    assert result is not None

    soup = BeautifulSoup(result["content"], "html.parser")
    order = [t.name for t in soup.find_all(["img", "p", "h2"])]
    assert order == ["img", "p", "img", "h2", "p"]
    assert [i["src"] for i in soup.find_all("img")] == [
        "https://cdn.hk01.com/a.png/x",
        "https://cdn.hk01.com/b.png/y",
    ]
    assert "第一段文字" in soup.get_text()
    assert soup.find("h2").get_text() == "小標題"


def test_keeps_image_captions():
    blocks = [_img_block("https://cdn.hk01.com/a.png/x", "掘爆機在武漢誕生。（央視新聞）")]
    result = extract_structured_article(_page(_article(blocks)), URL)
    soup = BeautifulSoup(result["content"], "html.parser")
    assert soup.find("figcaption").get_text() == "掘爆機在武漢誕生。（央視新聞）"
    assert soup.find("img")["alt"] == "掘爆機在武漢誕生。（央視新聞）"


def test_excludes_related_article_widget():
    """blocks[].articles[] is a recommendation widget, not this article's images."""
    blocks = [
        _img_block("https://cdn.hk01.com/real.png/x"),
        _text_block([("text", "內文")]),
        {
            "blockType": "relatedArticles",
            "articles": [
                {"data": {"mainImage": {"cdnUrl": "https://cdn.hk01.com/RELATED.png/z"},
                          "thumbnails": [{"cdnUrl": "https://cdn.hk01.com/THUMB.png/z"}]}}
            ],
        },
    ]
    result = extract_structured_article(_page(_article(blocks)), URL)
    assert "RELATED" not in result["content"]
    assert "THUMB" not in result["content"]
    assert "real.png" in result["content"]


def test_ignores_non_image_blocks_that_carry_an_image_key():
    """A trailing embed block has an image dict but no cdnUrl — must not emit an <img>."""
    blocks = [
        _text_block([("text", "內文")]),
        {"blockType": "embed", "image": {"mediaId": 1}, "htmlString": "<div>ad</div>"},
    ]
    result = extract_structured_article(_page(_article(blocks)), URL)
    assert BeautifulSoup(result["content"], "html.parser").find("img") is None


def test_includes_the_lede_from_teaser():
    """The opening paragraphs live outside blocks, in article.teaser."""
    art = _article([_text_block([("text", "內文第一段")])])
    art["teaser"] = ["這是導語段落。", "第二句導語。"]
    result = extract_structured_article(_page(art), URL)
    paras = [p.get_text() for p in BeautifulSoup(result["content"], "html.parser").find_all("p")]
    assert paras[:2] == ["這是導語段落。", "第二句導語。"]
    assert "內文第一段" in paras[2]


def test_prefers_teaser_over_truncated_description():
    """description is a truncated meta field; teaser holds the full text."""
    art = _article([_text_block([("text", "內文")])])
    art["teaser"] = ["完整的導語段落，沒有被截斷。"]
    art["description"] = "完整的導語段落，沒有被"
    result = extract_structured_article(_page(art), URL)
    text = BeautifulSoup(result["content"], "html.parser").get_text()
    assert "完整的導語段落，沒有被截斷。" in text
    assert text.count("完整的導語段落") == 1


def test_falls_back_to_description_without_teaser():
    art = _article([_text_block([("text", "內文")])])
    art["description"] = "只有描述。"
    result = extract_structured_article(_page(art), URL)
    assert "只有描述。" in BeautifulSoup(result["content"], "html.parser").get_text()


def test_does_not_duplicate_lede_already_present_in_blocks():
    art = _article([_text_block([("text", "這是導語段落。")]), _text_block([("text", "其後內文")])])
    art["teaser"] = ["這是導語段落。"]
    result = extract_structured_article(_page(art), URL)
    assert result["content"].count("這是導語段落。") == 1


def test_renders_summary_block():
    """A summary block holds extra intro paragraphs as a list of strings."""
    blocks = [
        _text_block([("text", "內文")]),
        {"blockType": "summary", "summary": ["重點一：載客率創新高。", "重點二：投入1,500億元。"]},
    ]
    result = extract_structured_article(_page(_article(blocks)), URL)
    text = BeautifulSoup(result["content"], "html.parser").get_text()
    assert "重點一：載客率創新高。" in text
    assert "重點二：投入1,500億元。" in text


def test_returns_main_image():
    result = extract_structured_article(
        _page(_article([_text_block([("text", "x")])], main="https://cdn.hk01.com/hero.jpg/q")), URL
    )
    assert result["main_image"] == "https://cdn.hk01.com/hero.jpg/q"


def test_escapes_text_content():
    blocks = [_text_block([("text", '<script>alert("x")</script> & more')])]
    result = extract_structured_article(_page(_article(blocks)), URL)
    assert "<script>alert" not in result["content"]
    assert "&amp; more" in result["content"] or "& more" in BeautifulSoup(
        result["content"], "html.parser"
    ).get_text()


def test_unknown_token_types_degrade_to_text():
    blocks = [{"blockType": "text", "htmlTokens": [[{"type": "quote", "content": "引文"}]]}]
    result = extract_structured_article(_page(_article(blocks)), URL)
    assert "引文" in BeautifulSoup(result["content"], "html.parser").get_text()


def test_returns_none_for_other_domains():
    assert extract_structured_article(_page(_article([])), "https://www.bbc.com/news/1") is None


def test_returns_none_when_payload_missing_or_broken():
    assert extract_structured_article("<html><body>no script</body></html>", URL) is None
    assert extract_structured_article(
        '<html><script id="__NEXT_DATA__">not json{</script></html>', URL
    ) is None
    assert extract_structured_article(_page({"title": "x"}), URL) is None


def test_returns_none_when_no_blocks_produce_content():
    assert extract_structured_article(_page(_article([])), URL) is None
