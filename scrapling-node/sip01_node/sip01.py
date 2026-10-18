#!/usr/bin/env python3
"""sip01.py — SIP-01 (Search Index Protocol, kind 39697) toolkit.

Zero-dependency Python port of src/lib/sip01-utils.ts, byte-compatible with
every ecosystem implementation (0xSearchstr / UNCAGED-ENGINE / Crawlstr /
UNCAGED-Index-Relay). URL normalization follows WHATWG URL semantics, matching
the JS `new URL()` reference implementation.

CLI:
  python3 sip01.py selftest                          # run spec §13 test vectors
  python3 sip01.py normalize '<url>'                 # normalized URL (or INVALID)
  python3 sip01.py d '<url>'                         # widx:... document id
  python3 sip01.py x '<title>' ['<description>']     # content hash
  python3 sip01.py build '<url>' '<title>' [--description D] [--topic T]...
        [--lang en] [--published UNIX] [--source name/1] [--image URL]
        [--type repository] [--platform github] [--category C] [--network N]
        [--country DE] [--mime application/pdf]
      → prints the unsigned kind-39697 event JSON (pipe into a Nostr signer,
        e.g. nostr_key.py sign from the `nostr` skill)
  python3 sip01.py validate '<event-json>'           # or pipe JSON via stdin
      → prints VALID/INVALID plus error and notice lines, exit code 0/1
"""
import hashlib
import json
import re
import sys
from urllib.parse import urlsplit, parse_qsl

SIP01_KIND = 39697
SIP01_SCHEMA_VERSION = "1"
SIP01_D_PREFIX = "widx:"

MAX_URL_LEN = 2048
MAX_TITLE_LEN = 300
MAX_DESCRIPTION_LEN = 1000
MAX_IMAGE_LEN = 2048
MAX_ALT_LEN = 1000
MAX_SOURCE_LEN = 100
MAX_TOPICS = 8

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "igshid", "ref_src",
    "spm", "si",
}

TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
EXTENSION_VALUE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,49}$")
MIME_RE = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9!#$&^_.+-]{0,126}/[a-zA-Z0-9][a-zA-Z0-9!#$&^_.+-]{0,126}"
    r"(;\s*[^\s;=]+=[^\s;]+)*$"
)

KNOWN_TAGS = {
    "d", "u", "v", "alt", "t", "l", "x", "published", "source",
    "type", "platform", "category", "network", "country", "mime",
}


# ---------------------------------------------------------------- WHATWG URL
def _encode_path(raw):
    """Re-serialize a URL path the way WHATWG URL does: keep existing
    %-escapes (normalized to uppercase), percent-encode the path encode set
    (C0 controls, space, \", #, <, >, ?, `, {, }) and all non-ASCII as UTF-8."""
    out = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "%" and i + 2 < len(raw) and re.match(r"[0-9a-fA-F]{2}", raw[i + 1:i + 3]):
            out.append(raw[i:i + 3].upper())
            i += 3
            continue
        b = c.encode("utf-8")
        if len(b) == 1 and 0x21 <= b[0] <= 0x7E and c not in '"#<>`{}?':
            out.append(c)
        else:
            out.extend("%%%02X" % byte for byte in b)
        i += 1
    return "".join(out)


def _form_encode(s):
    """application/x-www-form-urlencoded percent-encode set (WHATWG):
    keep A-Z a-z 0-9 * - . _ ; space -> '+' ; everything else %XX (UTF-8)."""
    out = []
    for byte in s.encode("utf-8"):
        c = chr(byte)
        if c.isalnum() and byte < 0x80 or c in "*-._":
            out.append(c)
        elif c == " ":
            out.append("+")
        else:
            out.append("%%%02X" % byte)
    return "".join(out)


def _resolve_dot_segments(path):
    """WHATWG URL dot-segment resolution ('.', '..', and their %2e forms)."""
    out = []
    segs = path.split("/")
    for i, seg in enumerate(segs):
        lowered = seg.lower().replace("%2e", ".")
        last = i == len(segs) - 1
        if lowered == ".":
            if last:
                out.append("")  # preserve trailing slash
            continue
        if lowered == "..":
            if len(out) > 1 or (out and out[0] != ""):
                out.pop()
            if last:
                out.append("")
            continue
        out.append(seg)
    return "/".join(out)


