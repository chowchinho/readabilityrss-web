from app.services.snippets import build_snippets


def test_strips_translation_badge_and_truncates():
    """Badge shape copied from scheduler.py:404, which prepends it as a sibling <p>."""
    html = (
        '<p style="color:#888;font-size:0.85em;">\U0001F310 Translated by DeepL</p>'
        '<p>Real article text starts here.</p>'
    )
    snippet, featured = build_snippets(html)
    assert snippet == "Real article text starts here."
    assert featured == "Real article text starts here."


def test_short_wrapper_holding_both_badge_and_body_is_dropped_whole():
    """Pre-existing quirk, locked in deliberately. The rule tests each element's own
    combined text, so a wrapper under 120 chars containing the badge takes the body
    down with it. Changing this would change previews on live articles."""
    html = (
        '<div><small>\U0001F310 Translated by DeepL</small>'
        '<p>Real article text starts here.</p></div>'
    )
    assert build_snippets(html) == ("", "")


def test_badge_rule_only_applies_under_120_chars():
    long_badge = "Translated by " + ("x" * 120)
    html = f"<p>{long_badge}</p><p>Body.</p>"
    snippet, _ = build_snippets(html)
    assert long_badge in snippet


def test_truncates_at_200_and_1200_with_ellipsis():
    html = "<p>" + ("a" * 1500) + "</p>"
    snippet, featured = build_snippets(html)
    assert snippet == "a" * 200 + "..."
    assert featured == "a" * 1200 + "..."


def test_no_ellipsis_when_under_the_limit():
    snippet, featured = build_snippets("<p>Short.</p>")
    assert snippet == "Short."
    assert featured == "Short."


def test_empty_and_none_return_empty_pair():
    assert build_snippets("") == ("", "")
    assert build_snippets(None) == ("", "")


def test_input_is_truncated_to_6000_chars():
    """The read path parses SUBSTR(content, 1, 6000); the write path holds full content.
    The builder must truncate so both produce the same string."""
    filler = "<span>x</span>" * 500
    html = filler + "<p>" + ("z" * 400) + "</p>"
    _, featured = build_snippets(html)
    assert "z" not in featured


def test_strips_html_tags():
    html = "<article><h1>Heading</h1><p>First paragraph with <a href='https://example.com'>a link</a>.</p></article>"
    snippet, _ = build_snippets(html)
    assert "Heading" in snippet
    assert "First paragraph with a link" in snippet
    assert "<" not in snippet and ">" not in snippet
