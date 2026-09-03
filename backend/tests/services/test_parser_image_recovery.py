# tests/services/test_parser_image_recovery.py
import pytest
from bs4 import BeautifulSoup
from app.services.parser import ReadabilityParser


def test_recover_images_from_container():
    """Parser should recover images from identified container"""
    html = '''
    <html>
    <article>
        <p>Article text</p>
        <img alt="content1" src="img1.jpg">
        <img alt="content2" src="img2.jpg">
        <aside class="hawk-root">
            <img alt="hawk" src="merchant.jpg">
        </aside>
    </article>
    </html>
    '''

    parser = ReadabilityParser()
    recovered = parser.recover_images_from_html(html, container_selector='article')

    # Should recover 2 content images, filter out hawk widget
    assert len(recovered) == 2
    assert recovered[0]['alt'] == 'content1'
    assert recovered[1]['alt'] == 'content2'


def test_merge_readability_with_images():
    """Parser should merge Readability output with recovered images"""
    html = '''
    <!DOCTYPE html>
    <html>
    <head><title>Article</title></head>
    <body>
        <article>
            <h1>Title</h1>
            <p>First paragraph with <img src="img1.jpg" alt="fig1"></p>
            <p>Second paragraph</p>
            <img src="img2.jpg" alt="fig2">
            <aside class="related"><img src="related.jpg"></aside>
        </article>
    </body>
    </html>
    '''

    parser = ReadabilityParser()
    result = parser.extract_with_images(html, container_selector='article')

    # Should have content text from Readability
    assert 'First paragraph' in result['content']
    assert 'Second paragraph' in result['content']

    # Should have recovered images
    assert len(result.get('images', [])) >= 2
    assert any(img['url'] == 'img1.jpg' for img in result.get('images', []))
    assert any(img['url'] == 'img2.jpg' for img in result.get('images', []))


def test_extract_with_images_without_container():
    """Should handle extraction without explicit container selector"""
    html = '''
    <html>
    <body>
        <div id="main">
            <img src="main.jpg" alt="main">
        </div>
        <div id="sidebar">
            <img src="sidebar.jpg" alt="sidebar">
        </div>
    </body>
    </html>
    '''

    parser = ReadabilityParser()
    result = parser.extract_with_images(html)

    # Should extract content and images using Readability detection
    assert 'content' in result
    assert 'images' in result
