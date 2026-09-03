from PIL import Image, ImageDraw

from app.utils.focal_point import DEFAULT_FOCAL, compute_focal_point


def test_flat_image_returns_centre():
    """No edges and no skin means no signal — must not drift to a corner.

    This is the FIND_EDGES border-artifact regression test.
    """
    image = Image.new("RGB", (300, 220), (128, 128, 128))
    assert compute_focal_point(image) == DEFAULT_FOCAL


def test_detail_still_pulls_focal_toward_it():
    """Contract check, deliberately loose — exact placement is smartcrop's business."""
    image = Image.new("RGB", (600, 400), (255, 255, 255))
    ImageDraw.Draw(image).rectangle([30, 30, 200, 170], fill=(0, 0, 0))
    focal_x, focal_y = compute_focal_point(image)
    assert focal_x < 55
    assert focal_y < 55


def test_tiny_image_returns_centre():
    assert compute_focal_point(Image.new("RGB", (2, 2), (10, 200, 10))) == DEFAULT_FOCAL


def test_output_always_within_bounds():
    image = Image.new("RGB", (64, 40), (255, 255, 255))
    ImageDraw.Draw(image).rectangle([0, 0, 4, 4], fill=(0, 0, 0))
    focal_x, focal_y = compute_focal_point(image)
    assert 0 <= focal_x <= 100
    assert 0 <= focal_y <= 100


def test_greyscale_input_is_handled():
    """Cached originals are not always RGB; the function must convert, not raise."""
    image = Image.new("L", (80, 80), 128)
    focal_x, focal_y = compute_focal_point(image)
    assert 0 <= focal_x <= 100
    assert 0 <= focal_y <= 100
