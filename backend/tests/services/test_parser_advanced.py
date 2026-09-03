"""Advanced tests for ReadabilityParser enhancements."""
import pytest
from app.services.parser import ReadabilityParser

@pytest.fixture
def parser():
    return ReadabilityParser()

def test_global_sibling_exclusion(parser):
    """Test that a sibling of the content container can be excluded globally."""
    html = """
    <html>
        <body>
            <article class="post-body-article">
                <p>Main content here.</p>
            </article>
            <aside class="post-body-sidebar">
                <p>Sidebar content that should be gone.</p>
            </aside>
        </body>
    </html>
    """
    # Use standard parse (no content_selector)
    # Readability might normally keep the sidebar if it looks relevant
    result = parser.parse(html, content_exclude_selector=".post-body-sidebar")
    
    assert "Main content here." in result["content"]
    assert "Sidebar content" not in result["content"]

def test_join_multiple_containers_first_of_each(parser):
    """Test joining the first match of each comma-separated selector."""
    html = """
    <html>
        <body>
            <div class="intro">Intro part.</div>
            <div class="intro">Ignored second intro.</div>
            <div class="body">Body part.</div>
            <div class="body">Ignored second body.</div>
            <div class="outro">Outro part.</div>
        </body>
    </html>
    """
    overrides = {
        "content_selector": ".intro, .body, .outro"
    }
    result = parser.parse_with_overrides(html, overrides=overrides)
    
    assert "Intro part." in result["content"]
    assert "Body part." in result["content"]
    assert "Outro part." in result["content"]
    assert "Ignored second intro" not in result["content"]
    assert "Ignored second body" not in result["content"]

def test_deduplicate_nested_selections(parser):
    """Test that nested elements are not joined twice if they match different selectors."""
    html = """
    <html>
        <body>
            <article class="main">
                <div class="inner">Nested content.</div>
            </article>
        </body>
    </html>
    """
    # If we use article, .inner - technically .inner is a match and article is a match.
    # But because article is matches first part and .inner matches second part,
    # and we joined them, we should ensure we don't have it twice.
    # However, our logic picks DIFFERENT objects. article and div are different objects.
    # To TRULY avoid doubling in a join, we would need to check if one is inside another.
    
    overrides = {
        "content_selector": ".main, .inner"
    }
    result = parser.parse_with_overrides(html, overrides=overrides)
    
    # In my current implementation:
    # 1. .main matches <article>
    # 2. .inner matches <div class="inner">
    # Result: Combined contents of both. Since <article> contains <div class="inner">,
    # the summary of <article> ALREADY has the div's content.
    # Joining them will double the text.
    
    # Wait, the user asked for "multiple first content_selector". 
    # If they use overlapping selectors, they might get doubling.
    # I should add a check to skip an element if it's already a child of a previously picked element.
    
    count = result["content"].count("Nested content.")
    assert count == 1

def test_exclude_selector_in_multi_container(parser):
    """Test that exclusion works across all joined containers."""
    html = """
    <html>
        <body>
            <div class="part1">
                <p>Part 1 text.</p>
                <div class="ad">Internal Ad</div>
            </div>
            <div class="part2">
                <p>Part 2 text.</p>
                <div class="ad">Internal Ad 2</div>
            </div>
        </body>
    </html>
    """
    overrides = {
        "content_selector": ".part1, .part2",
        "content_exclude_selector": ".ad"
    }
    result = parser.parse_with_overrides(html, overrides=overrides)
    
    assert "Part 1 text." in result["content"]
    assert "Part 2 text." in result["content"]
    assert "Internal Ad" not in result["content"]
