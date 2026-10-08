"""
Recursive website crawler for dynamic URL discovery.

Performs BFS crawl starting from a base URL, discovering all internal links.
Filters out static assets, external links, and respects depth/page limits.
"""

import hashlib
import json
import logging
import re
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

logger = logging.getLogger("crawler")

# File extensions to skip during crawl (static assets, documents, media)
SKIP_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".tar", ".gz", ".7z",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp",
    ".mp4", ".mp3", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf", ".eot",
    ".csv", ".txt",
}

# URL path segments to skip (common non-content paths)
SKIP_PATH_PATTERNS = {
    "/wp-admin", "/wp-login", "/wp-json", "/feed", "/rss",
    "/tag/", "/author/", "/page/", "/cart", "/checkout",
    "/_next/", "/static/", "/assets/", "/media/",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def normalize_url(url: str) -> str:
    """Normalize a URL by removing fragments, trailing slashes, and lowering scheme/host."""
    parsed = urlparse(url)
    # Rebuild without fragment
    normalized = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "/",
        parsed.params,
        parsed.query,
        "",  # drop fragment
    ))
    return normalized


def is_valid_internal_url(url: str, base_domain: str) -> bool:
    """Check if a URL is a valid internal page link worth crawling."""
    parsed = urlparse(url)
    
    # Must be HTTP/HTTPS
    if parsed.scheme not in ("http", "https"):
        return False
    
    # Must be same domain
    if parsed.netloc.lower().replace("www.", "") != base_domain.replace("www.", ""):
        return False
    
    # Skip mailto/tel links
    if url.startswith(("mailto:", "tel:", "javascript:")):
        return False
    
    # Skip static asset extensions
    path_lower = parsed.path.lower()
    for ext in SKIP_EXTENSIONS:
        if path_lower.endswith(ext):
            return False
    
    # Skip known non-content paths
    for pattern in SKIP_PATH_PATTERNS:
        if pattern in path_lower:
            return False
    
    return True


