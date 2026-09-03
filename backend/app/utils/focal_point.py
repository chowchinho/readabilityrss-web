"""Focal point detection for cached article images.

Ports the browser pipeline the reader used to run per client: face detection
first, smartcrop saliency when no face is found, dead centre when neither
produces an answer. Computed once at cache time so the reader can position the
crop on first paint.
"""

import logging
from PIL import Image

from .face_detect import detect_primary_face
from .smartcrop_port import find_best_crop

logger = logging.getLogger(__name__)

DEFAULT_FOCAL = (50, 50)


def _to_percent(centre_x: float, centre_y: float, size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    if width <= 0 or height <= 0:
        return DEFAULT_FOCAL
    focal_x = round(centre_x / width * 100)
    focal_y = round(centre_y / height * 100)
    return (max(0, min(100, focal_x)), max(0, min(100, focal_y)))


def compute_focal_point(image: Image.Image) -> tuple[int, int]:
    """Return (focal_x, focal_y) as integer percentages of width and height.

    Falls back to dead centre for anything unreadable — the same result the CSS
    default produces, so a miss is never visible.
    """
    try:
        size = image.size
        if size[0] < 3 or size[1] < 3:
            logger.debug("Image too small for focal point computation (%sx%s), using default", size[0], size[1])
            return DEFAULT_FOCAL
    except Exception as e:
        logger.debug("Failed to inspect image size for focal point computation: %s", e)
        return DEFAULT_FOCAL

    try:
        face = detect_primary_face(image)
        if face:
            x, y, width, height = face
            return _to_percent(x + width / 2, y + height / 2, size)
    except Exception as e:
        logger.debug("Face detection step raised exception: %s", e)

    try:
        crop = find_best_crop(image)
        if crop:
            return _to_percent(
                crop["x"] + crop["width"] / 2,
                crop["y"] + crop["height"] / 2,
                size,
            )
    except Exception as e:
        logger.debug("Smartcrop step raised exception: %s", e)

    logger.debug("No face or saliency crop produced; falling back to default focal point (50, 50)")
    return DEFAULT_FOCAL
