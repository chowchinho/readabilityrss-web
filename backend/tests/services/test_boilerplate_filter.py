"""Tests for boilerplate filter functionality."""
import pytest
from bs4 import BeautifulSoup
from app.utils.boilerplate_filter import BoilerplateFilter


@pytest.fixture
def filter_instance():
    """Fixture providing a BoilerplateFilter instance."""
    return BoilerplateFilter()


def test_identifies_hawk_widget_images(filter_instance):
    """Images in hawk-class elements should be marked as boilerplate."""
    html = """
    <html>
        <body>
            <div class="hawk-widget">
                <img src="https://example.com/hawk-ad.jpg" alt="hawk">
            </div>
            <div class="main-content">
                <img src="https://example.com/article.jpg" alt="article">
            </div>
        </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")

    filtered = filter_instance.filter_content_images(images)

    # Should only keep the article image, not the hawk-widget image
    assert len(filtered) == 1
    assert filtered[0].get("src") == "https://example.com/article.jpg"


def test_identifies_sidebar_images(filter_instance):
    """Images in sidebar elements should be filtered."""
    html = """
    <html>
        <body>
            <aside id="sidebar">
                <img src="https://example.com/sidebar-ad.jpg" alt="sidebar">
            </aside>
            <main>
                <img src="https://example.com/main.jpg" alt="main">
            </main>
        </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")

    filtered = filter_instance.filter_content_images(images)

    # Should only keep the main image, not the sidebar image
    assert len(filtered) == 1
    assert filtered[0].get("src") == "https://example.com/main.jpg"


def test_identifies_related_articles_images(filter_instance):
    """Images in related-content containers should be filtered."""
    html = """
    <html>
        <body>
            <article>
                <img src="https://example.com/article-image.jpg" alt="article">
            </article>
            <div class="related-articles">
                <img src="https://example.com/related1.jpg" alt="related">
                <img src="https://example.com/related2.jpg" alt="related">
            </div>
        </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")

    filtered = filter_instance.filter_content_images(images)

    # Should only keep the article image
    assert len(filtered) == 1
    assert filtered[0].get("src") == "https://example.com/article-image.jpg"


def test_keeps_images_without_boilerplate_markers(filter_instance):
    """Images without boilerplate markers should be kept."""
    html = """
    <html>
        <body>
            <article>
                <img src="https://example.com/image1.jpg" alt="image1">
                <img src="https://example.com/image2.jpg" alt="image2">
                <img src="https://example.com/image3.jpg" alt="image3">
            </article>
        </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")

    filtered = filter_instance.filter_content_images(images)

    # Should keep all images since none are in boilerplate containers
    assert len(filtered) == 3
    assert filtered[0].get("src") == "https://example.com/image1.jpg"
    assert filtered[1].get("src") == "https://example.com/image2.jpg"
    assert filtered[2].get("src") == "https://example.com/image3.jpg"


def test_keeps_images_in_roadmap_class(filter_instance):
    """Images in classes containing 'ad' substring (e.g., roadmap) should NOT be filtered."""
    html = """
    <html>
        <body>
            <div class="product-roadmap">
                <img src="https://example.com/roadmap.jpg" alt="roadmap">
            </div>
        </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")

    filtered = filter_instance.filter_content_images(images)

    # Should keep the roadmap image (false positive prevention)
    assert len(filtered) == 1
    assert filtered[0].get("src") == "https://example.com/roadmap.jpg"


def test_keeps_images_in_reading_class(filter_instance):
    """Images in classes containing 'ad' substring (e.g., reading) should NOT be filtered."""
    html = """
    <html>
        <body>
            <div class="reading-time">
                <img src="https://example.com/reading.jpg" alt="reading">
            </div>
        </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")

    filtered = filter_instance.filter_content_images(images)

    # Should keep the reading image (false positive prevention)
    assert len(filtered) == 1
    assert filtered[0].get("src") == "https://example.com/reading.jpg"


def test_filters_actual_ad_word_in_class(filter_instance):
    """Images in classes with 'ad' as a whole word should be filtered."""
    html = """
    <html>
        <body>
            <div class="ad-banner">
                <img src="https://example.com/ad-banner.jpg" alt="ad">
            </div>
            <div class="article-content">
                <img src="https://example.com/content.jpg" alt="content">
            </div>
        </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")

    filtered = filter_instance.filter_content_images(images)

    # Should only keep the article image
    assert len(filtered) == 1
    assert filtered[0].get("src") == "https://example.com/content.jpg"
