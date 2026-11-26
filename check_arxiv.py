#!/usr/bin/env python3
"""Has arXiv announced papers that the published site doesn't have yet?

The GitHub job runs this every 10 minutes through the night and only rebuilds the site when the
answer is yes. It reads just the top of a couple of arXiv feeds and the site's small status file.
Prints "build=true" or "build=false" (the format GitHub Actions expects for step outputs); the
details go to stderr for the job log.

    python3 check_arxiv.py --site https://you.github.io/preprint-stack/
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from email.utils import parsedate_to_datetime

import server

# A few busy categories: they all refresh together, so any one of them shows a new announcement.
PROBE_FEEDS = ("hep-th", "cond-mat.str-el")


def read_head(url, size):
    request = urllib.request.Request(url, headers={"User-Agent": server.USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read(size).decode("utf-8", "replace")


def feed_date(category):
    """Announcement date of the feed, e.g. '2026-11-25', from its <pubDate> near the top."""
    match = re.search(r"<pubDate>([^<]+)</pubDate>", read_head(server.RSS_URL.format(category), 8000))
    return parsedate_to_datetime(match.group(1)).date().isoformat() if match else None


def published_date(site):
    url = f"{site.rstrip('/')}/data/status.json?t={int(time.time())}"  # skip GitHub Pages' 10-minute cache
    try:
        return json.loads(server.fetch(url)).get("date")
    except Exception:
        return None  # first build with a status file, or the site is down: build


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", required=True, help="address of the published site")
    args = parser.parse_args()

    dates = []
    for category in PROBE_FEEDS:
        try:
            dates.append(feed_date(category))
        except Exception as err:
            print(f"couldn't read the {category} feed: {server.describe_error(err)}", file=sys.stderr)
    arxiv = max((d for d in dates if d), default=None)
    site = published_date(args.site)
    build = arxiv is not None and (site is None or arxiv > site)
    print(f"arXiv feed: {arxiv}   site: {site}   ->  "
          f"{'new papers, rebuilding' if build else 'nothing new'}", file=sys.stderr)
    print(f"build={'true' if build else 'false'}")


if __name__ == "__main__":
    main()
