import pytest
from app.services.parser import ReadabilityParser
from datetime import datetime, timedelta

# Every date asserted on below is fixed, so the clock the parser measures freshness
# against has to be fixed too - otherwise the suite passes until 2026-03-17 falls out
# of the 90-day window and then fails for a reason that has nothing to do with parsing.
# Set 3 days after the sample date so both it and 2026-03-20 are inside the window.
FIXED_NOW = datetime(2026, 3, 20)


def _pinned_parser(monkeypatch):
    parser = ReadabilityParser()
    monkeypatch.setattr(parser, "_now", lambda: FIXED_NOW)
    return parser


class TestDateParsing:
    """Test date parsing and validation logic."""

    @pytest.fixture
    def parser(self, monkeypatch):
        return _pinned_parser(monkeypatch)

    # ===== Structured Format Tests =====

    def test_parse_iso_format_valid(self, parser):
        """ISO format YYYY-MM-DD should parse correctly."""
        result = parser._parse_and_validate_date("2026-03-17")
        assert result == "2026-03-17"

    def test_parse_iso_format_with_time(self, parser):
        """ISO format with time should extract just the date."""
        result = parser._parse_and_validate_date("2026-03-17T14:30:00Z")
        assert result == "2026-03-17"

    def test_parse_slash_format_yyyy_mm_dd(self, parser):
        """Slash format YYYY/MM/DD should normalize to ISO."""
        result = parser._parse_and_validate_date("2026/03/17")
        assert result == "2026-03-17"

    def test_parse_slash_format_mm_dd_yyyy(self, parser):
        """Slash format MM/DD/YYYY should normalize to ISO."""
        result = parser._parse_and_validate_date("03/17/2026")
        assert result == "2026-03-17"

    def test_parse_dot_format(self, parser):
        """Dot format YYYY.MM.DD should normalize to ISO."""
        result = parser._parse_and_validate_date("2026.03.17")
        assert result == "2026-03-17"

    def test_parse_invalid_format(self, parser):
        """Invalid format should return empty string."""
        result = parser._parse_and_validate_date("invalid-date")
        assert result == ""

    # ===== Sanity Check Tests =====

    def test_sanity_check_future_date_within_tolerance(self, parser):
        """Dates up to 7 days in the future should be accepted."""
        future_date = (FIXED_NOW + timedelta(days=3)).strftime("%Y-%m-%d")
        result = parser._parse_and_validate_date(future_date)
        assert result == future_date

    def test_sanity_check_future_date_beyond_tolerance(self, parser):
        """Dates more than 7 days in the future should be rejected."""
        future_date = (FIXED_NOW + timedelta(days=10)).strftime("%Y-%m-%d")
        result = parser._parse_and_validate_date(future_date)
        assert result == ""

    def test_sanity_check_valid_old_date(self, parser):
        """Dates within 90 days should be accepted."""
        old_date = (FIXED_NOW - timedelta(days=60)).strftime("%Y-%m-%d")
        result = parser._parse_and_validate_date(old_date)
        assert result == old_date

    def test_sanity_check_invalid_old_date(self, parser):
        """Dates older than 90 days should be rejected."""
        old_date = (FIXED_NOW - timedelta(days=100)).strftime("%Y-%m-%d")
        result = parser._parse_and_validate_date(old_date)
        assert result == ""

    def test_sanity_check_invalid_month(self, parser):
        """Invalid month (13) should be rejected."""
        result = parser._parse_and_validate_date("2026-13-17")
        assert result == ""

    def test_sanity_check_invalid_day(self, parser):
        """Invalid day (32) should be rejected."""
        result = parser._parse_and_validate_date("2026-03-32")
        assert result == ""

    def test_sanity_check_february_29_non_leap_year(self, parser):
        """Feb 29 in non-leap year should be rejected."""
        result = parser._parse_and_validate_date("2025-02-29")
        assert result == ""

    def test_sanity_check_february_29_leap_year(self, parser, monkeypatch):
        """Feb 29 in leap year should be accepted."""
        # 2024 is well outside the freshness window from FIXED_NOW, so this one case
        # needs its own reference date rather than the module-wide one.
        monkeypatch.setattr(parser, "_now", lambda: datetime(2024, 3, 15))
        result = parser._parse_and_validate_date("2024-02-29")
        assert result == "2024-02-29"

    # ===== Natural Language Format Tests =====

    def test_parse_natural_language_month_full_comma(self, parser):
        """Format 'Month DD, YYYY' should parse correctly."""
        result = parser._parse_and_validate_date("March 17, 2026")
        assert result == "2026-03-17"

    def test_parse_natural_language_month_abbreviated_comma(self, parser):
        """Format 'Mon DD, YYYY' (abbreviated month) should parse correctly."""
        result = parser._parse_and_validate_date("Mar 17, 2026")
        assert result == "2026-03-17"

    def test_parse_natural_language_dd_month_yyyy(self, parser):
        """Format 'DD Month YYYY' should parse correctly."""
        result = parser._parse_and_validate_date("17 March 2026")
        assert result == "2026-03-17"

    def test_parse_natural_language_dd_month_yyyy_abbreviated(self, parser):
        """Format 'DD Mon YYYY' (abbreviated month) should parse correctly."""
        result = parser._parse_and_validate_date("17 Mar 2026")
        assert result == "2026-03-17"

    def test_parse_natural_language_invalid_month(self, parser):
        """Invalid month name should return empty string."""
        result = parser._parse_and_validate_date("InvalidMonth 17, 2026")
        assert result == ""

    # ===== Chinese/Japanese Format Tests =====

    def test_parse_chinese_date_format(self, parser):
        """Chinese format YYYY年MM月DD日 should parse correctly."""
        result = parser._parse_and_validate_date("2026年3月17日")
        assert result == "2026-03-17"

    def test_parse_japanese_date_format(self, parser):
        """Japanese format YYYY年M月D日 (single digit months/days) should parse correctly."""
        result = parser._parse_and_validate_date("2026年3月17日")
        assert result == "2026-03-17"

    def test_parse_chinese_date_with_padding(self, parser):
        """Chinese format with leading zeros should parse correctly."""
        result = parser._parse_and_validate_date("2026年03月07日")
        assert result == "2026-03-07"


