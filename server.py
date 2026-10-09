#!/usr/bin/env python3
"""Preprint Stack: swipe through today's arXiv papers like a deck of cards.

This small server hosts the web app in ./static and fetches arXiv on its behalf
at /api/feed (arXiv doesn't allow browsers to request its feeds directly).
It uses only the Python standard library.

    python3 server.py              # opens http://localhost:8000
    python3 server.py --lan        # also reachable from a phone on the same Wi-Fi
    python3 server.py --port 9000  # pick another port
"""
import argparse
import json
import re
import socket
import ssl
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"
RSS_URL = "https://rss.arxiv.org/rss/{}"
API_URL = "https://export.arxiv.org/api/query?{}"
USER_AGENT = "PreprintStack/1.0 (personal arXiv reader)"
CACHE_SECONDS = 15 * 60
MAX_TOPICS = 40

CATEGORY_RE = re.compile(r"^[a-z][a-z-]*(\.[A-Za-z-]+)?$")
TYPE_RANK = {"new": 0, "cross": 1, "replace": 2, "replace-cross": 3}
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# LaTeX accent commands that show up in arXiv metadata, mapped to combining marks.
SYMBOL_ACCENTS = {"'": "́", "`": "̀", "^": "̂", '"': "̈",
                  "~": "̃", "=": "̄", ".": "̇"}
LETTER_ACCENTS = {"c": "̧", "v": "̌", "u": "̆", "H": "̋",
                  "k": "̨", "r": "̊"}