def normalize_index_url(raw):
    """Normalize a URL per SIP-01 §7. Returns None for invalid/non-http(s)."""
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    try:
        host = parts.hostname  # urlsplit lowercases hostname
        port = parts.port
    except ValueError:
        return None
    if not host:
        return None
    # IDNA (JS URL puny-codes internationalized hosts)
    try:
        host = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return None
    # 2. strip leading www.
    if host.startswith("www."):
        host = host[4:]
    # userinfo, preserved verbatim (JS URL keeps user:pass@)
    userinfo = ""
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += ":" + parts.password
        userinfo += "@"
    # 3. default ports
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = userinfo + host + ((":" + str(port)) if port is not None else "")
    # 4. fragment removed entirely (urlsplit already separates it)
    # 5-6. strip tracking params, keep the rest, stable-sort by key
    params = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    params.sort(key=lambda kv: kv[0])
    query = "&".join(_form_encode(k) + "=" + _form_encode(v) for k, v in params)
    # 7. dot-segment resolution (WHATWG parse behavior), then trailing slash
    path = _resolve_dot_segments(parts.path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if not path:
        path = "/"
    # 8. re-serialize
    out = scheme + "://" + netloc + _encode_path(path)
    if query:
        out += "?" + query
    return out


# ---------------------------------------------------------------- identities
def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_id(normalized_url):
    return SIP01_D_PREFIX + sha256_hex(normalized_url)[:32]


def content_hash(title, description=""):
    return sha256_hex(title + "\n" + description)


# ---------------------------------------------------------------- build
def build_event(url, title, description="", topics=None, lang=None,
                published=None, source=None, image=None, extensions=None,
                created_at=None):
    """Build an unsigned SIP-01 event dict. Raises ValueError on bad input."""
    normalized = normalize_index_url(url)
    if normalized is None:
        raise ValueError("url is not a valid http(s) URL")
    if len(normalized) > MAX_URL_LEN:
        raise ValueError("normalized url exceeds %d chars" % MAX_URL_LEN)
    title = title.strip()[:MAX_TITLE_LEN]
    if not title:
        raise ValueError("title must be 1-%d characters" % MAX_TITLE_LEN)
    description = (description or "").strip()[:MAX_DESCRIPTION_LEN]
    content = {"title": title}
    if description:
        content["description"] = description
    if image:
        if not image.lower().startswith("https://") or len(image) > MAX_IMAGE_LEN:
            raise ValueError("image must be an https URL")
        content["image"] = image
    tags = [
        ["d", document_id(normalized)],
        ["u", normalized],
        ["x", content_hash(title, description)],
        ["v", SIP01_SCHEMA_VERSION],
        ["alt", ("Web index observation: " + title)[:MAX_ALT_LEN]],
    ]
    for t in (topics or [])[:MAX_TOPICS]:
        tags.append(["t", t])
    if lang:
        tags.append(["l", lang])
    if published is not None:
        tags.append(["published", str(int(published))])
    if source:
        tags.append(["source", source[:MAX_SOURCE_LEN]])
    for name, value in (extensions or {}).items():
        tags.append([name, value])
    ev = {
        "kind": SIP01_KIND,
        "created_at": created_at,
        "content": json.dumps(content, separators=(",", ":"), ensure_ascii=False),
        "tags": tags,
    }
    if created_at is None:
        import time
        ev["created_at"] = int(time.time())
    return ev


# ---------------------------------------------------------------- validate
def _tag_values(ev, name):
    return [t[1] for t in ev.get("tags", []) if len(t) > 1 and t[0] == name and t[1]]


def _tag_value(ev, name):
    vs = _tag_values(ev, name)
    return vs[0] if vs else None


def validate_event(ev):
    """Mirror of validateSip01Event() — UNCAGED relay ingestion rules.
    Returns {"valid": bool, "errors": [...], "notices": [...]}."""
    errors, notices = [], []
    if ev.get("kind") != SIP01_KIND:
        return {"valid": False, "errors": ["wrong kind (expected %d, got %s)" % (SIP01_KIND, ev.get("kind"))], "notices": []}

    for name in ("d", "u"):
        n = len(_tag_values(ev, name))
        if n == 0:
            errors.append("missing %s tag" % name)
        elif n > 1:
            errors.append("multiple %s tags" % name)
    vs = _tag_values(ev, "v")
    if not vs:
        errors.append("missing v tag")
    elif len(vs) > 1:
        errors.append("multiple v tags")
    elif vs[0] != SIP01_SCHEMA_VERSION:
        errors.append('unsupported web document schema version "%s"' % vs[0])
    alts = [t for t in ev.get("tags", []) if len(t) > 0 and t[0] == "alt"]
    if not alts or not (len(alts[0]) > 1 and alts[0][1].strip()):
        errors.append("missing alt tag")
    elif len(alts) > 1:
        errors.append("multiple alt tags")
    elif len(alts[0][1]) > MAX_ALT_LEN:
        errors.append("alt tag exceeds %d characters" % MAX_ALT_LEN)

    u = _tag_value(ev, "u")
    d = _tag_value(ev, "d")
    if u is not None:
        if len(u) > MAX_URL_LEN:
            errors.append("u tag exceeds %d characters" % MAX_URL_LEN)
        normalized = normalize_index_url(u)
        if normalized is None:
            errors.append("u tag is not a valid http(s) URL")
        elif d is not None and len(_tag_values(ev, "d")) == 1:
            if d != document_id(normalized):
                errors.append("d tag does not match the normalized u tag (widx: + sha256(u)[0:32])")

    title, description = "", None
    raw_content = ev.get("content") or ""
    try:
        parsed = json.loads(raw_content)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("title"), str):
            errors.append("content is not valid JSON with a title")
        else:
            title = parsed["title"]
            if not title.strip() or len(title.strip()) > MAX_TITLE_LEN:
                errors.append("title must be 1-%d characters" % MAX_TITLE_LEN)
            if isinstance(parsed.get("description"), str):
                description = parsed["description"]
                if len(description) > MAX_DESCRIPTION_LEN:
                    errors.append("description exceeds %d characters" % MAX_DESCRIPTION_LEN)
            if isinstance(parsed.get("image"), str):
                if not parsed["image"].lower().startswith("https://"):
                    errors.append("image must be an https URL")
    except (json.JSONDecodeError, TypeError):
        errors.append("content is not valid JSON with a title")

    topics = [t for t in ev.get("tags", []) if len(t) > 0 and t[0] == "t"]
    if len(topics) > MAX_TOPICS:
        errors.append("more than %d topic tags" % MAX_TOPICS)
    for t in topics:
        if len(t) < 2 or not TOPIC_RE.match(t[1] or ""):
            errors.append("topic (t) tags must be lowercase alphanumeric words")
            break
    lang = _tag_value(ev, "l")
    if lang is not None and not re.match(r"^[a-z]{2}$", lang):
        errors.append("l tag is not a valid ISO 639-1 language code")
    x = _tag_value(ev, "x")
    if x is not None:
        if not re.match(r"^[0-9a-f]{64}$", x):
            errors.append("x tag must be a lowercase hex sha256 digest")
        elif title and x != content_hash(title, description or ""):
            errors.append("x tag does not match sha256(title + \\n + description)")
    published = _tag_value(ev, "published")
    if published is not None and not re.match(r"^\d{1,16}$", published):
        errors.append("published tag must be a unix timestamp in seconds")
    source = _tag_value(ev, "source")
    if source is not None and len(source) > MAX_SOURCE_LEN:
        errors.append("source tag exceeds %d characters" % MAX_SOURCE_LEN)
    for name in ("type", "platform", "category", "network"):
        v = _tag_value(ev, name)
        if v is not None and not EXTENSION_VALUE_RE.match(v):
            errors.append("%s tag is not a valid keyword" % name)
    country = _tag_value(ev, "country")
    if country is not None and not re.match(r"^[a-zA-Z]{2}$", country):
        errors.append("country tag must be an ISO 3166-1 alpha-2 code")
    mime = _tag_value(ev, "mime")
    if mime is not None and not MIME_RE.match(mime):
        errors.append("mime tag is not a valid MIME type")

    unknown = sorted({t[0] for t in ev.get("tags", []) if t and t[0] not in KNOWN_TAGS})
    if unknown:
        notices.append("unknown extension tag(s) ignored: " + ", ".join(unknown))
    return {"valid": not errors, "errors": errors, "notices": notices}


