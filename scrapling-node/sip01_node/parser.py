"""parser.py — Scrapling-based content extraction → ParsedPage.

Extraction semantics mirror the TypeScript v3 nodes: OpenGraph/meta tags
first, HTML fallbacks second; ``article:published_time`` for the published
timestamp; canonical link resolution; same-page text for the thin-content
gate (< 10 words → page is not worth indexing → ``None``).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

log = logging.getLogger(__name__)

MIN_WORD_COUNT = 10
MAX_KEYWORDS = 8

_WORD_RE = re.compile(r"\S+")


@dataclass
class ParsedPage:
    """Structured content extracted from one fetched page."""

    url: str
    title: str
    description: str = ""
    image: str | None = None
    lang: str | None = None
    keywords: list[str] = field(default_factory=list)
    published: int | None = None
    canonical: str | None = None
    links: list[str] = field(default_factory=list)
    text: str = ""
    word_count: int = 0


def _first(adaptor, *selectors: str) -> str | None:
    """First non-empty ``::text`` / ``::attr`` extraction across selectors."""
    for sel in selectors:
        try:
            val = adaptor.css(sel).get()
        except Exception:  # pragma: no cover - defensive, scrapling is forgiving
            continue
        if val and val.strip():
            return val.strip()
    return None


def _parse_published(raw: str | None) -> int | None:
    """Parse an ISO-8601 / RFC-2822 date string into a unix timestamp."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
    except (TypeError, ValueError):
        pass
    return None


def _keywords(raw: str | None) -> list[str]:
    """Split a meta keywords blob into <= MAX_KEYWORDS lowercase topics."""
    if not raw:
        return []
    out: list[str] = []
    for part in raw.split(","):
        word = re.sub(r"[^a-z0-9-]+", "-", part.strip().lower()).strip("-")
        # SIP-01 topic tags: ^[a-z0-9][a-z0-9-]{0,99}$
        if word and re.match(r"^[a-z0-9][a-z0-9-]{0,99}$", word) and word not in out:
            out.append(word)
        if len(out) >= MAX_KEYWORDS:
            break
    return out


def parse_page(html: str, url: str) -> ParsedPage | None:
    """Extract a :class:`ParsedPage` from raw HTML.

    Returns ``None`` when the page is thin content (< MIN_WORD_COUNT words)
    or has no usable title — such pages are not indexed.
    """
    from scrapling.parser import Adaptor

    doc = Adaptor(html, url=url)

    title = _first(
        doc,
        'meta[property="og:title"]::attr(content)',
        'meta[name="twitter:title"]::attr(content)',
        "title::text",
        "h1::text",
    )
    description = _first(
        doc,
        'meta[property="og:description"]::attr(content)',
        'meta[name="description"]::attr(content)',
        'meta[name="twitter:description"]::attr(content)',
    ) or ""

    image = _first(
        doc,
        'meta[property="og:image"]::attr(content)',
        'meta[name="twitter:image"]::attr(content)',
    )
    if image:
        image = urljoin(url, image)
        if not image.lower().startswith("https://"):
            image = None  # SIP-01 requires https image URLs

    lang_raw = _first(doc, "html::attr(lang)", 'meta[http-equiv="content-language"]::attr(content)')
    lang = lang_raw[:2].lower() if lang_raw and re.match(r"^[a-zA-Z]{2}", lang_raw) else None

    keywords = _keywords(_first(doc, 'meta[name="keywords"]::attr(content)'))

    published = _parse_published(_first(
        doc,
        'meta[property="article:published_time"]::attr(content)',
        'meta[name="date"]::attr(content)',
        'meta[name="pubdate"]::attr(content)',
        'meta[itemprop="datePublished"]::attr(content)',
        "time[datetime]::attr(datetime)",
    ))

    canonical = _first(doc, 'link[rel="canonical"]::attr(href)')
    if canonical:
        canonical = urljoin(url, canonical)

    links: list[str] = []
    seen: set[str] = set()
    for href in doc.css("a::attr(href)").getall():
        if not href:
            continue
        abs_url = urljoin(url, href.strip())
        scheme = urlsplit(abs_url).scheme.lower()
        if scheme in ("http", "https") and abs_url not in seen:
            seen.add(abs_url)
            links.append(abs_url)

    text = ""
    try:
        body = doc.css("body").get()
        text = doc.get_all_text(strip=True) if body else ""
    except Exception:  # pragma: no cover - defensive
        text = doc.get_all_text(strip=True)
    word_count = len(_WORD_RE.findall(text or ""))

    if not title:
        log.info("no title for %s — skipping", url)
        return None
    if word_count < MIN_WORD_COUNT:
        log.info("thin content (%d words) for %s — skipping", word_count, url)
        return None

    return ParsedPage(
        url=url,
        title=title,
        description=description,
        image=image,
        lang=lang,
        keywords=keywords,
        published=published,
        canonical=canonical,
        links=links,
        text=text,
        word_count=word_count,
    )