def fetch_page(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch a single page's HTML content. Returns None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return None
            return response.read().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug(f"Failed to fetch {url}: {e}")
        return None


def extract_links(html: str, page_url: str) -> Set[str]:
    """Extract all href links from HTML and resolve them to absolute URLs."""
    links = set()
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            # Resolve relative URLs
            absolute_url = urljoin(page_url, href)
            links.add(normalize_url(absolute_url))
    except Exception as e:
        logger.debug(f"Error extracting links from {page_url}: {e}")
    return links


FRAMEWORK_NOISE_WORDS = {
    'outletboundary', 'viewportboundary', 'metadataboundary', '__page__',
    'parallelrouterkey', 'errorstyles', 'errorscripts', 'templatestyles',
    'templatescripts', 'notfound', 'not-found', 'default', '(frontend)', 'crossorigin',
    'font/woff2', 'null', 'undefined', 'children', 'classname', 'id', 'type',
    'variant', 'href', 'title', 'description', 'label', 'blocktype', 'blockname',
    'entrycssfiles', 'head', 'body', 'html', 'loading', 'layout', 'style', 'font', 'search',
    'fontsize', 'fontweight', 'verticalalign', 'lineheight', 'margin', 'padding',
    'stylesheet', 'precedence', 'inline-block', 'forbidden', 'unauthorized',
    'display', 'position', 'color', 'background', 'border', 'opacity', 'overflow',
    'alignitems', 'justifycontent', 'flexdirection', 'boxsizing', 'textalign',
    'whitespace', 'zindex', 'maxwidth', 'minwidth', 'maxheight', 'minheight',
    'gap', 'radius', 'borderradius', 'boxshadow', 'cursor', 'transition',
    'animation', 'transform', 'pointerevents', 'usernotfound', 'errorboundary',
    'suspense', 'route', 'router', 'template', 'div', 'fontfamily', 'system-',
    'this page could not be found.', '404: this page could not be found.',
    'dangerouslysetinnerhtml', 'error', 'height', 'center', 'flex', 'column',
    'row', 'grid', 'auto', 'width', 'left', 'right', 'top', 'bottom',
    'relative', 'absolute', '100vh', '100vw', '100%',
    'link', 'rel', 'next', 'nonce', 'script', 'src', 'async', 'defer', 'charset',
    'target', 'href', 'title', 'span', 'div', 'p', 'meta', 'head', 'body', 'html',
    'main', 'nav', 'header', 'footer', 'article', 'section', 'aside', 'button',
    'input', 'form', 'img', 'svg', 'path', 'true', 'false', 'boolean', 'string',
    'number', 'object', 'function', 'symbol', 'array',
}


def is_framework_noise(val: str) -> bool:
    """Filter out Next.js framework variables, IDs, classnames, CSS styles, and assets."""
    val_clean = val.strip()
    if len(val_clean) <= 2:
        return True
    
    val_lower = val_clean.lower()
    if (
        val_lower in FRAMEWORK_NOISE_WORDS
        or val.startswith(('$', '__', '/_next', 'script-', 'style-', 'link-', 'Next.', 'next-'))
        or 'boundary' in val_lower
        or 'next-error' in val_lower
    ):
        return True
    
    # CamelCase component names (e.g. ThemeProvider, ClientSegmentRoot)
    if len(val_clean.split()) == 1 and re.search(r'[a-z][A-Z]', val_clean):
        return True

    # React 19 / Next.js generated IDs (e.g. r2-USRJ-O71V5E1SFAVyD, 1:HL...)
    if len(val_clean.split()) == 1 and ('-' in val_clean or ':' in val_clean) and any(c.isdigit() for c in val_clean):
        return True

    # CSS Modules or Webpack chunk variables
    if '__variable' in val_lower or '-module__' in val_lower or '_module_' in val_lower or '__next' in val_lower:
        return True

    # MIME types
    if any(val_lower.startswith(m) for m in ['image/', 'audio/', 'video/', 'font/', 'text/css', 'application/']):
        return True

    # Single lowercase word HTML attributes or JSON keys (e.g., name, content, hidden, lang, icon, sizes)
    if len(val_clean.split()) == 1:
        if val_clean.islower() and len(val_clean) < 15:
            return True
        if ('_' in val_clean or '-' in val_clean) and len(val_clean) < 30:
            return True

    # CSS blocks, media queries, rgb values, font-family definitions, gradients
    if any(ch in val for ch in ['{', '}', '@media', ';', 'rgba(', 'rgb(']):
        return True
    if any(term in val_lower for term in ['sans-serif', 'system-ui', 'monospace', 'font-family', 'gradient(']):
        return True

    # CSS shorthand or measurements (e.g. "0 20px 0 0", "49px", "100%")
    if re.match(r'^[\d\.\s%pxremem\-]+$', val_clean):
        return True

    # 24-character hex IDs (database IDs)
    if re.match(r'^[a-fA-F0-9]{24}$', val_clean):
        return True
        
    # URLs and relative paths
    if val.startswith('/') or '://' in val or val.startswith('www.'):
        return True
        
    # Image filenames and asset extensions
    if any(val_lower.endswith(ext) for ext in ['.jpg', '.png', '.webp', '.svg', '.gif', '.ico', '.css', '.js', '.woff', '.woff2', '.ttf']):
        return True
        
    # ISO Date strings
    if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', val_clean):
        return True
        
    # Structural JSON fragments (pure digits, punctuation sequences, or next.js brackets)
    if val_clean.isdigit() or re.match(r'^[\d\s:\[\],{}#\-\+\(\)]+$', val_clean):
        return True
        
    # CSS class names or tailwind styles
    tailwind_indicators = [
        'bg-', 'text-', 'flex', 'grid', 'col-', 'md:', 'lg:', 'xl:', 'sm:',
        'opacity-', 'rounded-', 'inset-', 'w-', 'h-', 'border-', 'p-', 'm-',
        'space-', 'gap-', 'pointer-events-', 'items-', 'justify-', 'shadow-',
    ]
    if ' ' in val_clean and any(x in val_clean for x in tailwind_indicators):
        return True
        
    return False


def extract_nextjs_texts(soup: BeautifulSoup) -> List[str]:
    """Extract and decode user-facing text from self.__next_f.push scripts."""
    payloads = []
    
    for script in soup.find_all("script"):
        script_text = script.string or script.text or ""
        if not script_text or "self.__next_f.push" not in script_text:
            continue
            
        matches = list(re.finditer(r'self\s*\.\s*__next_f\s*\.\s*push\s*\(\s*\[\s*\d+\s*,\s*', script_text))
        for m in matches:
            start_idx = m.end()
            if start_idx >= len(script_text):
                continue
            quote_char = script_text[start_idx]
            if quote_char not in ('"', "'"):
                continue
                
            str_content = []
            i = start_idx + 1
            escaped = False
            while i < len(script_text):
                char = script_text[i]
                if escaped:
                    str_content.append(char)
                    escaped = False
                elif char == '\\':
                    str_content.append(char)
                    escaped = True
                elif char == quote_char:
                    break
                else:
                    str_content.append(char)
                i += 1
                
            raw_str = "".join(str_content)
            
            try:
                decoded = json.loads(f'"{raw_str}"')
                payloads.append(decoded)
            except Exception:
                try:
                    decoded = raw_str.encode('utf-8').decode('unicode-escape')
                    payloads.append(decoded)
                except Exception:
                    payloads.append(raw_str)
                    
    extracted_texts = []
    for p in payloads:
        matches = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', p)
        for m in matches:
            try:
                dec_m = json.loads(f'"{m}"')
            except Exception:
                dec_m = m
            
            val = dec_m.strip()
            if not val or is_framework_noise(val):
                continue
            if val not in extracted_texts:
                extracted_texts.append(val)
                
    return extracted_texts


BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "div", "section", 
    "article", "blockquote", "header", "footer", "main", "dt", "dd", "table"
}