# ---------------------------------------------------------------- test vectors
_VECTORS_D = [
    ("https://example.com/", "https://example.com/", "widx:0f115db062b7c0dd030b16878c99dea5"),
    ("HTTPS://WWW.Example.Com:443/page/?b=2&utm_source=x&a=1#top",
     "https://example.com/page?a=1&b=2", "widx:f68176b3eb966bd682c3c6eadcc5fe44"),
    ("https://example.com/page", "https://example.com/page", "widx:3641c5f2274c5471278ab5bf1df6d185"),
    ("https://github.com/NostrDanish/Crwalstr", "https://github.com/NostrDanish/Crwalstr",
     "widx:cdfd4df8c01d609fc9cdf943afa80197"),
]
_VECTORS_X = [
    ("Example", None, "e1762f14d9924e37b32f1c81dfd256410af462f5136415c96877efa8c80345d0"),
    ("Example Page", "A page about examples.", "2a5cbdf44513f552fb571d6c6de2ddf16c5452b235cc887980b52898fb38e7c1"),
    ("Crwalstr — a browser-based web crawler for Nostr",
     "A browser-based web crawler that publishes SIP-01 web index observations.",
     "babd08c579e107b98a360a7f713d5d822bbd9f24087b86d98404db214f0e5500"),
]


