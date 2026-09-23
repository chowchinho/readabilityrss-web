"""The translated-article badge has one definition, not three."""
import pytest

from app.services import translation, translation_worker


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Override the conftest autouse fixture; these are pure unit tests."""
    yield


def test_badge_html_contains_provider():
    out = translation.badge_html("Qwen-MT-flash")
    assert "Translated by Qwen-MT-flash" in out
    assert out.startswith("<p style=")


def test_worker_badge_delegates_to_translation():
    assert translation_worker.badge_html("Google Translate") == \
        translation.badge_html("Google Translate")


def test_badge_is_detected_by_the_already_translated_check():
    content = translation.badge_html("Qwen-MT-flash") + "<p>body</p>"
    assert "Translated by" in content[:400]
