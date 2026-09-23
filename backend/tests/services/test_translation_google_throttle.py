"""Pacing and backoff for Google's free endpoint.

Measured 2026-09-21: client=dict-chrome-ex absorbed 30 batched calls in 8.3s when it
was fresh, but a backfill running flat out alongside a refresh-all walled the IP
anyway, and the scheduler then spent its time retrying into a 429 wall. Two rules
come out of that — space the calls out, and when the wall does appear, stop knocking
until it has had time to lift.
"""
from unittest.mock import patch, Mock
import pytest

from app.services import translation


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Override the conftest autouse fixture — these are pure unit tests."""
    yield


@pytest.fixture(autouse=True)
def reset_throttle():
    translation._google_throttle_reset()
    yield
    translation._google_throttle_reset()


def _ok(segments=("译文",)):
    r = Mock()
    r.status_code = 200
    r.text = ""
    r.json = Mock(return_value=[[[s, "", None, None, 3] for s in segments], None, "ja"])
    return r


def _429():
    r = Mock()
    r.status_code = 429
    r.text = "<!DOCTYPE html><title>Sorry...</title>"
    return r


def test_calls_are_spaced_by_the_minimum_interval():
    slept = []
    clock = [1000.0]

    def fake_sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    with patch.object(translation.requests, "post", return_value=_ok()), \
         patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
         patch.object(translation.time, "sleep", side_effect=fake_sleep):
        translation._google_call("one", "zh-TW")
        translation._google_call("two", "zh-TW")

    # The second call waits out the remainder of the interval; the first does not.
    assert any(abs(s - translation.GOOGLE_MIN_INTERVAL_SECONDS) < 0.01 for s in slept)


def test_a_429_starts_a_cooldown_that_fails_fast():
    """Retrying into the wall wastes the scheduler's time and deepens the block."""
    clock = [1000.0]
    with patch.object(translation.requests, "post", return_value=_429()) as post, \
         patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
         patch.object(translation.time, "sleep"):
        with pytest.raises(translation.GoogleError):
            translation._google_call("x", "zh-TW")
        calls_during_first = post.call_count

        with pytest.raises(translation.GoogleError) as second:
            translation._google_call("y", "zh-TW")

    assert post.call_count == calls_during_first, "cooled down, so no new request"
    assert "cooling down" in str(second.value).lower()


def test_the_cooldown_expires():
    clock = [1000.0]
    with patch.object(translation.requests, "post", return_value=_429()), \
         patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
         patch.object(translation.time, "sleep"):
        with pytest.raises(translation.GoogleError):
            translation._google_call("x", "zh-TW")

    clock[0] += translation.GOOGLE_COOLDOWN_SECONDS + 1
    with patch.object(translation.requests, "post", return_value=_ok()), \
         patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
         patch.object(translation.time, "sleep"):
        assert translation._google_call("x", "zh-TW") == "译文"


def test_repeated_walls_back_off_further():
    """A wall that comes straight back means the first cooldown was too short."""
    clock = [1000.0]

    def wall_once():
        """Returns the cooldown it set, read against the same fake clock."""
        with patch.object(translation.requests, "post", return_value=_429()), \
             patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(translation.time, "sleep"):
            with pytest.raises(translation.GoogleError):
                translation._google_call("x", "zh-TW")
            return translation._google_cooldown_remaining()

    first = wall_once()
    clock[0] += first + 1
    second = wall_once()

    assert second > first


def test_a_success_clears_the_backoff_escalation():
    clock = [1000.0]
    with patch.object(translation.requests, "post", return_value=_429()), \
         patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
         patch.object(translation.time, "sleep"):
        with pytest.raises(translation.GoogleError):
            translation._google_call("x", "zh-TW")

    clock[0] += translation.GOOGLE_COOLDOWN_SECONDS + 1
    with patch.object(translation.requests, "post", return_value=_ok()), \
         patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
         patch.object(translation.time, "sleep"):
        translation._google_call("x", "zh-TW")

    with patch.object(translation.requests, "post", return_value=_429()), \
         patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
         patch.object(translation.time, "sleep"):
        with pytest.raises(translation.GoogleError):
            translation._google_call("x", "zh-TW")

    assert translation._google_cooldown_remaining() <= translation.GOOGLE_COOLDOWN_SECONDS


def test_a_cooled_down_article_is_left_untranslated_not_damaged():
    """The whole point of the cooldown is that it is cheap to hit."""
    clock = [1000.0]
    with patch.object(translation.requests, "post", return_value=_429()), \
         patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
         patch.object(translation.time, "sleep"):
        out, provider = translation.translate_html("<p>本文です</p>", "zh-TW", translator="google")

    assert out == "<p>本文です</p>"
    assert provider == "none"
    assert "Translation Error" not in out
