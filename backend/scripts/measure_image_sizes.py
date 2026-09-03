"""Measure how cached image size responds to dimension and JPEG quality.

Regenerates the SIZE_MULTIPLIERS table in frontend/src/components/Options.jsx,
which drives the storage projection on the Options page. Run on the host that
holds the cache (the Pi):

    python3 backend/scripts/measure_image_sizes.py

Multipliers are ratios against the 1200px/q70 baseline rather than absolute
byte counts, because the sample is re-encoded from already-compressed cached
copies. Ratios cancel most of that generational-loss bias; absolute numbers
would not. For the same reason the grid cannot exceed the current cached
dimension — the originals are gone, so an upscale has nothing to measure.
"""
import io
import os
import random

from PIL import Image

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "article_image_cache")
DIMS = [800, 1000, 1200]
QUALS = [50, 60, 70, 80, 85]
SAMPLE = 300
BASELINE = (1200, 70)


def normalize(image):
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        alpha = image.convert("RGBA")
        background = Image.new("RGB", alpha.size, (255, 255, 255))
        background.paste(alpha, mask=alpha.split()[-1])
        return background
    return image.convert("RGB")


def main():
    files = []
    for root, _, names in os.walk(CACHE):
        files.extend(os.path.join(root, n) for n in names)

    # Animated GIFs are stored raw by _prepare_cached_image, so re-encoding
    # them would measure something the settings never touch.
    encodable = [f for f in files if not f.lower().endswith(".gif")]
    print(f"cache files: {len(files)} ({len(files) - len(encodable)} animated gif, excluded)")

    random.seed(42)
    sample = random.sample(encodable, min(SAMPLE, len(encodable)))
    totals = {(d, q): 0 for d in DIMS for q in QUALS}
    measured = 0

    for path in sample:
        try:
            base = normalize(Image.open(io.BytesIO(open(path, "rb").read())))
        except Exception:
            continue
        measured += 1
        for dimension in DIMS:
            image = base.copy()
            if max(image.size) > dimension:
                image.thumbnail((dimension, dimension), Image.Resampling.LANCZOS)
            for quality in QUALS:
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
                totals[(dimension, quality)] += buffer.tell()

    baseline = totals[BASELINE] / measured
    print(f"sampled {measured} images; baseline {BASELINE[0]}px/q{BASELINE[1]} = {baseline:.0f} bytes\n")
    for dimension in DIMS:
        row = ", ".join(
            f"{q}: {totals[(dimension, q)] / measured / baseline:.3f}" for q in QUALS
        )
        print(f"  {dimension}: {{ {row} }},")


if __name__ == "__main__":
    main()
