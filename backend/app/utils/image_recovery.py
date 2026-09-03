"""Image recovery from HTML content with boilerplate filtering."""
import re
from typing import List, Optional, Dict
from bs4 import BeautifulSoup, Tag
from .boilerplate_filter import BoilerplateFilter
from .lazy_images import LAZY_IMAGE_ATTRIBUTES, SRCSET_ATTRIBUTES, extract_srcset_candidate


class ImageRecovery:
    """Recovers images from HTML containers with intelligent filtering."""

    # Minimum image size to avoid tiny icons and avatars.
    # Articles usually have images with width/height >= 200px.
    MIN_IMAGE_SIZE = 150

    # Common tracking pixel/boilerplate filenames and URL patterns
    BOILERPLATE_IMAGE_PATTERNS = {
        'pixel', 'tracker', 'beacon', 'spacer',
        'tracking', '1x1', 'clear', 'blank',
        'invisible', 'transparent', 'dot', 'shim',
        'cleardot', 'trans', 'telemetry',
        'avatar', 'logo', 'badge',
        'author', 'profile', 'writer',
        'related', 'recommend', 'social', 'share',
        'resize/100x',  # Matcha style small thumbnails/icons
        'placeholder', 'bg-', 'background',
        'button', 'matcha-news', 'chiachia', 'jia'
    }

    # Domains that should NEVER be considered boilerplate (CDNs, product hosts)
    TRUSTED_IMAGE_DOMAINS = {
        'cloudfront.net', 'akamaihd.net', 'fastly.net', 'cloudinary.com',
        'imgix.net', 'wp.com', 'googleusercontent.com', 'amazonaws.com'
    }

    def __init__(self, boilerplate_filter: BoilerplateFilter = None) -> None:
        """
        Initialize ImageRecovery.

        Args:
            boilerplate_filter: BoilerplateFilter instance for filtering ads/sidebars
        """
        self.boilerplate_filter = boilerplate_filter or BoilerplateFilter()

    def _get_image_url(self, img: Tag) -> str:
        """
        Get image URL from various possible attributes.

        Priority: data-src > data-lazy-src > src

        Args:
            img: Image tag element

        Returns:
            Image URL string or empty string if not found
        """
        for attr in LAZY_IMAGE_ATTRIBUTES:
            if img.get(attr):
                return img.get(attr)

        for attr in SRCSET_ATTRIBUTES:
            candidate = self._extract_srcset_candidate(img.get(attr, ''))
            if candidate:
                return candidate

        return img.get('src') or ''

    @staticmethod
    def _extract_srcset_candidate(srcset: str) -> str:
        return extract_srcset_candidate(srcset)

    def extract_images(
        self,
        container: Tag,
        apply_boilerplate_filter: bool = True
    ) -> List[Dict]:
        """
        Extract images from a content container with structural context.

        Args:
            container: BeautifulSoup Tag to search within
            apply_boilerplate_filter: Whether to filter boilerplate images

        Returns:
            List of dicts with image metadata and positional hints
        """
        if not container:
            return []

        # Find all images in the container
        all_images = container.find_all('img')

        # Apply boilerplate filtering
        if apply_boilerplate_filter:
            all_images = self.boilerplate_filter.filter_content_images(all_images)

        content_images = []
        for idx, img in enumerate(all_images):
            image_data = self._extract_image_metadata(img)
            if image_data and not self._is_boilerplate_image_attrs(img):
                # Add contextual hints for merging
                # We record the text immediately preceding and following the image in the tree
                # We use a broader search to find text even if there are formatting tags
                
                def get_context_text(node, direction='prev', limit=200):
                    text = ""
                    curr = node
                    while curr and len(text) < limit:
                        # Move in the tree
                        if direction == 'prev':
                            # Get previous sibling or parent's previous sibling
                            if curr.previous_sibling:
                                curr = curr.previous_sibling
                                if hasattr(curr, 'get_text'):
                                    text = curr.get_text() + text
                                elif isinstance(curr, str):
                                    text = curr + text
                            else:
                                curr = curr.parent
                                if not curr or curr.name in ['body', 'html', '[document]']: break
                        else:
                            if curr.next_sibling:
                                curr = curr.next_sibling
                                if hasattr(curr, 'get_text'):
                                    text = text + curr.get_text()
                                elif isinstance(curr, str):
                                    text = text + curr
                            else:
                                curr = curr.parent
                                if not curr or curr.name in ['body', 'html', '[document]']: break
                    return text.strip()

                prev_text = get_context_text(img, 'prev')
                next_text = get_context_text(img, 'next')

                image_data['anchor_text_prev'] = prev_text[-100:]
                image_data['anchor_text_next'] = next_text[:100]
                image_data['index'] = idx
                content_images.append(image_data)

        return content_images

    def merge_content_with_images(self, content_html: str, recovered_images: List[Dict], title: str = None) -> str:
        """
        Interleave recovered images back into the content HTML using proximity to content text.
        """
        if not recovered_images:
            return content_html

        soup = BeautifulSoup(content_html, 'html.parser')
        # Standardize for comparison
        existing_urls = {img.get('src') for img in soup.find_all('img')}
        
        # We only want to merge images that are NOT already in the content
        images_to_merge = [img for img in recovered_images if img['url'] not in existing_urls]
        if not images_to_merge:
            return content_html

        # Pre-process content text and string nodes for faster matching
        content_text = soup.get_text()
        content_lines = []
        for line in content_text.split('\n'):
            clean = ' '.join(line.strip().split())
            if len(clean) > 5:
                content_lines.append(clean)

        # Pre-extract and normalize text nodes once to avoid O(images * nodes * splits)
        text_nodes = [
            (node, ' '.join(node.strip().split()))
            for node in soup.find_all(string=True)
        ]

        # Normalize title for matching if provided
        norm_title = ' '.join(title.split()) if title else ""

        for img_data in images_to_merge:
            anchor_prev = img_data.get('anchor_text_prev', '').strip()
            anchor_next = img_data.get('anchor_text_next', '').strip()
            inserted = False
            
            # 1. Try matching previous text (anchor_prev)
            if len(anchor_prev) > 15:
                norm_anchor = ' '.join(anchor_prev.split())
                target_substr = norm_anchor[-20:]
                for node, norm_node_text in text_nodes:
                    if target_substr in norm_node_text:
                        new_img = soup.new_tag('img', src=img_data['url'], alt=img_data.get('alt', ''))
                        target = node.parent if node.parent.name in ['p', 'div', 'span', 'h1', 'h2', 'h3'] else node
                        target.insert_after(new_img)
                        inserted = True
                        break
            
            # 2. Try matching next text if prev failed
            if not inserted and len(anchor_next) > 15:
                norm_anchor = ' '.join(anchor_next.split())
                target_substr = norm_anchor[:20]
                for node, norm_node_text in text_nodes:
                    if target_substr in norm_node_text:
                        new_img = soup.new_tag('img', src=img_data['url'], alt=img_data.get('alt', ''))
                        target = node.parent if node.parent.name in ['p', 'div', 'span', 'h1', 'h2', 'h3'] else node
                        target.insert_before(new_img)
                        inserted = True
                        break
            
            # 3. Special Case: Lead/Header Images
            # If the image is among the first few found AND its context matches the TITLE,
            # or it precedes the first content paragraph, prepend it.
            if not inserted and img_data['index'] < 5:
                # Combine prev and next context to look for the title
                combined_context = ' '.join((anchor_prev + " " + anchor_next).split())
                
                # Check 1: Does context contain substantial part of the title?
                title_match = norm_title and len(norm_title) > 10 and (norm_title[:20] in combined_context or norm_title[-20:] in combined_context)
                
                # Check 2: Does it match the very first line of content?
                first_content_match = len(anchor_next) > 15 and content_lines and anchor_next[:20] in content_lines[0]

                if title_match or first_content_match:
                    new_img = soup.new_tag('img', src=img_data['url'], alt=img_data.get('alt', ''))
                    # Prepend to the first block element if possible, or just the body
                    target = soup.find(['p', 'div', 'h1', 'h2']) or soup
                    if target.name in ['p', 'div', 'h1', 'h2']:
                        target.insert_before(new_img)
                    else:
                        target.insert(0, new_img)
                    inserted = True

        return str(soup)

    def _extract_image_metadata(self, img: Tag) -> Optional[Dict]:
        """
        Extract metadata from an image tag.

        Handles multiple source attributes:
        - src: direct image URL
        - data-src: lazy-loaded primary
        - data-lazy-src: lazy-loaded fallback

        Args:
            img: Image tag element

        Returns:
            Dict with image metadata or None if no valid source
        """
        # Get image URL (try multiple attributes)
        url = self._get_image_url(img)

        # Skip if no URL or empty
        if not url or url.startswith('data:'):
            return None

        return {
            'url': url,
            'alt': img.get('alt', '').strip(),
            'title': img.get('title', '').strip(),
            'src': img.get('src', ''),
            'srcset': img.get('srcset', ''),
        }

    def _is_boilerplate_image_attrs(self, img: Tag) -> bool:
        """
        Check if an image tag attributes indicate boilerplate (ad, icon, author thumb).

        Uses heuristics including:
        - URL patterns (resize strings, keywords)
        - Alt text / title keywords
        - Dimensions (if available)

        Args:
            img: Image tag element

        Returns:
            True if likely boilerplate, False otherwise
        """
        url = self._get_image_url(img).lower()
        if not url:
            return True

        # Check if URL is from a trusted domain (bypass boilerplate checks)
        for domain in self.TRUSTED_IMAGE_DOMAINS:
            if domain in url:
                return False

        # 1. Check URL patterns and keywords
        for pattern in self.BOILERPLATE_IMAGE_PATTERNS:
            if pattern in url:
                return True

        # 2. Check Alt text and Title for boilerplate keywords
        alt = img.get('alt', '').lower()
        title = img.get('title', '').lower()
        combined_text = alt + " " + title
        
        BOILERPLATE_TEXT_KEYWORDS = {
            'ad', 'advertisement', 'promo', 'sponsored',
            'icon', 'avatar', 'profile', 'writer', 'author',
            'share', 'social', 'related', 'recommended'
        }
        
        for keyword in BOILERPLATE_TEXT_KEYWORDS:
            if re.search(r'\b' + re.escape(keyword) + r'\b', combined_text, re.I):
                # Be careful not to filter too aggressively on short keywords
                # But icons usually have very simple alt/title
                if len(combined_text) < 50:
                    return True

        # 3. Check dimensions
        try:
            width = int(img.get('width', '0') or 0)
            height = int(img.get('height', '0') or 0)

            # Standard article images are usually fairly large
            if width > 0 and width < self.MIN_IMAGE_SIZE:
                return True
            if height > 0 and height < self.MIN_IMAGE_SIZE:
                return True

            # Extreme aspect ratios (banners or spacers)
            if width > 0 and height > 0:
                ratio = max(width, height) / min(width, height)
                if ratio > 15:
                    return True
        except (ValueError, TypeError):
            pass

        return False

    # Backwards-compatibility alias
    _is_boilerplate_image = _is_boilerplate_image_attrs
