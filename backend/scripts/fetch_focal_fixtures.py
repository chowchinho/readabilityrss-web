"""Download the 62 baseline images used to test focal-point parity.

Run once. Images are cached under tests/fixtures/focal_images/ and skipped if
already present, so re-running is cheap.

Usage, from backend/:
    python scripts/fetch_focal_fixtures.py
"""

import json
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(HERE, "tests", "fixtures", "focal_baseline.json")
IMAGE_DIR = os.path.join(HERE, "tests", "fixtures", "focal_images")
BASE_URL = "https://reader.example.com/api/reader/cached-image/"


def main():
    os.makedirs(IMAGE_DIR, exist_ok=True)
    with open(BASELINE, encoding="utf-8") as f:
        entries = json.load(f)["entries"]

    fetched = skipped = failed = 0
    for entry in entries:
        path = os.path.join(IMAGE_DIR, entry["hash"] + ".jpg")
        if os.path.exists(path):
            skipped += 1
            continue
        try:
            # A User-Agent is required; the endpoint returns 403 without one.
            request = urllib.request.Request(
                BASE_URL + entry["hash"] + ".jpg",
                headers={"User-Agent": "focal-fixtures/1.0"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            with open(path, "wb") as f:
                f.write(data)
            fetched += 1
        except Exception as exc:
            failed += 1
            print(f"  FAIL {entry['hash']}: {exc}")

    print(f"fetched={fetched} skipped={skipped} failed={failed} total={len(entries)}")


if __name__ == "__main__":
    main()
