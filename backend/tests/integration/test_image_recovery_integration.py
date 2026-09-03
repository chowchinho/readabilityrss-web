"""Integration tests for image recovery system."""
import pytest
from app.services.parser import ReadabilityParser
from app.utils.image_recovery import ImageRecovery
from app.utils.boilerplate_filter import BoilerplateFilter
from bs4 import BeautifulSoup


class TestImageRecoveryIntegration:
    """Integration tests for complete image recovery flow."""

    def test_end_to_end_image_recovery_with_boilerplate(self):
        """Test complete flow: parse HTML -> identify container -> recover images -> filter boilerplate"""
        html = '''
        <!DOCTYPE html>
        <html>
        <head><title>Tech Review Article</title></head>
        <body>
            <header><img src="logo.png" alt="logo"></header>
            <article>
                <h1>The Best Tablets</h1>
                <img src="tablet1.jpg" alt="iPad Pro" width="800" height="600">
                <p>This is the first paragraph of the article. It contains important information about tablets and their features for reading. Great device for reading.</p>
                <img src="tablet2.jpg" alt="Samsung Tab" width="800" height="600">

                <aside class="hawk-widget">
                    <h3>Where to Buy</h3>
                    <img src="affiliate-widget.jpg" alt="merchant" width="200" height="200">
                </aside>

                <p>This is another paragraph with more details. Another great option for anyone looking for a quality tablet.</p>
                <img src="tablet3.jpg" alt="iPad Air" width="800" height="600">

                <div class="related-articles">
                    <img src="related1.jpg" alt="related">
                    <img src="related2.jpg" alt="related">
                </div>
            </article>
            <footer><img src="footer-ad.jpg" width="1" height="1"></footer>
        </body>
        </html>
        '''

        parser = ReadabilityParser()
        result = parser.extract_with_images(html, container_selector='article')

        # Should extract title (using page's <title> tag)
        assert result.get('title') == 'Tech Review Article'

        # Should recover tablet images from article
        images = result.get('images', [])
        image_alts = [img.get('alt', '') for img in images]

        # Should include content images
        assert any('iPad' in alt for alt in image_alts)
        assert any('Samsung' in alt for alt in image_alts)

        # Should exclude boilerplate images
        assert not any('merchant' in alt for alt in image_alts)  # hawk widget filtered
        assert not any('related' in alt for alt in image_alts)   # related articles filtered


    def test_multi_url_learning_pattern(self):
        """Test that patterns are learned across multiple URLs"""
        html1 = '''
        <article>
            <h1>Article 1</h1>
            <img src="img1.jpg" alt="fig1">
            <p>Content 1</p>
            <img src="img2.jpg" alt="fig2">
            <aside class="sidebar"><img src="sidebar.jpg"></aside>
        </article>
        '''

        html2 = '''
        <article>
            <h1>Article 2</h1>
            <img src="img3.jpg" alt="fig3">
            <p>Content 2</p>
            <img src="img4.jpg" alt="fig4">
            <aside class="sidebar"><img src="sidebar2.jpg"></aside>
        </article>
        '''

        parser = ReadabilityParser()

        # Parse first URL
        result1 = parser.extract_with_images(html1, container_selector='article')
        images1 = result1.get('images', [])

        # Parse second URL with same container selector
        result2 = parser.extract_with_images(html2, container_selector='article')
        images2 = result2.get('images', [])

        # Both should have filtered out sidebar
        assert len(images1) == 2  # Only img1, img2
        assert len(images2) == 2  # Only img3, img4

        # Pattern is consistent across URLs
        assert all('sidebar' not in img['url'] for img in images1)
        assert all('sidebar' not in img['url'] for img in images2)


    def test_image_recovery_with_lazy_loading(self):
        """Test recovery of lazy-loaded images"""
        html = '''
        <article>
            <img src="placeholder.jpg" data-src="lazy1.jpg" alt="lazy1">
            <img data-lazy-src="lazy2.jpg" alt="lazy2">
            <img src="normal.jpg" alt="normal">
            <img src="tracker.gif" width="1" height="1" alt="tracker">
        </article>
        '''

        recovery = ImageRecovery(boilerplate_filter=BoilerplateFilter())
        soup = BeautifulSoup(html, 'html.parser')
        container = soup.find('article')

        images = recovery.extract_images(container)

        # Should recover all proper images
        urls = [img['url'] for img in images]
        assert 'lazy1.jpg' in urls  # data-src prioritized
        assert 'lazy2.jpg' in urls  # data-lazy-src handled
        assert 'normal.jpg' in urls # regular src handled

        # Should filter tracking pixel
        assert 'tracker.gif' not in urls


    def test_boilerplate_filter_accuracy(self):
        """Test boilerplate filter correctly identifies and removes non-content images"""
        html = '''
        <div class="main-content">
            <img src="article.jpg" alt="article">

            <div class="hawk-widget">
                <img src="merchant.jpg" alt="merchant">
            </div>

            <img src="article2.jpg" alt="article2">

            <div id="ad">
                <img src="ad1.jpg" alt="ad">
                <img src="ad2.jpg" alt="ad">
            </div>

            <img src="article3.jpg" alt="article3">
        </div>
        '''

        recovery = ImageRecovery(boilerplate_filter=BoilerplateFilter())
        soup = BeautifulSoup(html, 'html.parser')
        container = soup.find('div', class_='main-content')

        images = recovery.extract_images(container, apply_boilerplate_filter=True)

        # Should only recover article images
        assert len(images) == 3
        urls = [img['url'] for img in images]
        assert 'article.jpg' in urls
        assert 'article2.jpg' in urls
        assert 'article3.jpg' in urls

        # Should filter out merchant and ad images
        assert 'merchant.jpg' not in urls
        assert 'ad1.jpg' not in urls
        assert 'ad2.jpg' not in urls