def selftest():
    ok = True
    for raw, normalized, d in _VECTORS_D:
        got_n = normalize_index_url(raw)
        got_d = document_id(got_n) if got_n else None
        good = got_n == normalized and got_d == d
        ok &= good
        print("%s d-vector: %s" % ("PASS" if good else "FAIL", raw))
        if not good:
            print("   normalized: %r (want %r)" % (got_n, normalized))
            print("   d:          %r (want %r)" % (got_d, d))
    for title, desc, x in _VECTORS_X:
        got = content_hash(title, desc or "")
        good = got == x
        ok &= good
        print("%s x-vector: %r" % ("PASS" if good else "FAIL", title[:40]))
        if not good:
            print("   got %s want %s" % (got, x))
    print("ALL VECTORS PASS" if ok else "SOME VECTORS FAILED")
    return ok


# ---------------------------------------------------------------- CLI
def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "help"
    if cmd == "selftest":
        sys.exit(0 if selftest() else 1)
    elif cmd == "normalize":
        print(normalize_index_url(args[1]) or "INVALID")
    elif cmd == "d":
        n = normalize_index_url(args[1])
        if not n:
            sys.exit("INVALID URL")
        print(document_id(n))
    elif cmd == "x":
        print(content_hash(args[1], args[2] if len(args) > 2 else ""))
    elif cmd == "build":
        kw = {"topics": [], "extensions": {}}
        positional = []
        i = 1
        flag_map = {"--description": "description", "--lang": "lang", "--published": "published",
                    "--source": "source", "--image": "image"}
        ext_flags = {"--type": "type", "--platform": "platform", "--category": "category",
                     "--network": "network", "--country": "country", "--mime": "mime"}
        while i < len(args):
            a = args[i]
            if a == "--topic":
                kw["topics"].append(args[i + 1]); i += 2
            elif a in flag_map:
                kw[flag_map[a]] = args[i + 1]; i += 2
            elif a in ext_flags:
                kw["extensions"][ext_flags[a]] = args[i + 1]; i += 2
            else:
                positional.append(a); i += 1
        ev = build_event(positional[0], positional[1], **kw)
        print(json.dumps(ev, ensure_ascii=False))
    elif cmd == "validate":
        raw = args[1] if len(args) > 1 else sys.stdin.read()
        result = validate_event(json.loads(raw))
        print("VALID" if result["valid"] else "INVALID")
        for e in result["errors"]:
            print("error: " + e)
        for n in result["notices"]:
            print("notice: " + n)
        sys.exit(0 if result["valid"] else 1)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