class TestMetadataDetection:
    """Test metadata extraction from HTML."""

    @pytest.fixture
    def parser(self, monkeypatch):
        return _pinned_parser(monkeypatch)

    def test_detect_article_published_time_meta(self, parser):
        """Should extract date from article:published_time meta tag."""
        html = '''<html>
            <head>
                <meta property="article:published_time" content="2026-03-17T10:30:00Z">
            </head>
            <body>Content</body>
        </html>'''
        result = parser._extract_publish_date_from_metadata(html)
        assert result == "2026-03-17"

    def test_detect_og_published_time_meta(self, parser):
        """Should extract date from og:published_time meta tag."""
        html = '''<html>
            <head>
                <meta property="og:published_time" content="2026-03-17">
            </head>
            <body>Content</body>
        </html>'''
        result = parser._extract_publish_date_from_metadata(html)
        assert result == "2026-03-17"

    def test_detect_json_ld_date_published(self, parser):
        """Should extract datePublished from JSON-LD script tag."""
        html = '''<html>
            <head>
                <script type="application/ld+json">
                {"@type": "Article", "datePublished": "2026-03-17"}
                </script>
            </head>
            <body>Content</body>
        </html>'''
        result = parser._extract_publish_date_from_metadata(html)
        assert result == "2026-03-17"

    def test_detect_publish_date_meta_name(self, parser):
        """Should extract date from publish_date meta name tag."""
        html = '''<html>
            <head>
                <meta name="publish_date" content="March 17, 2026">
            </head>
            <body>Content</body>
        </html>'''
        result = parser._extract_publish_date_from_metadata(html)
        assert result == "2026-03-17"

    def test_metadata_priority_order(self, parser):
        """Should prioritize article:published_time over og:published_time."""
        html = '''<html>
            <head>
                <meta property="article:published_time" content="2026-03-17">
                <meta property="og:published_time" content="2026-03-15">
            </head>
            <body>Content</body>
        </html>'''
        result = parser._extract_publish_date_from_metadata(html)
        assert result == "2026-03-17"

    def test_metadata_not_found(self, parser):
        """Should return empty string if no metadata found."""
        html = '<html><head></head><body>Content</body></html>'
        result = parser._extract_publish_date_from_metadata(html)
        assert result == ""


