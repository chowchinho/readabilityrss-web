"""Compute focal points for images cached before focal detection existed.

Run once after deploying. Safe to re-run and safe to interrupt: images that
already have a stored focal point are skipped, so a second run resumes rather
than repeating.

Usage, from the project root:
    python -m backend.scripts.backfill_focal_points
"""

import asyncio
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.database import db  # noqa: E402
from backend.app.services.article_image_cache import ARTICLE_IMAGE_CACHE_DIR  # noqa: E402
from backend.app.utils.focal_point import compute_focal_point  # noqa: E402

BATCH_SIZE = 200


async def main():
    await db.init()
    try:
        filenames = sorted(os.listdir(ARTICLE_IMAGE_CACHE_DIR))
        print(f"Found {len(filenames)} cached files")

        processed = 0
        skipped = 0
        failed = 0

        for start in range(0, len(filenames), BATCH_SIZE):
            batch = filenames[start:start + BATCH_SIZE]
            stems = [name.rsplit(".", 1)[0] for name in batch]
            existing = await db.get_focal_points(stems)

            for name, stem in zip(batch, stems):
                if stem in existing:
                    skipped += 1
                    continue
                path = os.path.join(ARTICLE_IMAGE_CACHE_DIR, name)
                try:
                    with Image.open(path) as image:
                        focal = await asyncio.to_thread(compute_focal_point, image)
                    await db.set_focal_point(stem, focal[0], focal[1])
                    processed += 1
                except Exception as exc:
                    failed += 1
                    print(f"  skip {name}: {exc}")

            done = start + len(batch)
            print(f"  {done}/{len(filenames)} — computed {processed}, skipped {skipped}, failed {failed}")

        print(f"Done. computed={processed} skipped={skipped} failed={failed}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
