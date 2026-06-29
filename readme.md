# WebProfiler — Generic Professional Web Scraper

Crawls any website and extracts all semantically useful data using heuristic
scoring. No hardcoded site assumptions, formats, or domains.

---

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Usage

```bash
# Basic — crawl up to 10 pages
python scraper.py https://example.com

# Larger crawl with custom output file
python scraper.py https://example.com --max-pages 50 --output my_results.json

# Slow down requests (polite crawling) + debug logs
python scraper.py https://example.com --delay 2.0 --verbose
```

### All Options

| Flag           | Default              | Description                            |
|----------------|----------------------|----------------------------------------|
| `url`          | *(required)*         | Seed URL to start crawling from        |
| `--max-pages`  | `10`                 | Max pages to crawl                     |
| `--output`     | `scraped_output.json`| Output file path                       |
| `--delay`      | `1.0`                | Seconds between page loads             |
| `--verbose`    | off                  | Enable debug-level logging             |

---

## Output Schema

```json
{
  "report": {
    "generated_at": "2024-01-01T00:00:00Z",
    "seed_url": "https://example.com",
    "pages_scraped": 5,
    "aggregate": {
      "unique_emails": ["contact@example.com"],
      "unique_phones": ["+1-800-555-0100"],
      "social_profiles": {
        "linkedin": ["https://linkedin.com/company/example"],
        "twitter": ["https://twitter.com/example"]
      }
    }
  },
  "pages": [
    {
      "url": "https://example.com/about",
      "scraped_at": "2024-01-01T00:00:00Z",
      "page_title": "About Us",
      "meta": { "description": "...", "keywords": "..." },
      "contacts": {
        "emails": ["info@example.com"],
        "phones": ["+1-800-555-0100"]
      },
      "social_profiles": { "linkedin": ["..."] },
      "structured_data": [ { "source": "json_ld", "data": {} } ],
      "navigation": [ { "label": "Home", "url": "https://example.com" } ],
      "headings": [ { "level": "h1", "text": "About Us" } ],
      "content_blocks": ["Company founded in 1990..."],
      "tables": [ { "headers": ["Name", "Role"], "rows": [ {...} ] } ],
      "images": [ { "src": "https://...", "alt": "CEO portrait" } ]
    }
  ]
}
```

---

## What It Extracts

| Signal              | Details                                                  |
|---------------------|----------------------------------------------------------|
| **Emails**          | From text body, `mailto:` links — validated by RFC regex |
| **Phones**          | International format, 7–15 digits, false-positive filtered |
| **Social profiles** | LinkedIn, Twitter/X, GitHub, Facebook, Instagram, YouTube, ResearchGate, ORCID, Telegram, WhatsApp, and more |
| **Structured data** | JSON-LD schemas, OpenGraph tags, Twitter Card meta       |
| **Metadata**        | All `<meta>` tags (description, keywords, author, etc.)  |
| **Navigation**      | All `<nav>` links with labels                            |
| **Headings**        | H1–H4 in document order                                  |
| **Content blocks**  | Main body paragraphs, filtered for boilerplate           |
| **Tables**          | Parsed into header+rows JSON                             |
| **Images**          | With meaningful alt text only                            |

---

## Crawl Behaviour

- **Domain-locked**: only follows links within the seed domain
- **Priority queue**: contact, about, team, profile, directory pages crawled first
- **Lazy-load support**: scrolls page before extracting to trigger dynamic content
- **Deduplication**: URLs normalised (strips `#fragments` and trailing slashes)
- **Polite**: configurable delay between requests