class TestContentDetection:
    """Test date detection from HTML content."""

    @pytest.fixture
    def parser(self, monkeypatch):
        return _pinned_parser(monkeypatch)

    def test_detect_date_near_published_keyword_english(self, parser):
        """Should find date near 'published' keyword."""
        html = '''<html><body>
            <article>
                <h1>Article Title</h1>
                <p>Published on 2026-03-17</p>
                <p>Article content here.</p>
            </article>
        </body></html>'''
        result = parser._extract_publish_date_from_content(html)
        assert result == "2026-03-17"

    def test_detect_date_near_posted_keyword(self, parser):
        """Should find date near 'posted' keyword."""
        html = '''<html><body>
            <div class="article-info">Posted: March 17, 2026</div>
            <article>Article content</article>
        </body></html>'''
        result = parser._extract_publish_date_from_content(html)
        assert result == "2026-03-17"

    def test_detect_date_near_updated_keyword(self, parser):
        """Should find date near 'updated' keyword."""
        html = '''<html><body>
            <footer>Last updated: 2026/03/17</footer>
            <article>Article content</article>
        </body></html>'''
        result = parser._extract_publish_date_from_content(html)
        assert result == "2026-03-17"

    def test_fallback_scan_content_without_keyword(self, parser):
        """Should find date in content even without keyword."""
        html = '''<html><body>
            <article>
                <h1>Article Title</h1>
                <div class="byline">By Author | 2026-03-17</div>
                <p>Article content</p>
            </article>
        </body></html>'''
        result = parser._extract_publish_date_from_content(html)
        assert result == "2026-03-17"

    def test_content_detection_returns_first_date(self, parser):
        """Should return first valid date found."""
        html = '''<html><body>
            <article>
                Posted on 2026-03-17
                <p>Updated on 2026-03-20</p>
            </article>
        </body></html>'''
        result = parser._extract_publish_date_from_content(html)
        # Should return the first date found (2026-03-17)
        assert result == "2026-03-17"

    def test_content_detection_empty_if_no_date(self, parser):
        """Should return empty string if no date found in content."""
        html = '''<html><body>
            <article>
                <h1>Article Title</h1>
                <p>Article without any date information.</p>
            </article>
        </body></html>'''
        result = parser._extract_publish_date_from_content(html)
        assert result == ""


class TestPublishDateExtraction:
    """Integration tests for the main _extract_publish_date() method."""

    @pytest.fixture
    def parser(self, monkeypatch):
        return _pinned_parser(monkeypatch)

    def test_extract_from_metadata_prioritized(self, parser):
        """Should prioritize metadata over content."""
        html = '''<html>
            <head>
                <meta property="article:published_time" content="2026-03-17">
            </head>
            <body>
                <article>Posted on 2026-03-15</article>
            </body>
        </html>'''
        result = parser._extract_publish_date(html)
        assert result == "2026-03-17"

    def test_extract_fallback_to_content(self, parser):
        """Should fallback to content if no metadata found."""
        html = '''<html>
            <head></head>
            <body>
                <article>Published: 2026-03-17</article>
            </body>
        </html>'''
        result = parser._extract_publish_date(html)
        assert result == "2026-03-17"

    def test_extract_returns_empty_if_not_found(self, parser):
        """Should return empty string if no date found anywhere."""
        html = '''<html>
            <head></head>
            <body>
                <article>No date information here</article>
            </body>
        </html>'''
        result = parser._extract_publish_date(html)
        assert result == ""

    def test_css_selector_override_takes_precedence(self, parser):
        """CSS selector override should take precedence over auto-detection."""
        html = '''<html>
            <head>
                <meta property="article:published_time" content="2026-03-17">
            </head>
            <body>
                <article>
                    <span class="custom-date">2026-03-20</span>
                </article>
            </body>
        </html>'''

        overrides = {"date_selector": ".custom-date"}
        result = parser.parse_with_overrides(html, overrides)

        # Override should win over metadata
        assert result.get("publish_date") == "2026-03-20"
