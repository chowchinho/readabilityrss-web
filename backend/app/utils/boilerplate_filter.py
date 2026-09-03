"""Filter for identifying and removing boilerplate images (ads, sidebars, affiliate widgets)."""
import re
from typing import List
from bs4 import Tag


class BoilerplateFilter:
    """Filter for detecting boilerplate images using semantic element analysis."""

    # Maximum parent levels to traverse when checking for boilerplate markers.
    # Set to 20 to balance between finding distant containers and performance.
    MAX_PARENT_LEVELS = 20
    
    # Regex for unlikely content containers
    UNLIKELY_RE = re.compile(
        r"related|sidebar|ad-|advertisement|recommended|recommend|widget|social|"
        r"affiliate|popular|trending|trend|recommendation|featured|special|promo|"
        r"sponsor|sponsored|native|outbrain|taboola|revcontent|writer|author|"
        r"profile|avatar|thumbnail|meta|nav|footer|header|menu|aside|banner|popup|"
        r"modal|overlay|plugin|share|follow|subscribe|newsletter|"
        r"bottom|comments|disqus|extra",
        re.I
    )

    # Regex for containers that are likely to be content
    MAYBE_CONTENT_RE = re.compile(
        r"and|\barticle\b|body|column|main|shadow|content|image|picture|post|entry|page|grid|"
        r"gallery|slider|swiper|product|detail",
        re.I
    )

    # Semantic HTML tags that unambiguously contain article content
    CONTENT_TAGS = {"article", "main"}

    # Semantic HTML tags that indicate supplementary/non-content sections
    BOILERPLATE_TAGS = {"aside", "nav"}

    # ID attributes that typically indicate boilerplate content.
    BOILERPLATE_IDS = {
        "ad",
        "sidebar",
        "related",
        "recommended",
        "popular",
        "trending",
        "recommendation",
        "author",
        "profile",
        "comment",
        "social",
        "share",
    }


    def remove_boilerplate_elements(self, container: Tag) -> Tag:
        """
        Identify and remove boilerplate elements from within a container.
        
        Searches for divs, sections, asides, etc. that match boilerplate patterns
        and removes them from the DOM tree.
        
        Args:
            container: BeautifulSoup Tag to clean
            
        Returns:
            The cleaned BeautifulSoup Tag
        """
        if not container:
            return container

        # Find all potential boilerplate elements
        # We look for common container tags
        potential_tags = container.find_all(['div', 'section', 'aside', 'nav', 'ul', 'ol', 'footer'])
        
        elements_to_remove = []
        for element in potential_tags:
            # Check ID
            element_id = element.get("id")
            if element_id and isinstance(element_id, str):
                if element_id.lower() in self.BOILERPLATE_IDS:
                    elements_to_remove.append(element)
                    continue

            # Check Classes (regex match)
            classes = element.get("class")
            if classes:
                class_str = " ".join(classes) if isinstance(classes, list) else str(classes)
                if self.UNLIKELY_RE.search(class_str) and not self.MAYBE_CONTENT_RE.search(class_str):
                    elements_to_remove.append(element)
                    continue

        # Remove the identified elements
        for element in elements_to_remove:
            # Check if element still has a parent (might have been removed as a child of another boilerplate element)
            if element.parent:
                element.decompose()
        
        return container

    def filter_content_images(self, images: List[Tag]) -> List[Tag]:
        """
        Filter out boilerplate images from a list of images.

        Args:
            images: List of BeautifulSoup img tags

        Returns:
            List of images that are not in boilerplate containers
        """
        return [img for img in images if not self._is_boilerplate_image(img)]

    def _is_boilerplate_image(self, img: Tag) -> bool:
        """
        Check if an image is within a boilerplate container.

        Traverses parent elements up to MAX_PARENT_LEVELS to check for boilerplate
        markers in id attributes, class names, roles, and aria-labels.

        Args:
            img: BeautifulSoup img tag object

        Returns:
            True if image is in a boilerplate container, False otherwise
        """
        current = img.parent
        levels_checked = 0

        # Modern ad-tech and navigation keywords
        BOILERPLATE_ROLES = {"complementary", "banner", "contentinfo", "navigation"}
        BOILERPLATE_ARIA_KEYWORDS = {"advertisement", "sponsored", "related", "social", "promoted"}

        while current is not None and levels_checked < self.MAX_PARENT_LEVELS:
            # 0. Stop early if we've reached a semantic content container —
            #    anything inside <article> or <main> is almost certainly content.
            if hasattr(current, 'name') and current.name in self.CONTENT_TAGS:
                return False

            # 0b. Check tag name for semantic boilerplate elements (e.g. <aside>, <nav>)
            if hasattr(current, 'name') and current.name in self.BOILERPLATE_TAGS:
                return True

            # 1. Check ID (exact match)
            element_id = current.get("id")
            if element_id and isinstance(element_id, str):
                if element_id.lower() in self.BOILERPLATE_IDS:
                    return True

            # 2. Check Role (exact match)
            role = current.get("role")
            if role and isinstance(role, str):
                if role.lower() in BOILERPLATE_ROLES:
                    return True

            # 3. Check Aria-Label (keyword match)
            aria_label = current.get("aria-label")
            if aria_label and isinstance(aria_label, str):
                label_lower = aria_label.lower()
                if any(keyword in label_lower for keyword in BOILERPLATE_ARIA_KEYWORDS):
                    return True

            # 4. Check Classes (substring match)
            classes = current.get("class")
            if classes:
                if isinstance(classes, str):
                    class_list = classes.split()
                elif isinstance(classes, list):
                    class_list = classes
                else:
                    class_list = []

                class_str = " ".join(class_list)
                if self.UNLIKELY_RE.search(class_str) and not self.MAYBE_CONTENT_RE.search(class_str):
                    return True

            # Move to parent and continue traversing
            current = current.parent
            levels_checked += 1

        return False

