import json
import os
import pytest

from PIL import Image

from app.utils.face_detect import detect_primary_face

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")
IMAGE_DIR = os.path.join(FIXTURES, "focal_images")
HAS_FIXTURES = os.path.isdir(IMAGE_DIR) and os.path.exists(os.path.join(FIXTURES, "focal_baseline.json")) and len(os.listdir(IMAGE_DIR)) >= 10


def _baseline(mode):
    with open(os.path.join(FIXTURES, "focal_baseline.json"), encoding="utf-8") as f:
        return [e for e in json.load(f)["entries"] if e["mode"] == mode]


@pytest.mark.skipif(not HAS_FIXTURES, reason="focal_images fixtures missing")
def test_detects_faces_on_most_face_baseline_images():
    """The client's face path fired on these 17. We should fire on most of them.

    Not all: some client detections are false positives (one locked onto a
    salmon fillet), and YuNet is expected to correctly decline those.
    """
    hits = 0
    total = 0
    for entry in _baseline("face"):
        path = os.path.join(IMAGE_DIR, entry["hash"] + ".jpg")
        if not os.path.exists(path):
            continue
        total += 1
        with Image.open(path) as image:
            image.load()
            if detect_primary_face(image):
                hits += 1
    assert total >= 15
    assert hits >= total * 0.6, f"only {hits}/{total} faces detected"


@pytest.mark.skipif(not HAS_FIXTURES, reason="focal_images fixtures missing")
def test_does_not_fire_on_most_saliency_baseline_images():
    """The client found no face in these 45. Mass false positives would be worse
    than no detector at all."""
    fires = 0
    total = 0
    for entry in _baseline("saliency"):
        path = os.path.join(IMAGE_DIR, entry["hash"] + ".jpg")
        if not os.path.exists(path):
            continue
        total += 1
        with Image.open(path) as image:
            image.load()
            if detect_primary_face(image):
                fires += 1
    assert fires <= total * 0.25, f"fired on {fires}/{total} face-free images"


def test_returns_none_for_blank_image():
    assert detect_primary_face(Image.new("RGB", (400, 300), (128, 128, 128))) is None


def test_returns_none_for_tiny_image():
    assert detect_primary_face(Image.new("RGB", (4, 4), (200, 150, 130))) is None


def test_absent_model_file_disables_permanently_and_returns_none(monkeypatch):
    import app.utils.face_detect as fd
    monkeypatch.setattr(fd, "_DETECTOR", None)
    monkeypatch.setattr(fd, "_ABSENT_LOGGED", False)
    monkeypatch.setattr(fd, "MODEL_PATH", "/non/existent/path/model.onnx")

    img = Image.new("RGB", (400, 300), (128, 128, 128))
    res1 = fd.detect_primary_face(img)
    assert res1 is None
    assert fd._DETECTOR is False

    # Second call uses cached False
    res2 = fd.detect_primary_face(img)
    assert res2 is None
    assert fd._DETECTOR is False


def test_transient_model_load_failure_retries_after_backoff(monkeypatch):
    import app.utils.face_detect as fd
    monkeypatch.setattr(fd, "_DETECTOR", None)
    monkeypatch.setattr(fd, "_LAST_LOAD_ATTEMPT", 0.0)
    monkeypatch.setattr(fd, "_LOAD_FAILED_LOGGED", False)
    monkeypatch.setattr(fd, "LOAD_RETRY_BACKOFF_SECONDS", 10.0)

    class DummyDetector:
        def setInputSize(self, size): pass
        def detect(self, img): return (0, None)

    calls = 0
    def mock_create(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Transient ONNX memory error")
        return DummyDetector()

    monkeypatch.setattr(fd.cv2.FaceDetectorYN, "create", mock_create)
    monkeypatch.setattr(fd.os.path, "exists", lambda p: True)

    img = Image.new("RGB", (400, 300), (128, 128, 128))

    # First call throws, detector remains None (not False)
    assert fd.detect_primary_face(img) is None
    assert fd._DETECTOR is None
    assert calls == 1

    # Immediate second call is within backoff -> returns None without calling create
    assert fd.detect_primary_face(img) is None
    assert calls == 1

    # Advance time past backoff -> retries create and succeeds
    monkeypatch.setattr(fd, "_LAST_LOAD_ATTEMPT", fd.time.time() - 20.0)
    assert fd.detect_primary_face(img) is None  # DummyDetector returns no faces
    assert fd._DETECTOR is not None and fd._DETECTOR is not False
    assert calls == 2


@pytest.mark.skipif(not HAS_FIXTURES, reason="focal_images fixtures missing")
def test_box_is_inside_image_bounds():
    for entry in _baseline("face"):
        path = os.path.join(IMAGE_DIR, entry["hash"] + ".jpg")
        if not os.path.exists(path):
            continue
        with Image.open(path) as image:
            image.load()
            box = detect_primary_face(image)
            if box:
                x, y, w, h = box
                assert 0 <= x and 0 <= y
                assert x + w <= image.size[0] + 1
                assert y + h <= image.size[1] + 1
