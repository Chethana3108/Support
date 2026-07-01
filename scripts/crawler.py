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

from bs4 import BeautifulSoup, Comment

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


def is_framework_noise(val: str) -> bool:
    """Filter out Next.js framework variables, IDs, classnames, and assets."""
    if len(val) <= 2:
        return True
    
    # 24-character hex IDs (database IDs)
    if re.match(r'^[a-fA-F0-9]{24}$', val):
        return True
        
    # URLs and relative paths
    if val.startswith('/') or '://' in val or val.startswith('www.'):
        return True
        
    # Image filenames and asset extensions
    if any(val.lower().endswith(ext) for ext in ['.jpg', '.png', '.webp', '.svg', '.gif', '.ico', '.css', '.js']):
        return True
        
    # ISO Date strings
    if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', val):
        return True
        
    # React / Next.js internal properties
    if val.startswith('$') or val in ('null', 'undefined', 'children', 'className', 'id', 'type', 'variant', 'href', 'title', 'description', 'label', 'blockType', 'blockName'):
        return True
        
    # Structural JSON fragments (pure digits, punctuation sequences, or next.js brackets)
    if val.isdigit() or re.match(r'^[\d\s:\[\],{}#\-\+\(\)]+$', val):
        return True
        
    # CSS class names or tailwind styles
    if ' ' in val and any(x in val for x in ['bg-', 'text-', 'flex', 'grid', 'col-', 'md:', 'lg:', 'xl:', 'sm:']):
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


def clean_html(html: str, url: str) -> Dict[str, str]:
    """
    Clean HTML content, extracting meaningful text.
    Returns dict with 'title', 'content', and 'url'.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Step 1: Extract text from Next.js payload scripts BEFORE decomposing script tags
    nextjs_texts = extract_nextjs_texts(soup)

    # Step 2: Remove non-content elements
    for tag in soup(["script", "style", "noscript", "svg", "path", "meta", "link"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else url.split("/")[-1]

    article = soup.find("article")
    main_el = article if article else soup.find("main") or soup.find("body")
    if main_el is None:
        main_el = soup

    for tag in main_el.find_all(["header", "footer", "nav"]):
        tag.decompose()

    text = main_el.get_text(separator="\n", strip=True)
    html_lines = [line.strip() for line in text.split("\n") if len(line.strip()) > 10]

    # Combine HTML lines and Next.js text lines
    all_lines = html_lines + nextjs_texts

    # Step 3: Globally deduplicate all lines to preserve order and keep unique content
    deduped = []
    seen = set()
    for line in all_lines:
        if line not in seen:
            seen.add(line)
            deduped.append(line)

    return {"title": title, "content": "\n".join(deduped), "url": url}


def content_hash(text: str) -> str:
    """Generate SHA-256 hash of text content for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover_urls(
    base_url: str,
    max_pages: int = 500,
    max_depth: int = 10,
    max_workers: int = 5,
) -> List[str]:
    """
    BFS crawl starting from base_url to discover all internal page URLs.
    
    Args:
        base_url: The starting URL to crawl from
        max_pages: Maximum number of pages to discover (safety limit)
        max_depth: Maximum link-follow depth from the base URL
        max_workers: Number of concurrent fetch workers
    
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
