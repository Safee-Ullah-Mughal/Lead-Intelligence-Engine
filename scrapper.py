#!/usr/bin/env python3
"""
WebProfiler - Professional Generic Web Scraper
================================================
Crawls any website and extracts all semantically useful data using
heuristic scoring — no hardcoded formats, domains, or site assumptions.

Usage:
    python scraper.py <url> [options]

Examples:
    python scraper.py https://example.com
    python scraper.py https://example.com --max-pages 20 --output results.json
    python scraper.py https://example.com --max-pages 10 --delay 1.5 --verbose
"""

import argparse
import json
import logging
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

def setup_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("WebProfiler")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


# ─────────────────────────────────────────────
#  CONTACT EXTRACTION
# ─────────────────────────────────────────────

# International phone: supports +XX, country codes, separators (space/dash/dot)
# Minimum 7 digits, max 15 (E.164 standard). Avoids matching pure year numbers.
PHONE_RE = re.compile(
    r"""
    (?<!\d)                         # no leading digit
    (?:\+?\d{1,3}[\s.\-]?)?         # optional country code
    (?:\(?\d{1,4}\)?[\s.\-]?)       # area code (with optional parens)
    \d{2,5}[\s.\-]?\d{2,5}         # core number blocks
    (?!\d)                          # no trailing digit
    """,
    re.VERBOSE,
)

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

SOCIAL_DOMAINS = {
    "linkedin.com": "linkedin",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
    "github.com": "github",
    "scholar.google.com": "google_scholar",
    "researchgate.net": "researchgate",
    "orcid.org": "orcid",
    "tiktok.com": "tiktok",
    "pinterest.com": "pinterest",
    "reddit.com": "reddit",
    "medium.com": "medium",
    "telegram.me": "telegram",
    "t.me": "telegram",
    "wa.me": "whatsapp",
}


def is_valid_phone(raw: str) -> bool:
    """Validate extracted phone: must have 7–15 digits, reject pure years."""
    digits = re.sub(r"\D", "", raw)
    if not (7 <= len(digits) <= 15):
        return False
    # Reject strings that look like standalone years (4 digits, 1900–2099)
    if re.fullmatch(r"(19|20)\d{2}", digits):
        return False
    return True


def extract_contacts(soup: BeautifulSoup, page_url: str) -> dict:
    """Extract emails, phones, and social profiles from page content and links."""
    text = soup.get_text(separator=" ")

    raw_emails = set(EMAIL_RE.findall(text))
    raw_phones = set(PHONE_RE.findall(text))

    emails = set()
    phones = set()
    socials = defaultdict(list)

    # Scan all anchor tags for mailto/tel/social links
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if addr:
                raw_emails.add(addr)
        elif href.startswith("tel:"):
            num = href[4:].strip()
            if num:
                raw_phones.add(num)
        else:
            try:
                domain = urlparse(urljoin(page_url, href)).netloc.lower()
                for social_domain, platform in SOCIAL_DOMAINS.items():
                    if social_domain in domain:
                        full = urljoin(page_url, href)
                        if full not in socials[platform]:
                            socials[platform].append(full)
                        break
            except Exception:
                pass

    # Validate and deduplicate emails
    for e in raw_emails:
        e = e.strip().lower()
        if EMAIL_RE.fullmatch(e):
            emails.add(e)

    # Validate and deduplicate phones
    for p in raw_phones:
        p = p.strip()
        if is_valid_phone(p):
            phones.add(p)

    return {
        "emails": sorted(emails),
        "phones": sorted(phones),
        "social_profiles": dict(socials),
    }


# ─────────────────────────────────────────────
#  STRUCTURED DATA EXTRACTION (JSON-LD / Microdata)
# ─────────────────────────────────────────────

def extract_structured_data(soup: BeautifulSoup) -> list:
    """Parse JSON-LD and OpenGraph blocks — the richest semantic signals."""
    results = []

    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            results.append({"source": "json_ld", "data": data})
        except (json.JSONDecodeError, TypeError):
            pass

    # OpenGraph & Twitter Card meta tags
    og_data = {}
    for meta in soup.find_all("meta"):
        prop = meta.get("property", "") or meta.get("name", "")
        content = meta.get("content", "")
        if prop.startswith("og:") or prop.startswith("twitter:"):
            og_data[prop] = content
    if og_data:
        results.append({"source": "opengraph_twitter", "data": og_data})

    return results


# ─────────────────────────────────────────────
#  PAGE METADATA
# ─────────────────────────────────────────────

