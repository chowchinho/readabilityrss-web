"""Manual integration test on real websites."""
import requests
from app.services.parser import ReadabilityParser
from datetime import datetime, timedelta

parser = ReadabilityParser()

# Test cases: (URL, expected_date_pattern_or_none)
test_cases = [
    # News sites - should have metadata or clear date in content
    ("https://www.bbc.com/news", "metadata or content detection"),
    # Tech news - usually has good metadata
    ("https://www.theverge.com", "metadata or content detection"),
    # Blog - may have various date formats
    ("https://blog.google", "metadata or content detection"),
]

print("=" * 60)
print("INTEGRATION TEST: Date Detection on Real Websites")
print("=" * 60)

for url, description in test_cases:
    try:
        print(f"\nTesting: {url}")
        print(f"Expected: {description}")

        response = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        result = parser.parse(response.text)
        detected_date = result.get("publish_date", "")

        if detected_date:
            # Verify it's a valid ISO date
            try:
                date_obj = datetime.strptime(detected_date, "%Y-%m-%d")
                # Check if it's within reasonable range (last 90 days to future)
                days_old = (datetime.now() - date_obj).days
                if -7 <= days_old <= 90:
                    print(f"✓ PASS - Detected: {detected_date} ({days_old} days old)")
                else:
                    print(f"⚠ WARNING - Date found but outside expected range: {detected_date} ({days_old} days old)")
            except ValueError:
                print(f"✗ FAIL - Invalid date format: {detected_date}")
        else:
            print(f"⚠ INFO - No date detected (may have restrictive metadata)")

    except requests.exceptions.RequestException as e:
        print(f"⚠ SKIP - Network error: {str(e)}")
    except Exception as e:
        print(f"✗ ERROR - Unexpected error: {str(e)}")

print("\n" + "=" * 60)
print("Integration test complete")
print("=" * 60)
