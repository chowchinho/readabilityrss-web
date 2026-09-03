# Vendored model assets

## face_detection_yunet_2023mar.onnx

Face detector used by `backend/app/utils/face_detect.py`, consumed through
`cv2.FaceDetectorYN`.

| | |
|---|---|
| Source | [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) — OpenCV's own model zoo |
| Exact URL | `https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx` |
| Retrieved | 2026-08-06 |
| Size | 232,589 bytes |
| SHA-256 | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| License | MIT, per the opencv_zoo repository |

**Vendored rather than downloaded at runtime** so the Pi needs no network access to compute
focal points, and so the model cannot change underneath a deploy. The PyPI
`opencv-python-headless` wheel does **not** bundle any YuNet weights — `cv2.data` carries only
the Haar cascades — so this file has to come from somewhere, and pinning it here with a
recorded hash is the auditable option.

Measured against `backend/tests/fixtures/focal_baseline.json` at score threshold 0.6:
detects a face in 14 of the 17 images where the retired browser pipeline used face detection,
and fires on 5 of the 45 where it did not.

To verify this file still matches its source:

```bash
python -c "import hashlib;print(hashlib.sha256(open('backend/app/assets/face_detection_yunet_2023mar.onnx','rb').read()).hexdigest())"
```
