"""Parser parity test harness.

This harness asserts that ReadabilityParser.extract_with_images produces byte-for-byte
identical extraction output compared against committed reference expectations in tests/fixtures/parity/.

Regenerating expectations (via `REGENERATE_PARITY_FIXTURES=1 pytest tests/test_parser_parity.py`)
is a deliberate architectural decision to change extraction behavior across the app,
never a routine fix to turn a failing test green.
"""
import json
import os
from datetime import datetime
from pathlib import Path
import pytest
from app.services.parser import ReadabilityParser

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PARITY_DIR = FIXTURES_DIR / "parity"
CORPUS_DIR = FIXTURES_DIR / "parity_corpus"
FIXED_NOW = datetime(2026, 3, 20)

HAS_CORPUS = CORPUS_DIR.is_dir() and len(list(CORPUS_DIR.glob("*.html"))) >= 10


def get_html_fixtures():
    """Return all base HTML fixture paths in tests/fixtures."""
    return sorted(FIXTURES_DIR.glob("*.html"))


def get_corpus_fixtures():
    """Return all parity corpus HTML fixture paths in tests/fixtures/parity_corpus."""
    if not CORPUS_DIR.is_dir():
        return []
    return sorted(CORPUS_DIR.glob("*.html"))


@pytest.mark.parametrize("html_path", get_html_fixtures(), ids=lambda p: p.stem)
def test_parser_parity(html_path):
    """Assert extract_with_images output matches committed JSON expectation."""
    expected_path = PARITY_DIR / f"{html_path.stem}.json"
    html_content = html_path.read_text(encoding="utf-8")
    base_url = f"https://example.com/{html_path.stem}"

    parser = ReadabilityParser()
    parser._now = lambda: FIXED_NOW
    actual_result = parser.extract_with_images(html_content, base_url=base_url)

    if os.environ.get("REGENERATE_PARITY_FIXTURES") == "1":
        PARITY_DIR.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(json.dumps(actual_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip(f"Regenerated parity fixture: {expected_path.name}")

    assert expected_path.exists(), (
        f"Missing parity expectation file: {expected_path}. "
        f"Run with REGENERATE_PARITY_FIXTURES=1 to generate initial fixture."
    )

    expected_result = json.loads(expected_path.read_text(encoding="utf-8"))
    assert actual_result == expected_result


@pytest.mark.skipif(not HAS_CORPUS, reason="parity_corpus fixtures missing")
# An empty corpus makes pytest pass a NotSetType placeholder to the id function,
# so the lambda must tolerate it or collection fails before skipif can apply.
@pytest.mark.parametrize("html_path", get_corpus_fixtures(), ids=lambda p: getattr(p, "stem", "no-corpus"))
def test_parser_parity_corpus(html_path):
    """Assert extract_with_images output on parity corpus matches committed JSON expectation."""
    expected_path = PARITY_DIR / f"{html_path.stem}.json"
    html_content = html_path.read_text(encoding="utf-8")

    # Read real origin URL from metadata if present
    meta_path = html_path.with_name(f"{html_path.stem}.meta.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            base_url = meta.get("url", f"https://example.com/{html_path.stem}")
        except Exception:
            base_url = f"https://example.com/{html_path.stem}"
    else:
        base_url = f"https://example.com/{html_path.stem}"

    parser = ReadabilityParser()
    parser._now = lambda: FIXED_NOW
    actual_result = parser.extract_with_images(html_content, base_url=base_url)

    if os.environ.get("REGENERATE_PARITY_FIXTURES") == "1":
        PARITY_DIR.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(json.dumps(actual_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip(f"Regenerated parity fixture: {expected_path.name}")

    assert expected_path.exists(), (
        f"Missing parity expectation file: {expected_path}. "
        f"Run with REGENERATE_PARITY_FIXTURES=1 to generate initial fixture."
    )

    expected_result = json.loads(expected_path.read_text(encoding="utf-8"))
    assert actual_result == expected_result
