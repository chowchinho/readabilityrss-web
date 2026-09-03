# tests/services/test_image_recovery.py
import pytest
from bs4 import BeautifulSoup
from app.utils.image_recovery import ImageRecovery
from app.utils.boilerplate_filter import BoilerplateFilter


def test_extracts_images_from_container():
    """Should extract images from identified container"""
    html = '''
    <html>
    <article>
        <p>Article text</p>
        <img alt="content1" src="img1.jpg">
        <img alt="content2" src="img2.jpg">
    </article>
    <aside>
        <img alt="sidebar" src="sidebar.jpg">
    </aside>
    </html>
    '''

    recovery = ImageRecovery(boilerplate_filter=BoilerplateFilter())
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("article")

    images = recovery.extract_images(container)

    # Verify extraction
    assert len(images) == 2
    assert images[0]['url'] == 'img1.jpg'
    assert images[0]['alt'] == 'content1'
    assert images[1]['url'] == 'img2.jpg'
    assert images[1]['alt'] == 'content2'
    # Verify sidebar image is not included (from different container)
    assert all(img['url'] != 'sidebar.jpg' for img in images)


def test_handles_lazy_loaded_images():
    """Should handle data-src and data-lazy-src attributes"""
    html = '''
    <html>
    <article>
        <img data-src="lazy1.jpg" alt="lazy">
        <img data-lazy-src="lazy2.jpg" alt="very-lazy">
        <img src="direct.jpg" alt="direct">
    </article>
    </html>
    '''

    recovery = ImageRecovery(boilerplate_filter=BoilerplateFilter())
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("article")

    images = recovery.extract_images(container)

    assert len(images) == 3
    assert images[0]['url'] == 'lazy1.jpg'
    assert images[1]['url'] == 'lazy2.jpg'
    assert images[2]['url'] == 'direct.jpg'


def test_filters_tracking_pixels():
    """Should filter small images that look like tracking pixels"""
    html = '''
    <html>
    <article>
        <img src="content.jpg" width="800" height="600" alt="content">
        <img src="tracker.gif" width="1" height="1" alt="tracker">
        <img src="pixel.png" alt="pixel">
    </article>
    </html>
    '''

    recovery = ImageRecovery(boilerplate_filter=BoilerplateFilter())
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("article")

    images = recovery.extract_images(container)

    # Should keep content image, filter out tracking pixel
    assert len(images) == 1
    assert images[0]['url'] == 'content.jpg'


def test_filters_tracking_pixels_by_aspect_ratio():
    """Should filter images with extreme aspect ratios"""
    html = '''
    <html>
    <article>
        <img src="normal.jpg" width="800" height="600" alt="normal">
        <img src="wide.gif" width="2000" height="1" alt="extreme-wide">
        <img src="tall.gif" width="1" height="2000" alt="extreme-tall">
    </article>
    </html>
    '''

    recovery = ImageRecovery(boilerplate_filter=BoilerplateFilter())
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("article")

    images = recovery.extract_images(container)

    # Should keep normal image, filter extreme aspect ratios
    assert len(images) == 1
    assert images[0]['url'] == 'normal.jpg'


def test_filters_boilerplate_images():
    """Should apply boilerplate filter to images"""
    html = '''
    <html>
    <article>
        <img src="article1.jpg" alt="article">
        <aside class="hawk-root">
            <img src="merchant.jpg" alt="merchant">
        </aside>
        <img src="article2.jpg" alt="article2">
    </article>
    </html>
    '''

    recovery = ImageRecovery(boilerplate_filter=BoilerplateFilter())
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("article")

    images = recovery.extract_images(container, apply_boilerplate_filter=True)

    # Should only recover the two article images
    assert len(images) == 2
    assert images[0]['url'] == 'article1.jpg'
    assert images[1]['url'] == 'article2.jpg'


def test_skips_images_without_src():
    """Should skip images without valid src attributes"""
    html = '''
    <html>
    <article>
        <img alt="no-src">
        <img src="valid.jpg" alt="valid">
        <img src="data:image/gif;base64,..." alt="data-uri">
    </article>
    </html>
    '''

    recovery = ImageRecovery(boilerplate_filter=BoilerplateFilter())
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("article")

    images = recovery.extract_images(container)

    # Should only keep valid external URL
    assert len(images) == 1
    assert images[0]['url'] == 'valid.jpg'