SPECIAL_LETTERS = {"ss": "ß", "o": "ø", "O": "Ø", "l": "ł", "L": "Ł", "aa": "å",
                   "AA": "Å", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ", "i": "ı"}
SYMBOL_ACCENT_RE = re.compile(r"\{?\\([" + re.escape("".join(SYMBOL_ACCENTS)) + r"])\s*\{?(\\i|[A-Za-z])\}?\}?")
LETTER_ACCENT_RE = re.compile(r"\{?\\([cvuHkr])\{(\\i|[A-Za-z])\}\}?")
SPECIAL_LETTER_RE = re.compile(r"\{\\(ss|aa|AA|ae|AE|oe|OE|o|O|l|L)\}|\\(ss|aa|AA|ae|AE|oe|OE)(?![A-Za-z])")


def detex_accents(text):
    """Turn LaTeX accents like Schr\\"odinger or Universit\\'e into real characters."""
    def accent(table):
        def sub(m):
            base = "i" if m.group(2) == "\\i" else m.group(2)
            return unicodedata.normalize("NFC", base + table[m.group(1)])
        return sub
    text = SYMBOL_ACCENT_RE.sub(accent(SYMBOL_ACCENTS), text)
    text = LETTER_ACCENT_RE.sub(accent(LETTER_ACCENTS), text)
    return SPECIAL_LETTER_RE.sub(lambda m: SPECIAL_LETTERS[m.group(1) or m.group(2)], text)


def tex_typography(text):
    """Turn TeX quotes and dashes (``x'', ---, --) into real characters, leaving $math$ alone."""
    parts = re.split(r"(\$[^$]*\$)", text)
    for i in range(0, len(parts), 2):
        part = (parts[i].replace("``", "\u201c").replace("''", "\u201d")
                .replace("---", "\u2014").replace("--", "\u2013"))
        parts[i] = re.sub("\u201c([^\"\u201c\u201d]*)\"", "\u201c\\1\u201d", part)  # ``x" closed with a plain quote
    return "".join(parts)


def squash(text):
    return " ".join((text or "").split())


def split_authors(raw):
    """Split 'A (Univ X, Y), B' into ['A', 'B'], dropping affiliations."""
    names, depth, current = [], 0, []
    for ch in detex_accents(squash(raw)):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            if ch == ",":
                names.append("".join(current))
                current = []
            else:
                current.append(ch)
    names.append("".join(current))
    names = [squash(n.replace("{", "").replace("}", "")) for n in names]
    return [n for n in names if n]


# ---------------------------------------------------------------- fetching

_cache = {}
_cache_lock = threading.Lock()
_api_lock = threading.Lock()
_api_last_call = 0.0


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def cached(key, load):
    with _cache_lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = load()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def describe_error(err):
    if isinstance(err, urllib.error.HTTPError):
        return f"arXiv answered HTTP {err.code}"
    reason = getattr(err, "reason", err)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return ("Python can't verify HTTPS certificates. If you installed Python from python.org, "
                "run 'Install Certificates.command' in its Applications folder.")
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "arXiv took too long to answer"
    if isinstance(err, urllib.error.URLError):
        return f"couldn't reach arXiv ({reason})"
    return str(err) or err.__class__.__name__


def parse_rss(xml_bytes):
    channel = ET.fromstring(xml_bytes).find("channel")
    date = None
    if channel.findtext("pubDate"):
        date = parsedate_to_datetime(channel.findtext("pubDate")).date().isoformat()
    papers = []
    for item in channel.findall("item"):
        description = item.findtext("description") or ""
        id_match = re.search(r"arXiv:(\S+?)(v\d+)\b", description)
        if not id_match:
            continue
        abstract = description.split("Abstract:", 1)[-1]
        papers.append({
            "id": id_match.group(1),
            "version": id_match.group(2),
            "title": tex_typography(detex_accents(squash(item.findtext("title")))),
            "authors": split_authors(item.findtext("dc:creator", "", NS)),
            "abstract": tex_typography(detex_accents(squash(abstract))),
            "categories": [c.text for c in item.findall("category") if c.text],
            "type": item.findtext("arxiv:announce_type", "new", NS).strip(),
            "date": date,
        })
    return {"date": date, "papers": papers}


def parse_api(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise RuntimeError("arXiv is rate-limiting requests; try again in a minute") from None
    papers = []
    for entry in root.findall("atom:entry", NS):
        full_id = entry.findtext("atom:id", "", NS).rsplit("/abs/", 1)[-1]
        id_match = re.match(r"(.+?)(v\d+)?$", full_id)
        primary = entry.find("arxiv:primary_category", NS)
        categories = [c.get("term") for c in entry.findall("atom:category", NS)]
        if primary is not None and primary.get("term") in categories:
            categories.remove(primary.get("term"))
            categories.insert(0, primary.get("term"))
        published = entry.findtext("atom:published", "", NS)
        papers.append({
            "id": id_match.group(1),
            "version": id_match.group(2) or "v1",
            "title": tex_typography(detex_accents(squash(entry.findtext("atom:title", "", NS)))),
            "authors": [detex_accents(squash(a.findtext("atom:name", "", NS)))
                        for a in entry.findall("atom:author", NS)],
            "abstract": tex_typography(detex_accents(squash(entry.findtext("atom:summary", "", NS)))),
            "categories": categories,
            "type": "new",
            "date": published[:10] or None,
        })
    return papers


def rss_feed(category):
    return cached(("rss", category), lambda: parse_rss(fetch(RSS_URL.format(category))))


def latest_submissions(categories):
    """Most recent submissions via the arXiv API, for days without an announcement."""
    global _api_last_call
    query = urllib.parse.urlencode({
        "search_query": " OR ".join(f"cat:{c}" for c in categories),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": 150,
    })

    def load():
        global _api_last_call
        with _api_lock:  # arXiv asks API clients to wait 3 seconds between calls
            wait = 3 - (time.time() - _api_last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                return parse_api(fetch(API_URL.format(query)))
            finally:
                _api_last_call = time.time()

    return cached(("api", tuple(sorted(categories))), load)


def build_feed(categories):
    errors, feeds = [], []
    with ThreadPoolExecutor(max_workers=min(8, len(categories))) as pool:
        futures = [(c, pool.submit(rss_feed, c)) for c in categories]
        for category, future in futures:
            try:
                feeds.append(future.result())
            except Exception as err:  # one bad topic shouldn't sink the rest
                errors.append(f"{category}: {describe_error(err)}")

    # A paper can appear in several topic feeds; keep its most relevant listing
    # (a new submission beats a cross-list beats a replacement).
    by_id = {}
    for feed in feeds:
        for paper in feed["papers"]:
            current = by_id.get(paper["id"])
            if current is None or TYPE_RANK.get(paper["type"], 9) < TYPE_RANK.get(current["type"], 9):
                by_id[paper["id"]] = paper
    papers = sorted(by_id.values(), key=lambda p: TYPE_RANK.get(p["type"], 9))
    dates = [f["date"] for f in feeds if f["date"]]

    source = "announcement"
    if not papers:
        # Feeds are empty on weekends and holidays; fall back to recent submissions.
        try:
            papers = latest_submissions(categories)
            source = "latest"
        except Exception as err:
            errors.append(f"recent submissions: {describe_error(err)}")

    return {
        "source": source,
        "date": max(dates) if dates else None,
        "topics": categories,
        "papers": papers,
        "errors": errors,
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- web server

class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".webmanifest": "application/manifest+json"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/api/feed":
            return self.serve_feed(urllib.parse.parse_qs(url.query))
        return super().do_GET()

    def serve_feed(self, params):
        topics = [t.strip() for t in params.get("topics", [""])[0].split(",") if t.strip()]
        invalid = [t for t in topics if not CATEGORY_RE.match(t)]
        if not topics or invalid or len(topics) > MAX_TOPICS:
            message = f"Unknown topic codes: {', '.join(invalid)}" if invalid else "Choose between 1 and 40 topics."
            return self.send_json({"error": message}, status=400)
        self.send_json(build_feed(list(dict.fromkeys(topics))))

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_request(self, code="-", size="-"):
        try:
            failed = int(code) >= 400
        except (TypeError, ValueError):
            failed = False
        if failed or self.path.startswith("/api/"):  # keep the console quiet for static files
            super().log_request(code, size)


def lan_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))  # no packets are sent; this just picks the outgoing interface
            return s.getsockname()[0]
        except OSError:
            return "your-computer's-ip"


def main():
    parser = argparse.ArgumentParser(description="Swipe through today's arXiv papers.")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (default 8000)")
    parser.add_argument("--lan", action="store_true",
                        help="accept connections from other devices on your network, e.g. your phone")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab on start")
    args = parser.parse_args()

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    try:
        server = ThreadingHTTPServer((host, args.port), Handler)
    except OSError:
        raise SystemExit(f"Port {args.port} is busy. Try: python3 server.py --port {args.port + 1}")

    url = f"http://localhost:{args.port}"
    print(f"Preprint Stack is running at {url}")
    if args.lan:
        print(f"On your phone (same Wi-Fi): http://{lan_address()}:{args.port}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
