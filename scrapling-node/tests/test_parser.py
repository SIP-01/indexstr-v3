"""test_parser.py — fixture HTML → ParsedPage fields."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sip01_node.parser import MIN_WORD_COUNT, parse_page  # noqa: E402

WORDS = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod " * 3

FIXTURE = f"""<!doctype html>
<html lang="en">
<head>
  <title>Fallback Title</title>
  <meta property="og:title" content="OG Title Wins">
  <meta property="og:description" content="An OG description.">
  <meta property="og:image" content="/img/cover.png">
  <meta property="article:published_time" content="2024-03-01T12:00:00Z">
  <meta name="keywords" content="Rust, Web Crawling, nostr, BAD TAG!!, rust">
  <link rel="canonical" href="/post/hello-world">
</head>
<body>
  <article><p>{WORDS}</p></article>
  <a href="/about">About</a>
  <a href="https://other.example/x">External</a>
  <a href="#frag">Fragment</a>
  <a href="mailto:a@b.c">Mail</a>
</body>
</html>"""

BASE = "https://blog.example.com/post/hello-world"


def test_parse_extracts_og_first():
    page = parse_page(FIXTURE, BASE)
    assert page is not None
    assert page.title == "OG Title Wins"
    assert page.description == "An OG description."
    assert page.lang == "en"


def test_image_resolved_https_only():
    page = parse_page(FIXTURE, BASE)
    assert page.image == "https://blog.example.com/img/cover.png"


def test_http_image_dropped():
    html = FIXTURE.replace("/img/cover.png", "http://insecure.example/x.png")
    page = parse_page(html, BASE)
    assert page.image is None


def test_keywords_normalized_and_deduped():
    page = parse_page(FIXTURE, BASE)
    assert page is not None
    assert "rust" in page.keywords
    assert "web-crawling" in page.keywords
    assert page.keywords.count("rust") == 1
    assert len(page.keywords) <= 8
    assert all(k == k.lower() for k in page.keywords)


def test_published_timestamp():
    page = parse_page(FIXTURE, BASE)
    assert page.published == 1709294400  # 2024-03-01T12:00:00Z


def test_canonical_resolved():
    page = parse_page(FIXTURE, BASE)
    assert page.canonical == BASE


def test_links_absolute_http_only_deduped():
    page = parse_page(FIXTURE, BASE)
    assert "https://blog.example.com/about" in page.links
    assert "https://other.example/x" in page.links
    assert not any(l.startswith("mailto:") for l in page.links)
    assert len(page.links) == len(set(page.links))


def test_thin_content_returns_none():
    html = "<html><head><title>T</title></head><body><p>too few words</p></body></html>"
    assert parse_page(html, BASE) is None


def test_no_title_returns_none():
    html = f"<html><body><p>{WORDS}</p></body></html>"
    assert parse_page(html, BASE) is None


def test_word_count():
    page = parse_page(FIXTURE, BASE)
    assert page.word_count >= MIN_WORD_COUNT


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