def extract_structured_text(elem) -> str:
    """Recursively extract text from HTML elements, ensuring block tags are separated
    by newlines and inline tags (like span, strong, a) are separated by spaces.
    This prevents word truncation in animated spans and preserves headings and lists."""
    chunks = []
    for child in elem.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                if chunks and not chunks[-1].endswith((" ", "\n")):
                    chunks.append(" ")
                chunks.append(text)
        elif isinstance(child, Tag):
            child_text = extract_structured_text(child)
            if child_text:
                if child.name in BLOCK_TAGS:
                    chunks.append("\n" + child_text + "\n")
                else:
                    if chunks and not chunks[-1].endswith((" ", "\n")):
                        chunks.append(" ")
                    chunks.append(child_text + " ")
    return "".join(chunks)


def clean_html(html: str, url: str) -> Dict[str, str]:
    """
    Clean HTML content, extracting meaningful and structured text.
    Preserves titles, headings, bullet points, implementation phases,
    and paragraphs without splitting inline tags into individual lines or
    polluting the page with unrelated Next.js route bundles.
    Returns dict with 'title', 'content', and 'url'.
    """
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else url.split("/")[-1]

    # Remove non-content elements
    for tag in soup(["script", "style", "noscript", "svg", "path", "meta", "link"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    article = soup.find("article")
    main_el = article if article else soup.find("main") or soup.find("body")
    if main_el is None:
        main_el = soup

    for tag in main_el.find_all(["header", "footer", "nav"]):
        tag.decompose()

    raw_structured = extract_structured_text(main_el)
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in raw_structured.split("\n")]

    clean_lines = []
    seen = set()
    for line in lines:
        if not line:
            continue
        if is_framework_noise(line):
            continue
        if line not in seen:
            seen.add(line)
            clean_lines.append(line)

    content = "\n".join(clean_lines)

    # Extract interactive client component blocks (e.g., Meet Our Team cards) from Next.js push scripts
    if "self.__next_f.push" in html:
        soup_fresh = BeautifulSoup(html, "html.parser")
        comp_blocks = []
        for script in soup_fresh.find_all("script"):
            s_text = script.string or script.text or ""
            if "self.__next_f.push" not in s_text:
                continue
            pairs = re.findall(r'\\?"title\\?":\\?"([^"\\]+)\\?",\\?"description\\?":\\?"([^"\\]+)\\?"', s_text)
            for t_val, d_val in pairs:
                t_val = t_val.replace("\\u0026", "&").strip()
                d_val = d_val.replace("\\u0026", "&").strip()
                if len(t_val) >= 2 and len(d_val) >= 2 and not is_framework_noise(t_val) and not is_framework_noise(d_val):
                    line_str = f"{t_val}: {d_val}"
                    if line_str not in content and line_str not in comp_blocks:
                        comp_blocks.append(line_str)
        if comp_blocks:
            content += "\n\n" + "\n".join(comp_blocks)

    # Fallback to Next.js payload text ONLY if the HTML body was essentially empty (pure CSR)
    if len(content.strip()) < 100:
        soup_fresh = BeautifulSoup(html, "html.parser")
        nextjs_texts = extract_nextjs_texts(soup_fresh)
        if nextjs_texts:
            content = "\n".join(nextjs_texts)

    return {"title": title, "content": content, "url": url}


def content_hash(text: str) -> str:
    """Generate SHA-256 hash of text content for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_sitemaps_from_robots(base_url: str) -> List[str]:
    """
    Fetch robots.txt from base_url and extract all declared Sitemap URLs.
    Falls back to /sitemap.xml if no sitemaps are declared.
    """
    robots_url = urljoin(base_url, "/robots.txt")
    sitemaps = []
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read().decode("utf-8", errors="ignore")
        matches = re.findall(r'^Sitemap:\s*(https?://\S+)', content, re.IGNORECASE | re.MULTILINE)
        sitemaps = [m.strip() for m in matches if m.strip()]
        logger.info(f"Discovered {len(sitemaps)} sitemaps from {robots_url}")
    except Exception as e:
        logger.warning(f"Failed to fetch robots.txt from {robots_url}: {e}")
    
    if not sitemaps:
        sitemaps = [urljoin(base_url, "/sitemap.xml")]
    return sitemaps


def fetch_sitemap_urls(sitemap_url: str) -> List[str]:
    """
    Fetch and parse an XML sitemap, returning all <loc> URLs.
    Supports standard sitemaps and sitemap index files.
    """
    urls = []
    try:
        req = urllib.request.Request(sitemap_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read().decode("utf-8", errors="ignore")
        # Extract all <loc> entries using regex (handles both sitemap and sitemapindex)
        locs = re.findall(r'<loc>\s*(https?://[^<]+?)\s*</loc>', content)
        for loc in locs:
            loc = loc.strip()
            # If this is a sitemap index pointing to sub-sitemaps, recurse
            if loc.endswith('.xml'):
                urls.extend(fetch_sitemap_urls(loc))
            else:
                urls.append(normalize_url(loc))
        logger.info(f"Sitemap {sitemap_url}: found {len(urls)} URLs")
    except Exception as e:
        logger.warning(f"Failed to fetch sitemap {sitemap_url}: {e}")
    return urls


def discover_urls(
    base_url: str,
    max_pages: int = 500,
    max_depth: int = 10,
    max_workers: int = 5,
    seed_urls: Optional[List[str]] = None,
) -> List[str]:
    """
    BFS crawl starting from base_url to discover all internal page URLs.
    Optionally pre-seeds the crawl queue with sitemap URLs.
    
    Args:
        base_url: The starting URL to crawl from
        max_pages: Maximum number of pages to discover (safety limit)
        max_depth: Maximum link-follow depth from the base URL
        max_workers: Number of concurrent fetch workers
        seed_urls: Optional list of URLs to add to the crawl queue upfront (e.g. from sitemaps)
    
    Returns:
        List of discovered internal URLs
    """
    base_parsed = urlparse(base_url)
    base_domain = base_parsed.netloc.lower()
    
    start_url = normalize_url(base_url)
    visited: Set[str] = set()
    discovered: List[str] = []
    
    # BFS queue: (url, depth)
    queue: deque = deque([(start_url, 0)])
    visited.add(start_url)

    # Pre-seed with sitemap URLs (depth=0 so their links also get followed)
    if seed_urls:
        base_domain = urlparse(base_url).netloc.lower()
        for su in seed_urls:
            su_norm = normalize_url(su)
            if su_norm not in visited and is_valid_internal_url(su_norm, base_domain):
                visited.add(su_norm)
                queue.append((su_norm, 0))
        logger.info(f"Pre-seeded BFS with {len(seed_urls)} sitemap URLs")
    
    logger.info(f"Starting BFS crawl from {base_url} (max_pages={max_pages}, max_depth={max_depth})")
    
    while queue and len(discovered) < max_pages:
        # Collect a batch of URLs at current queue front
        batch: List[Tuple[str, int]] = []
        while queue and len(batch) < max_workers:
            batch.append(queue.popleft())
        
        if not batch:
            break
        
        # Fetch batch concurrently
        results: Dict[str, Tuple[Optional[str], int]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(fetch_page, url): (url, depth)
                for url, depth in batch
            }
            for future in as_completed(future_map):
                url, depth = future_map[future]
                try:
                    html = future.result()
                    results[url] = (html, depth)
                except Exception as e:
                    logger.debug(f"Fetch error for {url}: {e}")
                    results[url] = (None, depth)
        
        # Process results: extract links for BFS expansion
        for url, (html, depth) in results.items():
            if html is None:
                continue
            
            discovered.append(url)
            logger.debug(f"Discovered [{len(discovered)}/{max_pages}] depth={depth}: {url}")
            
            if len(discovered) >= max_pages:
                logger.warning(f"Reached max_pages limit ({max_pages}). Stopping crawl.")
                break
            
            # Only expand links if we haven't hit max depth
            if depth < max_depth:
                new_links = extract_links(html, url)
                for link in new_links:
                    if link not in visited and is_valid_internal_url(link, base_domain):
                        visited.add(link)
                        queue.append((link, depth + 1))
    
    logger.info(f"BFS crawl complete. Discovered {len(discovered)} pages (visited {len(visited)} URLs total)")
    return discovered


def crawl_and_extract(
    urls: List[str],
    max_workers: int = 5,
) -> List[Dict[str, str]]:
    """
    Fetch and clean content from a list of URLs.
    
    Returns list of dicts with 'url', 'title', 'content', 'content_hash'.
    """
    documents = []
    
    logger.info(f"Fetching and extracting content from {len(urls)} URLs...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(fetch_page, url): url for url in urls}
        
        for future in as_completed(future_map):
            url = future_map[future]
            try:
                html = future.result()
                if html is None:
                    logger.warning(f"Failed to fetch content from {url}")
                    continue
                
                doc = clean_html(html, url)
                if len(doc["content"]) > 50:
                    doc["content_hash"] = content_hash(doc["content"])
                    documents.append(doc)
                    logger.debug(f"Extracted {url} ({len(doc['content'])} chars)")
                else:
                    logger.debug(f"Skipped {url} — content too short ({len(doc['content'])} chars)")
            except Exception as e:
                logger.warning(f"Error processing {url}: {e}")
    
    logger.info(f"Extracted content from {len(documents)}/{len(urls)} URLs")
    return documents
