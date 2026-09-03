import logging
import re
from collections import Counter
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class LinkDiscovery:
    def __init__(self):
        # Ignore common non-article prefixes or exact matches
        self.ignore_prefixes = ['javascript:', 'mailto:', 'tel:']
        self.ignore_paths = [
            '/login', '/signup', '/register', '/about', '/contact', 
            '/search', '/tag', '/category', '/author'
        ]
        self.ignore_domains = [
            'facebook.com', 'twitter.com', 'instagram.com', 'linkedin.com',
            'youtube.com', 'pinterest.com', 't.co'
        ]

    def _is_valid_article_link(self, url: str, base_url: str) -> bool:
        if not url or url.startswith('#'):
            return False
            
        for prefix in self.ignore_prefixes:
            if url.lower().startswith(prefix):
                return False
                
        try:
            parsed = urlparse(urljoin(base_url, url))
            domain = parsed.netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
                
            if domain in self.ignore_domains:
                return False
                
            path = parsed.path.lower()
            if path == '/' or path == '':
                return False
                
            for ignore_path in self.ignore_paths:
                if path.startswith(ignore_path):
                    return False
                    
            return True
        except:
            return False

    def _get_parent_pattern(self, a_tag):
        # Build a pattern like "div.article-card > h2.title"
        # We simplify to parent's tag and classes
        parent = a_tag.parent
        if not parent:
            return None
        
        tag_name = parent.name
        classes = parent.get('class', [])
        
        if classes:
            return f"{tag_name}.{'.'.join(classes)}"
        return tag_name

    def _extract_site_name(self, soup, base_url: str) -> str:
        """Extract site name from <title> tag, cleaning common suffixes."""
        title_tag = soup.find('title')
        if not title_tag or not title_tag.string:
            return urlparse(base_url).netloc

        title = title_tag.string.strip()
        # Many sites use "Page Title - Site Name" or "Page Title | Site Name"
        # Take the last part after common separators as likely the site name
        for sep in [' | ', ' - ', ' – ', ' — ', ' :: ', ' » ', ' ｜ ', '｜']:
            if sep in title:
                parts = title.split(sep)
                # The site name is usually the last or first part
                # If last part is short (likely site name), use it
                if len(parts[-1].strip()) < len(parts[0].strip()):
                    return parts[-1].strip()
                return parts[0].strip()
        return title

    def _extract_channel_link(self, container, base_url: str) -> str | None:
        """The feed's canonical site URL from a channel/feed <link>.

        RSS uses <link>text</link>; Atom uses <link rel="alternate" href=...>.
        Self-referential atom:link elements (rel="self") are skipped so the
        result is the website, not the feed host.
        """
        if not container:
            return None
        for link in container.find_all('link', recursive=False):
            if link.get('rel') in ('self', ['self']):
                continue
            href = link.get('href') or link.get_text(strip=True)
            if href:
                return urljoin(base_url, href)
        return None

    def _try_parse_feed(self, html: str, base_url: str) -> dict | None:
        """Detect and parse RSS 2.0 or Atom XML feeds, returning links or None."""
        # Quick check: does it look like XML feed content?
        stripped = html.lstrip()
        if not (stripped.startswith('<?xml') or stripped.startswith('<rss') or stripped.startswith('<feed')):
            return None

        soup = BeautifulSoup(html, 'xml')
        if not soup:
            return None

        # RSS 2.0: <rss><channel><item><title> + <link>
        items = soup.find_all('item')
        if items:
            channel = soup.find('channel')
            site_name = channel.find('title').get_text(strip=True) if channel and channel.find('title') else urlparse(base_url).netloc
            links = []
            for idx, item in enumerate(items):
                link_tag = item.find('link')
                title_tag = item.find('title')
                url = link_tag.get_text(strip=True) if link_tag else None
                title = title_tag.get_text(strip=True) if title_tag else ''
                
                # Extract image
                image_url = None
                # 1. media:content or media:thumbnail
                media = item.find(['media:content', 'content', 'media:thumbnail'])
                if media and media.get('url'):
                    image_url = media.get('url')
                # 2. enclosure
                if not image_url:
                    enclosure = item.find('enclosure', type=re.compile(r'^image/'))
                    if enclosure:
                        image_url = enclosure.get('url')
                # 3. image tag (sometimes used inside item)
                if not image_url:
                    img_tag = item.find('image')
                    if img_tag and img_tag.find('url'):
                        image_url = img_tag.find('url').get_text(strip=True)
                    elif img_tag:
                        image_url = img_tag.get_text(strip=True)

                if url:
                    links.append({
                        'url': urljoin(base_url, url), 
                        'title': title, 
                        'index': idx,
                        'image': urljoin(base_url, image_url) if image_url else None
                    })
            return {
                'links': links,
                'selector_used': 'item > link (RSS 2.0)',
                'total_found': len(links),
                'site_name': site_name,
                'site_url': self._extract_channel_link(channel, base_url),
            }

        # Atom: <feed><entry><title> + <link href="...">
        entries = soup.find_all('entry')
        if entries:
            feed_title = soup.find('feed')
            site_name = feed_title.find('title').get_text(strip=True) if feed_title and feed_title.find('title') else urlparse(base_url).netloc
            links = []
            for idx, entry in enumerate(entries):
                link_tag = entry.find('link', rel='alternate') or entry.find('link', rel=None) or entry.find('link')
                title_tag = entry.find('title')
                url = link_tag.get('href') if link_tag else None
                title = title_tag.get_text(strip=True) if title_tag else ''
                
                # Extract image
                image_url = None
                # 1. media:content or media:thumbnail
                media = entry.find(['media:content', 'content', 'media:thumbnail'])
                if media and media.get('url'):
                    image_url = media.get('url')
                # 2. link rel="enclosure" or "image"
                if not image_url:
                    img_link = entry.find('link', rel=['enclosure', 'image', 'preview'], type=re.compile(r'^image/'))
                    if img_link:
                        image_url = img_link.get('href')
                
                if url:
                    links.append({
                        'url': urljoin(base_url, url), 
                        'title': title, 
                        'index': idx,
                        'image': urljoin(base_url, image_url) if image_url else None
                    })
            return {
                'links': links,
                'selector_used': 'entry > link (Atom)',
                'total_found': len(links),
                'site_name': site_name,
                'site_url': self._extract_channel_link(feed_title, base_url),
            }

        return None

    def discover(self, html: str, base_url: str) -> dict:
        # Try RSS/Atom XML first
        feed_result = self._try_parse_feed(html, base_url)
        if feed_result:
            return feed_result

        soup = BeautifulSoup(html, 'html.parser')
        site_name = self._extract_site_name(soup, base_url)
        
        # Remove nav, header, footer, etc.
        for tag in soup.find_all(['nav', 'header', 'footer']):
            tag.decompose()
            
        for tag in soup.find_all(class_=re.compile(r'nav|menu|sidebar|footer|header', re.I)):
            tag.decompose()

        links = soup.find_all('a')
        
        groups = {}
        parsed_base = urlparse(base_url)
        def normalize(p):
            return p.rstrip('/').lower().replace('/index.php', '').replace('/index.html', '') or '/'
        
        for idx, a in enumerate(links):
            href = a.get('href')
            if not href:
                continue
                
            text = a.get_text(strip=True)
            if not text:
                continue
                
            if not self._is_valid_article_link(href, base_url):
                continue
                
            parsed_link = urlparse(urljoin(base_url, href))
            is_self_nav = normalize(parsed_link.path) == normalize(parsed_base.path) and parsed_link.query

            
            pattern = self._get_parent_pattern(a)
            if not pattern:
                continue
                
            if pattern not in groups:
                groups[pattern] = []
                
            full_url = urljoin(base_url, href)
            groups[pattern].append({
                'url': full_url,
                'title': text,
                'index': idx,
                'a_tag': a,
                'is_self_nav': is_self_nav
            })


        best_group = []
        best_pattern = ""
        best_score = -1

        for pattern, group_links in groups.items():
            if len(group_links) < 3:
                continue
                
            # Score based on size and text length
            # avg_text_len is a stronger signal for articles than link count for large generic sites
            avg_text_len = sum(len(l['title']) for l in group_links) / len(group_links)
            
            # Penalize very short titles (likely nav/years/tags)
            length_bonus = avg_text_len * 2
            if avg_text_len < 12:
                length_bonus = 0
                
            # Cap count contribution to prevent being overwhelmed by giant nav lists/tag clouds
            count_contribution = min(len(group_links), 40) * 10
            score = count_contribution + length_bonus
            
            # Penalize groups that mostly consist of navigation to the same page (e.g. filters, pagination)
            self_nav_count = sum(1 for l in group_links if l.get('is_self_nav'))
            if self_nav_count > len(group_links) / 2:
                score -= 150
            
            # Bonus if parent is a heading
            if pattern.startswith(('h1', 'h2', 'h3', 'h4', 'h5', 'h6')):
                score += 50
                
            if score > best_score:
                best_score = score
                best_group = group_links
                best_pattern = pattern

        # Deduplicate by URL, keeping longest title
        unique_links = {}
        for link in best_group:
            url = link['url']
            if url not in unique_links or len(link['title']) > len(unique_links[url]['title']):
                unique_links[url] = {
                    'url': url,
                    'title': link['title'],
                    'index': link['index']
                }

        final_links = list(unique_links.values())
        final_links.sort(key=lambda x: x['index'])

        selector = f"{best_pattern} a" if best_pattern else ""
        if best_pattern and best_pattern.startswith(('h1','h2','h3','h4','h5','h6')):
            selector = f"{best_pattern} > a"

        return {
            'links': final_links,
            'selector_used': selector,
            'total_found': len(final_links),
            'site_name': site_name
        }

    def discover_with_selector(self, html: str, base_url: str, item_selector: str, link_selector: str = "a", exclude_selector: str = None) -> dict:
        # If it's one of our special placeholders, just use the normal discover logic
        if item_selector and ("(RSS" in item_selector or "(Atom" in item_selector):
            return self.discover(html, base_url)

        soup = BeautifulSoup(html, 'html.parser')
        site_name = self._extract_site_name(soup, base_url)

        # Apply exclude_selector if provided
        if exclude_selector:
            try:
                for excluded in soup.select(exclude_selector):
                    excluded.decompose()
            except Exception as e:
                logger.warning("Invalid exclude_selector '%s': %s", exclude_selector, e)

        try:
            containers = soup.select(item_selector)
        except Exception as e:
            logger.warning("Invalid item_selector '%s': %s", item_selector, e)
            containers = []
        
        links = []
        unique_urls = set()
        
        for idx, container in enumerate(containers):
            a_tags = container.select(link_selector)
            for a in a_tags:
                href = a.get('href')
                if not href:
                    continue
                    
                full_url = urljoin(base_url, href)
                if full_url in unique_urls:
                    continue
                    
                text = a.get_text(strip=True)
                
                links.append({
                    'url': full_url,
                    'title': text,
                    'index': len(links)
                })
                unique_urls.add(full_url)
                
        return {
            'links': links,
            'selector_used': f"{item_selector} {link_selector}",
            'total_found': len(links),
            'site_name': site_name
        }

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        d = domain.lower()
        return d[4:] if d.startswith('www.') else d

    def derive_exclusion_patterns(self, base_url: str, included_urls: list[str], excluded_urls: list[str]) -> list[dict]:
        """Analyze excluded URLs vs included URLs to derive reusable filter patterns."""
        if not excluded_urls:
            return []

        base_domain = self._normalize_domain(urlparse(base_url).netloc)
        included_parsed = [urlparse(u) for u in included_urls]
        included_domains = {self._normalize_domain(p.netloc) for p in included_parsed}
        included_paths = {p.path for p in included_parsed}

        patterns = []
        same_domain_excluded = []

        # 1. External domain patterns
        external_domains = set()
        for url in excluded_urls:
            parsed = urlparse(url)
            domain = self._normalize_domain(parsed.netloc)
            if domain != base_domain:
                external_domains.add(domain)
            else:
                same_domain_excluded.append(parsed)

        for domain in sorted(external_domains):
            if domain not in included_domains:
                patterns.append({"type": "domain", "value": domain})

        # 2. Path prefix patterns (first path segment)
        prefix_counts = Counter()
        for parsed in same_domain_excluded:
            segments = [s for s in parsed.path.split('/') if s]
            if segments:
                prefix_counts[f"/{segments[0]}/"] += 1

        for prefix, count in prefix_counts.most_common():
            # Only emit if no included URL starts with this prefix
            if not any(p.path.startswith(prefix) for p in included_parsed):
                patterns.append({"type": "path_prefix", "value": prefix})
                # Remove matched URLs from further analysis
                same_domain_excluded = [p for p in same_domain_excluded if not p.path.startswith(prefix)]

        # 3. Path-contains patterns (interior segments)
        segment_counts = Counter()
        for parsed in same_domain_excluded:
            segments = [s for s in parsed.path.split('/') if s]
            for seg in segments:
                candidate = f"/{seg}/"
                segment_counts[candidate] += 1

        for segment, count in segment_counts.most_common():
            if count < 1:
                continue
            # Safety: never match any included URL
            if not any(segment in p.path for p in included_parsed):
                patterns.append({"type": "path_contains", "value": segment})

        return patterns

    @staticmethod
    def apply_exclusion_patterns(links: list[dict], patterns: list[dict]) -> list[dict]:
        """Filter out links matching any exclusion pattern."""
        if not patterns:
            return links

        def matches(link_url: str) -> bool:
            parsed = urlparse(link_url)
            domain = parsed.netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            path = parsed.path

            for p in patterns:
                if p["type"] == "domain" and domain == p["value"]:
                    return True
                elif p["type"] == "path_prefix" and path.startswith(p["value"]):
                    return True
                elif p["type"] == "path_contains" and p["value"] in path:
                    return True
            return False

        return [link for link in links if not matches(link['url'])]