def extract_metadata(soup: BeautifulSoup) -> dict:
    """Extract all meaningful <meta> tags into a clean dictionary."""
    meta = {}
    for tag in soup.find_all("meta"):
        name = tag.get("name") or tag.get("property") or tag.get("http-equiv")
        content = tag.get("content")
        if name and content:
            meta[name.lower()] = content.strip()
    return meta


# ─────────────────────────────────────────────
#  CONTENT EXTRACTION
# ─────────────────────────────────────────────

# Tags that almost never contain useful body content
JUNK_TAGS = {"script", "style", "noscript", "svg", "path", "iframe", "head"}

# Heuristic: headings signal page sections
HEADING_TAGS = ["h1", "h2", "h3", "h4"]

# Minimum character length to consider a paragraph meaningful
MIN_PARA_LEN = 40


def extract_headings(soup: BeautifulSoup) -> list:
    """Pull all headings in document order with their level."""
    headings = []
    for tag in soup.find_all(HEADING_TAGS):
        text = tag.get_text(separator=" ").strip()
        if text:
            headings.append({"level": tag.name, "text": text})
    return headings


def extract_main_content(soup: BeautifulSoup) -> list:
    """
    Heuristically score content blocks and return the most informative paragraphs.
    Prefers <main>, <article>, <section> over generic divs.
    Filters out boilerplate (nav, footer, sidebar).
    """
    # Remove noisy structural elements
    for tag in soup(["nav", "footer", "header", "aside"] + list(JUNK_TAGS)):
        tag.decompose()

    # Prefer semantic content containers
    container = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"content|main|body", re.I))
        or soup.find(class_=re.compile(r"content|main|body|post", re.I))
        or soup.body
    )

    if not container:
        return []

    paragraphs = []
    for p in container.find_all(["p", "li", "td", "dd", "blockquote"]):
        text = p.get_text(separator=" ").strip()
        # Filter short, copyright, or purely symbolic strings
        if (
            len(text) >= MIN_PARA_LEN
            and "©" not in text
            and "All Rights Reserved" not in text.lower()
            and not text.startswith("http")
        ):
            paragraphs.append(text)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for para in paragraphs:
        key = para[:80]
        if key not in seen:
            seen.add(key)
            unique.append(para)

    return unique


def extract_images(soup: BeautifulSoup, base_url: str) -> list:
    """Extract images that have meaningful alt text (skip decorative icons)."""
    images = []
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()
        src = img.get("src") or img.get("data-src") or ""
        if alt and len(alt) > 3 and src:
            full_src = urljoin(base_url, src)
            images.append({"src": full_src, "alt": alt})
    return images


def extract_tables(soup: BeautifulSoup) -> list:
    """Parse HTML tables into list-of-dict row format."""
    tables = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if cells:
                if headers and len(cells) == len(headers):
                    rows.append(dict(zip(headers, cells)))
                else:
                    rows.append(cells)
        if rows:
            tables.append({"headers": headers, "rows": rows})
    return tables


def extract_navigation(soup: BeautifulSoup, base_url: str) -> list:
    """Extract site navigation links for structural understanding."""
    nav_links = []
    seen = set()
    for nav in soup.find_all("nav"):
        for a in nav.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = urljoin(base_url, a["href"])
            if text and href not in seen:
                seen.add(href)
                nav_links.append({"label": text, "url": href})
    return nav_links


# ─────────────────────────────────────────────
#  CRAWL PRIORITY SCORING
# ─────────────────────────────────────────────

# URLs containing these keywords are crawled first (highest information density)
PRIORITY_KEYWORDS = [
    "contact", "about", "team", "people", "staff", "faculty",
    "profile", "member", "directory", "who-we-are", "our-team",
    "leadership", "board", "services", "products",
]


def score_url(url: str) -> int:
    """Return a crawl priority score — higher means crawl sooner."""
    path = urlparse(url).path.lower()
    score = 0
    for keyword in PRIORITY_KEYWORDS:
        if keyword in path:
            score += 1
    return score


# ─────────────────────────────────────────────
#  CORE CRAWLER
# ─────────────────────────────────────────────

