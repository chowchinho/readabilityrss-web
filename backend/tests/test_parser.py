"""Tests for readability extraction functionality."""
import pytest
from datetime import datetime
from pathlib import Path
from app.services.parser import ReadabilityParser


@pytest.fixture
def parser():
    """Fixture providing a ReadabilityParser instance."""
    return ReadabilityParser()


@pytest.fixture
def sample_html_path():
    """Fixture providing path to sample HTML fixture."""
    return Path(__file__).parent / "fixtures" / "sample.html"


@pytest.fixture
def sample_html(sample_html_path):
    """Fixture providing sample HTML content."""
    with open(sample_html_path, "r") as f:
        return f.read()


def test_extract_title_from_html(parser, sample_html):
    """Test that title is correctly extracted from HTML."""
    result = parser.parse(sample_html)
    assert result["title"] == "Test Article: Lazy Loading Images"


def test_extract_content_from_html(parser, sample_html):
    """Test that main content is correctly extracted from HTML."""
    result = parser.parse(sample_html)
    assert "first paragraph of the article" in result["content"]
    assert "lazy loading improves page performance" in result["content"]


def test_extract_language_from_meta(parser, sample_html):
    """Test that language is correctly extracted from meta tags."""
    result = parser.parse(sample_html)
    assert result["language"] == "en"


def test_extract_publish_date(parser, sample_html, monkeypatch):
    """Test that publish date is correctly extracted from article:published_time."""
    # sample.html carries a fixed date, so the freshness clock has to be fixed too -
    # otherwise this fails once that date ages past 90 days. See _now() in parser.py.
    monkeypatch.setattr(parser, "_now", lambda: datetime(2026, 3, 20))
    result = parser.parse(sample_html)
    assert result["publish_date"] == "2026-03-17"


def test_extract_main_image(parser, sample_html):
    """Test that main image URL is correctly extracted from og:image."""
    result = parser.parse(sample_html)
    assert result["main_image"] == "https://example.com/images/article-cover.jpg"


def test_convert_lazy_images_data_src(parser):
    """Test that data-src attributes are converted to src attributes."""
    html = '<img data-src="https://example.com/img.jpg" alt="test">'
    result = parser.convert_lazy_images(html)
    assert 'src="https://example.com/img.jpg"' in result
    assert 'data-src' not in result


def test_convert_lazy_images_data_lazy_src(parser):
    """Test that data-lazy-src attributes are converted to src attributes."""
    html = '<img data-lazy-src="https://example.com/img2.jpg" alt="test">'
    result = parser.convert_lazy_images(html)
    assert 'src="https://example.com/img2.jpg"' in result
    assert 'data-lazy-src' not in result


def test_preserve_normal_images(parser):
    """Test that normal images with src are preserved."""
    html = '<img src="https://example.com/normal.jpg" alt="normal">'
    result = parser.convert_lazy_images(html)
    assert 'src="https://example.com/normal.jpg"' in result


def test_preserve_alt_text_when_converting(parser):
    """Test that alt text is preserved when converting lazy images."""
    html = '<img data-src="https://example.com/img.jpg" alt="My image">'
    result = parser.convert_lazy_images(html)
    assert 'alt="My image"' in result


def test_convert_lazy_images_data_srcset_placeholder(parser):
    """Test that data-srcset promotes a concrete src when the existing src is a placeholder."""
    html = '<img src="data:image/gif;base64,abc" data-srcset="https://example.com/small.jpg 400w, https://example.com/large.jpg 1200w">'
    result = parser.convert_lazy_images(html)
    assert 'src="https://example.com/large.jpg"' in result
    assert 'srcset="https://example.com/small.jpg 400w, https://example.com/large.jpg 1200w"' in result


def test_extract_srcset_candidate_keeps_commas_inside_urls(parser):
    """URLs with query-string commas should stay intact when picking a srcset candidate."""
    srcset = (
        "https://asset.watch.impress.co.jp/img/sample-small.jpg?quality=85,75&format=jpeg 536w, "
        "https://asset.watch.impress.co.jp/img/sample-large.jpg?quality=85,75&format=jpeg 1200w"
    )

    assert parser._extract_srcset_candidate(srcset) == (
        "https://asset.watch.impress.co.jp/img/sample-large.jpg?quality=85,75&format=jpeg"
    )


def test_convert_lazy_images_picture_source(parser):
    """Test that picture/source lazy markup promotes a usable img src."""
    html = """
    <picture>
        <source data-srcset="https://example.com/pic-800.jpg 800w, https://example.com/pic-1200.jpg 1200w">
        <img src="data:image/gif;base64,abc" alt="Example">
    </picture>
    """
    result = parser.convert_lazy_images(html)
    assert 'src="https://example.com/pic-1200.jpg"' in result


def test_convert_lazy_images_noscript_fallback(parser):
    """Test that noscript image fallbacks populate placeholder images."""
    html = """
    <div>
        <img src="data:image/gif;base64,abc" alt="Example">
        <noscript><img src="https://example.com/fallback.jpg" alt="Example"></noscript>
    </div>
    """
    result = parser.convert_lazy_images(html)
    assert 'src="https://example.com/fallback.jpg"' in result


def test_override_title_with_css_selector(parser):
    """Test that title can be overridden using a CSS selector."""
    html = """
    <html>
        <body>
            <h1>Main Title</h1>
            <div class="custom-title">Custom Article Title</div>
            <article>Content here</article>
        </body>
    </html>
    """
    result = parser.parse_with_overrides(html, overrides={"title_selector": ".custom-title"})
    assert result["title"] == "Custom Article Title"


def test_override_content_with_css_selector(parser):
    """Test that content can be overridden using a CSS selector."""
    html = """
    <html>
        <body>
            <article>Auto-detected content</article>
            <div class="custom-content">This is the real content</div>
        </body>
    </html>
    """
    result = parser.parse_with_overrides(html, overrides={"content_selector": ".custom-content"})
    assert "This is the real content" in result["content"]


def test_tidy_html_removes_excessive_whitespace(parser):
    """Test that _tidy_html removes empty tags and collapses extra whitespace."""
    dirty_html = """
    <div class="content">
        <p>First paragraph.</p>
        
        <p>   </p>
        
        <p>Second paragraph.    With many spaces.</p>
        
        <div>
            <span></span>
        </div>
        
        <p>Third paragraph.</p>
    </div>
    """
    # pylint: disable=protected-access
    tidy_html = parser._tidy_html(dirty_html)
    
    # Check that real content is present
    assert "First paragraph." in tidy_html
    assert "Second paragraph. With many spaces." in tidy_html
    assert "Third paragraph." in tidy_html
    
    # Check that empty tags are removed
    assert "<p> </p>" not in tidy_html
    assert "<span></span>" not in tidy_html
    
    # Check that excessive whitespace is collapsed
    # Note: str(BeautifulSoup) might add its own newlines/indentation depending on structure
    # but the content should be much cleaner
    assert "    " not in tidy_html
