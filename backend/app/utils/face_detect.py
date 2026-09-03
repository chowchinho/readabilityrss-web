"""Face detection using OpenCV's YuNet ONNX model.

Uses the vendored model in backend/app/assets/face_detection_yunet_2023mar.onnx.
Returns the largest-area face in original image coordinates, or None.
"""

import logging
import os
import time
import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets",
    "face_detection_yunet_2023mar.onnx",
)

# _DETECTOR is None (uninitialized/retryable), False (permanently disabled: model file absent),
# or a loaded cv2.FaceDetectorYN instance.
# NOTE: cv2.FaceDetectorYN is reused across calls. OpenCV DNN objects are not guaranteed
# thread-safe if article processing is parallelized in the future.
_DETECTOR = None
_LAST_LOAD_ATTEMPT = 0.0
_LOAD_FAILED_LOGGED = False
_ABSENT_LOGGED = False
LOAD_RETRY_BACKOFF_SECONDS = 60.0


def _get_detector():
    global _DETECTOR, _LAST_LOAD_ATTEMPT, _LOAD_FAILED_LOGGED, _ABSENT_LOGGED
    if _DETECTOR is False:
        return None
    if _DETECTOR is not None:
        return _DETECTOR

    if not os.path.exists(MODEL_PATH):
        if not _ABSENT_LOGGED:
            logger.error("Face detection model file genuinely absent: %s. Face detection disabled.", MODEL_PATH)
            _ABSENT_LOGGED = True
        _DETECTOR = False
        return None

    now = time.time()
    if _LAST_LOAD_ATTEMPT and (now - _LAST_LOAD_ATTEMPT < LOAD_RETRY_BACKOFF_SECONDS):
        return None

    _LAST_LOAD_ATTEMPT = now
    try:
        # Score threshold 0.7 was chosen by sweeping 0.6/0.7/0.8/0.9 against the baseline:
        # 0.7 maintains identical face accuracy to 0.6 (mean |dy| 6.13, 0 images > 15)
        # while cutting false fires on the saliency baseline from 6/45 to 4/45.
        # Thresholds >= 0.8 start losing real faces.
        _DETECTOR = cv2.FaceDetectorYN.create(
            MODEL_PATH,
            "",
            (320, 320),
            score_threshold=0.7,
            nms_threshold=0.3,
            top_k=500,
        )
        _LOAD_FAILED_LOGGED = False
        _LAST_LOAD_ATTEMPT = 0.0
        return _DETECTOR
    except Exception as e:
        _DETECTOR = None
        if not _LOAD_FAILED_LOGGED:
            logger.error("Failed to load FaceDetectorYN model from %s: %s", MODEL_PATH, e, exc_info=True)
            _LOAD_FAILED_LOGGED = True
        return None


def detect_primary_face(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Detect primary (largest area) face in image.

    Returns (x, y, width, height) in original image coordinates, or None if no
    face was detected or detection failed.
    """
    detector = _get_detector()
    if detector is None or image is None:
        return None

    try:
        w, h = image.size
        if w < 10 or h < 10:
            return None

        max_dim = max(w, h)
        scale = 320.0 / max_dim if max_dim > 320 else 1.0
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))

        img_bgr = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        resized = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA)

        detector.setInputSize((nw, nh))
        _, faces = detector.detect(resized)

        if faces is None or len(faces) == 0:
            return None

        # Return largest area face
        best = max(faces, key=lambda f: f[2] * f[3])
        x = best[0] / scale
        y = best[1] / scale
        fw = best[2] / scale
        fh = best[3] / scale

        # Clamp box to image boundaries
        x_clamped = max(0, min(w - 1, int(round(x))))
        y_clamped = max(0, min(h - 1, int(round(y))))
        fw_clamped = max(1, min(w - x_clamped, int(round(fw))))
        fh_clamped = max(1, min(h - y_clamped, int(round(fh))))

        return (x_clamped, y_clamped, fw_clamped, fh_clamped)
    except Exception as e:
        logger.debug("Face detection failed for image: %s", e)
        return None
