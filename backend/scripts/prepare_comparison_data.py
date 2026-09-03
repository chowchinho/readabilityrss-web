#!/usr/bin/env python3
"""Fetch URL and parse with readability and node-unfluff for comparison preview."""

import json
import sys
import os
import asyncio

# Add backend to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.app.services.parser import ReadabilityParser
import httpx


async def prepare_data():
    """Fetch and parse URL with readability and node-unfluff, save results to JSON."""
    url = "https://matcha-jp.com/tw/26948"

    try:
        # Fetch raw HTML
        print(f"Fetching URL...")
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30)
            response.encoding = 'utf-8'  # Ensure UTF-8 for Chinese
            html_content = response.text
        print(f"✓ Successfully fetched URL ({len(html_content)} bytes)")

        # Parse with readability
        print("Parsing with readability...")
        parser = ReadabilityParser()
        readability_result = parser.parse(html_content)

        # Parse with node-unfluff via API
        print("Parsing with node-unfluff...")
        async with httpx.AsyncClient() as client:
            unfluff_response = await client.post(
                'http://localhost:8001/api/parse/unfluff',
                json={'url': url}
            )
            unfluff_result = unfluff_response.json()

        # Prepare output data
        data = {
            'url': url,
            'readability': {
                'text': readability_result.get('content', ''),
                'images': readability_result.get('images', [])
            },
            'node_unfluff': {
                'text': unfluff_result.get('text', ''),
                'images': unfluff_result.get('images', [])
            }
        }

        # Get the directory where this script is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(script_dir, '..', '..', 'comparison_data.json')

        # Save to JSON file for reference
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print("✓ Data prepared successfully")
        print(f"  - Readability text: {len(data['readability']['text'])} chars, {len(data['readability']['images'])} images")
        print(f"  - Node-unfluff text: {len(data['node_unfluff']['text'])} chars, {len(data['node_unfluff']['images'])} images")
        print(f"  - Saved to: {output_path}")

        return data

    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == '__main__':
    asyncio.run(prepare_data())