def crawl(start_url: str, max_pages: int, delay: float, verbose: bool) -> list:
    logger = logging.getLogger("WebProfiler")
    target_domain = urlparse(start_url).netloc

    logger.info(f"Starting crawl → {start_url}")
    logger.info(f"Domain scope: {target_domain} | Max pages: {max_pages} | Delay: {delay}s")

    queue: list[tuple[int, str]] = [(-score_url(start_url), start_url)]
    visited: set[str] = set()
    results: list[dict] = []

    def normalize(url: str) -> str:
        return url.split("#")[0].rstrip("/")

    with sync_playwright() as pw:
        logger.info("Launching headless Chromium...")
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()

        while queue and len(visited) < max_pages:
            # Pop highest-priority URL (lowest negative score)
            queue.sort(key=lambda x: x[0])
            _, current_url = queue.pop(0)
            current_url = normalize(current_url)

            if current_url in visited:
                continue
            visited.add(current_url)

            logger.info(f"[{len(visited)}/{max_pages}] Scraping: {current_url}")

            try:
                page.goto(current_url, wait_until="networkidle", timeout=60000)
                # Trigger lazy-load by scrolling
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.5)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(delay)

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                # ── Extract all signals ──────────────────────────────────
                contacts = extract_contacts(soup, current_url)
                structured = extract_structured_data(soup)
                meta = extract_metadata(soup)
                headings = extract_headings(soup)
                content = extract_main_content(BeautifulSoup(html, "html.parser"))  # fresh parse (decompose mutates)
                images = extract_images(soup, current_url)
                tables = extract_tables(BeautifulSoup(html, "html.parser"))
                navigation = extract_navigation(soup, current_url)

                page_record = {
                    "url": current_url,
                    "scraped_at": datetime.utcnow().isoformat() + "Z",
                    "page_title": (page.title() or "").strip(),
                    "meta": meta,
                    "contacts": contacts,
                    "social_profiles": contacts.pop("social_profiles", {}),
                    "structured_data": structured,
                    "navigation": navigation,
                    "headings": headings,
                    "content_blocks": content,
                    "tables": tables,
                    "images": images,
                }

                # Move social_profiles to top level (cleaner schema)
                page_record["contacts"].pop("social_profiles", None)

                results.append(page_record)
                logger.debug(
                    f"  → emails={len(contacts['emails'])} | "
                    f"phones={len(contacts['phones'])} | "
                    f"blocks={len(content)} | tables={len(tables)}"
                )

                # ── Discover new URLs ────────────────────────────────────
                for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
                    href = a["href"].strip()
                    if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                        continue
                    abs_url = normalize(urljoin(current_url, href))
                    parsed = urlparse(abs_url)
                    if (
                        parsed.scheme in ("http", "https")
                        and parsed.netloc == target_domain
                        and abs_url not in visited
                        and not any(abs_url == u for _, u in queue)
                    ):
                        priority = -score_url(abs_url)
                        queue.append((priority, abs_url))

            except Exception as exc:
                logger.warning(f"  Skipped {current_url}: {exc}")
                continue

        browser.close()

    logger.info(f"Crawl complete. {len(results)} pages scraped.")
    return results


# ─────────────────────────────────────────────
#  OUTPUT BUILDER
# ─────────────────────────────────────────────

def build_output(start_url: str, pages: list) -> dict:
    """Wrap page records in a top-level report envelope."""

    # Aggregate all contacts across pages for a quick summary
    all_emails = sorted({e for p in pages for e in p["contacts"]["emails"]})
    all_phones = sorted({ph for p in pages for ph in p["contacts"]["phones"]})
    all_socials: dict = {}
    for p in pages:
        for platform, links in p.get("social_profiles", {}).items():
            all_socials.setdefault(platform, [])
            for link in links:
                if link not in all_socials[platform]:
                    all_socials[platform].append(link)

    return {
        "report": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "seed_url": start_url,
            "pages_scraped": len(pages),
            "aggregate": {
                "unique_emails": all_emails,
                "unique_phones": all_phones,
                "social_profiles": all_socials,
            },
        },
        "pages": pages,
    }


# ─────────────────────────────────────────────
#  CLI ENTRY POINT
# ─────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        prog="scraper.py",
        description="WebProfiler — Generic professional web scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", help="Seed URL to start crawling from")
    parser.add_argument(
        "--max-pages", type=int, default=10,
        help="Maximum number of pages to crawl (default: 10)",
    )
    parser.add_argument(
        "--output", default="scraped_output.json",
        help="Output JSON file path (default: scraped_output.json)",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="Seconds to wait between page loads (default: 1.0)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug-level logging",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    setup_logger(args.verbose)
    logger = logging.getLogger("WebProfiler")

    pages = crawl(
        start_url=args.url,
        max_pages=args.max_pages,
        delay=args.delay,
        verbose=args.verbose,
    )

    output = build_output(args.url, pages)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info(f"Results saved → {args.output}")
    logger.info(
        f"Summary: {len(output['report']['aggregate']['unique_emails'])} unique emails | "
        f"{len(output['report']['aggregate']['unique_phones'])} unique phones"
    )


if __name__ == "__main__":
    main()