import json
import re
import os
from pathlib import Path


def escape_js_string(s):
    """Escape string for JavaScript."""
    s = s.replace('\\', '\\\\')  # Escape backslashes first
    s = s.replace('"', '\\"')
    s = s.replace('\n', '\\n')
    s = s.replace('\r', '\\r')
    s = s.replace('\t', '\\t')
    return s


def main():
    # Get the project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent

    # Paths
    comparison_data_path = project_root / 'comparison_data.json'
    html_path = project_root / 'frontend' / 'public' / 'comparison_preview.html'

    # Check if comparison data exists
    if not comparison_data_path.exists():
        print("⚠ comparison_data.json not found. Using empty data.")
        data = {
            'readability': {'text': '', 'images': []},
            'node_unfluff': {'text': '', 'images': []}
        }
    else:
        # Load comparison data
        with open(comparison_data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

    # Extract content
    readability_text = data.get('readability', {}).get('text', '')
    readability_images = data.get('readability', {}).get('images', [])
    unfluff_text = data.get('node_unfluff', {}).get('text', '')
    unfluff_images = data.get('node_unfluff', {}).get('images', [])

    # Read HTML template
    with open(html_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    # Build replacement script
    replacement_script = f'''        const parserData = {{
            readabilityText: "{escape_js_string(readability_text)}",
            readabilityImages: {json.dumps(readability_images, ensure_ascii=False)},
            nodeUnfluffText: "{escape_js_string(unfluff_text)}",
            nodeUnfluffImages: {json.dumps(unfluff_images, ensure_ascii=False)}
        }};'''

    # Find and replace the data section
    pattern = r'        const parserData = \{[^}]*\};'
    html_content = re.sub(pattern, replacement_script, html_content, flags=re.DOTALL)

    # Write updated HTML with UTF-8 encoding
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print("✓ HTML updated with embedded data")


if __name__ == '__main__':
    main()
