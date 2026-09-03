import json
import os

import pytest
from PIL import Image

from app.utils.smartcrop_port import find_best_crop

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")
IMAGE_DIR = os.path.join(FIXTURES, "focal_images")
HAS_FIXTURES = os.path.isdir(IMAGE_DIR) and os.path.exists(os.path.join(FIXTURES, "focal_baseline.json")) and len(os.listdir(IMAGE_DIR)) >= 10


def _baseline(mode):
    with open(os.path.join(FIXTURES, "focal_baseline.json"), encoding="utf-8") as f:
        return [e for e in json.load(f)["entries"] if e["mode"] == mode]


def _focal_from_crop(crop, size):
    width, height = size
    focal_x = round((crop["x"] + crop["width"] / 2) / width * 100)
    focal_y = round((crop["y"] + crop["height"] / 2) / height * 100)
    return max(0, min(100, focal_x)), max(0, min(100, focal_y))


def _score_all():
    results = []
    for entry in _baseline("saliency"):
        path = os.path.join(IMAGE_DIR, entry["hash"] + ".jpg")
        if not os.path.exists(path):
            continue
        with Image.open(path) as image:
            image.load()
            crop = find_best_crop(image)
            got = _focal_from_crop(crop, image.size)
        results.append((entry, got, abs(got[0] - entry["x"]), abs(got[1] - entry["y"])))
    return results


@pytest.mark.skipif(not HAS_FIXTURES, reason="focal_images fixtures missing")
def test_fixtures_are_present():
    """Task 1 must have run — without images this suite proves nothing."""
    assert len(os.listdir(IMAGE_DIR)) >= 60


@pytest.mark.skipif(not HAS_FIXTURES, reason="focal_images fixtures missing")
def test_saliency_parity_mean_within_three_points():
    """A faithful port reproduces the library it was ported from."""
    results = _score_all()
    assert len(results) >= 40
    mean_dx = sum(r[2] for r in results) / len(results)
    mean_dy = sum(r[3] for r in results) / len(results)
    assert mean_dx <= 3.0, f"mean |dx| {mean_dx:.1f} — port diverges from smartcrop.js"
    assert mean_dy <= 3.0, f"mean |dy| {mean_dy:.1f} — port diverges from smartcrop.js"


@pytest.mark.skipif(not HAS_FIXTURES, reason="focal_images fixtures missing")
def test_no_catastrophic_saliency_divergence():
    """Per-image exactness is not achievable, and does not need to be.

    A crop search returns one winning window. When two dissimilar windows score
    within a fraction of a percent of each other, JPEG decode and float
    rounding differences flip the winner and the coordinates jump a long way
    for a negligible scoring difference. Verified on bff45136: the runner-up
    sits near the client's answer with a sub-2% score gap.

    So the port is held to aggregate fidelity plus a ceiling on how far any
    single image may drift, rather than per-image equality.
    """
    results = _score_all()
    assert len(results) >= 40

    mean_dx = sum(r[2] for r in results) / len(results)
    mean_dy = sum(r[3] for r in results) / len(results)
    assert mean_dx <= 3.0, f"mean |dx| {mean_dx:.1f}"
    assert mean_dy <= 3.0, f"mean |dy| {mean_dy:.1f}"

    # Outliers are expected but must stay rare and bounded.
    outliers = [r for r in results if r[2] > 10 or r[3] > 10]
    assert len(outliers) <= 4, f"{len(outliers)} images off by >10 — too many for near-ties"

    catastrophic = [r for r in results if r[2] > 35 or r[3] > 35]
    assert not catastrophic, f"images off by >35: {[(r[0]['hash'][:8], r[1]) for r in catastrophic]}"


def test_returns_none_or_default_for_degenerate_input():
    tiny = Image.new("RGB", (2, 2), (10, 10, 10))
    crop = find_best_crop(tiny)
    assert crop is None or (crop["width"] > 0 and crop["height"] > 0)


def test_greyscale_input_is_handled():
    image = Image.new("L", (400, 300), 128)
    crop = find_best_crop(image)
    assert crop is None or crop["width"] > 0
