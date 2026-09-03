"""One-off cleanup of animated GIFs cached before they were re-encoded.

Animated GIFs used to be stored raw, so 0.3% of cache files held 21% of the
bytes (individual files reached 50 MB). _prepare_cached_image now re-encodes
them to a first-frame JPEG; this script applies the same treatment to what is
already on disk:

  * GIFs no article references are deleted outright.
  * Referenced GIFs are converted to {hash}.jpg and the article HTML is
    rewritten to point at the new file, so nothing 404s.

Dry run by default. Run on the host holding the cache (the Pi):

    python3 backend/scripts/convert_cached_gifs.py            # report only
    python3 backend/scripts/convert_cached_gifs.py --apply    # make changes

Back up feeds.db before using --apply: article content is rewritten in place.
"""
import argparse
import io
import os
import re
import sqlite3
import sys

from PIL import Image

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_DIR = os.path.join(DATA_DIR, "article_image_cache")
DB_PATH = os.path.join(DATA_DIR, "feeds.db")
HASH_RE = re.compile(r"/api/reader/cached-image/([0-9a-f]+)")
DEFAULT_DIMENSION = 1200
DEFAULT_QUALITY = 70


def encode_settings(con) -> tuple[int, int]:
    values = dict(con.execute("SELECT key, value FROM system_settings").fetchall())
    def get(key, fallback):
        try:
            return int(values.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
    return get("image_max_dimension", DEFAULT_DIMENSION), get("image_jpeg_quality", DEFAULT_QUALITY)


def normalize(image):
    """Mirrors _normalize_image_mode in article_image_cache.py."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        alpha = image.convert("RGBA")
        background = Image.new("RGB", alpha.size, (255, 255, 255))
        background.paste(alpha, mask=alpha.split()[-1])
        return background
    return image.convert("RGB")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="perform the changes")
    args = parser.parse_args()

    con = sqlite3.connect(DB_PATH, timeout=30)
    max_dimension, quality = encode_settings(con)
    print(f"encode settings: {max_dimension}px / q{quality}")

    referenced = set()
    for content, main_image in con.execute("SELECT content, main_image FROM feed_articles"):
        for blob in (content, main_image):
            if blob:
                referenced.update(HASH_RE.findall(blob))

    gifs = []
    for root, _, names in os.walk(CACHE_DIR):
        for name in names:
            if name.lower().endswith(".gif"):
                path = os.path.join(root, name)
                try:
                    gifs.append((path, name.rsplit(".", 1)[0], os.path.getsize(path)))
                except OSError:
                    pass

    orphans = [g for g in gifs if g[1] not in referenced]
    live = [g for g in gifs if g[1] in referenced]
    print(f"{len(gifs)} gifs: {len(orphans)} orphaned "
          f"({sum(g[2] for g in orphans) / 1024 / 1024:.0f} MB), "
          f"{len(live)} referenced ({sum(g[2] for g in live) / 1024 / 1024:.0f} MB)")

    if not args.apply:
        print("\ndry run — pass --apply to delete orphans and convert the rest")
        return

    freed = 0
    for path, _, size in orphans:
        try:
            os.remove(path)
            freed += size
        except OSError as err:
            print(f"  skip {os.path.basename(path)}: {err}")

    converted = failed = 0
    for path, url_hash, size in live:
        jpg_path = os.path.join(CACHE_DIR, f"{url_hash}.jpg")
        try:
            with open(path, "rb") as handle:
                image = normalize(Image.open(io.BytesIO(handle.read())))
            if max(image.size) > max_dimension:
                image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            image.save(jpg_path, format="JPEG", quality=quality, optimize=True)
        except Exception as err:
            print(f"  failed {url_hash[:12]}: {err}")
            failed += 1
            continue

        # Only drop the GIF once the JPEG exists and the HTML points at it,
        # so an interrupted run never leaves an article referencing nothing.
        con.execute(
            "UPDATE feed_articles SET content = REPLACE(content, ?, ?) WHERE content LIKE ?",
            (f"{url_hash}.gif", f"{url_hash}.jpg", f"%{url_hash}.gif%"),
        )
        con.execute(
            "UPDATE feed_articles SET main_image = REPLACE(main_image, ?, ?) WHERE main_image LIKE ?",
            (f"{url_hash}.gif", f"{url_hash}.jpg", f"%{url_hash}.gif%"),
        )
        con.commit()
        try:
            os.remove(path)
            freed += size - os.path.getsize(jpg_path)
        except OSError as err:
            print(f"  kept {url_hash[:12]}: {err}")
        converted += 1

    print(f"\ndeleted {len(orphans)} orphans, converted {converted} referenced"
          f"{f', {failed} failed' if failed else ''}")
    print(f"reclaimed {freed / 1024 / 1024:.0f} MB")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
