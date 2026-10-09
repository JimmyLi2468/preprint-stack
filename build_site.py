#!/usr/bin/env python3
"""Build the hosted version of Preprint Stack, e.g. for GitHub Pages.

A static host can't run server.py, so this fetches the latest announcement for
every arXiv category ahead of time and writes the app plus one JSON file per
category into an output folder:

    python3 build_site.py                       # writes _site/
    python3 build_site.py --previous https://you.github.io/preprint-stack/

--previous is the address of the currently published site. A category whose feed
is empty today (weekends, holidays) keeps the papers already published there.
"""
import argparse
import json
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parent
STATIC_MARKER = '<meta name="preprint-stack-data" content="static">'


def all_categories():
    text = (server.STATIC_DIR / "taxonomy.js").read_text(encoding="utf-8")
    groups = json.loads(text[text.index("["):text.rindex("]") + 1])
    return [code for group in groups for code, _name in group["cats"]]


def fetch_feed(code):
    error = None
    for attempt in range(3):
        try:
            return server.parse_rss(server.fetch(server.RSS_URL.format(code))), None
        except Exception as err:
            error = server.describe_error(err)
            time.sleep(3 * (attempt + 1))
    return {"date": None, "papers": []}, error


def build_category(code, previous_url):
    """Returns (feed, status) where status is fresh, kept, empty or failed."""
    feed, error = fetch_feed(code)
    if feed["papers"]:
        return feed, "fresh"
    if previous_url:
        try:
            published = json.loads(server.fetch(f"{previous_url.rstrip('/')}/data/{code}.json"))
            if published.get("papers"):
                published.pop("error", None)
                return published, "kept"
        except Exception:
            pass  # nothing published for this category yet
    if error:
        feed["error"] = error
        return feed, "failed"
    return feed, "empty"


def main():
    parser = argparse.ArgumentParser(description="Build the static, hostable version of Preprint Stack.")
    parser.add_argument("--out", default="_site", help="output folder (default: _site)")
    parser.add_argument("--previous", default="", help="address of the currently published site")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    if out in (ROOT, *ROOT.parents) or out == server.STATIC_DIR:
        sys.exit(f"Refusing to overwrite {out}; pick a separate output folder.")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(server.STATIC_DIR, out)

    # Tell the app to read the prebuilt data files instead of calling server.py.
    index = out / "index.html"
    html = index.read_text(encoding="utf-8")
    index.write_text(html.replace('<meta charset="utf-8">', f'<meta charset="utf-8">\n{STATIC_MARKER}', 1), encoding="utf-8")

    data_dir = out / "data"
    data_dir.mkdir()
    codes = all_categories()
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    statuses = Counter()
    failures = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for code, (feed, status) in zip(codes, pool.map(lambda c: build_category(c, args.previous), codes)):
            feed.setdefault("builtAt", built_at)
            payload = json.dumps({"category": code, **feed}, ensure_ascii=False, separators=(",", ":"))
            (data_dir / f"{code}.json").write_text(payload, encoding="utf-8")
            statuses[status] += 1
            if status == "failed":
                failures.append(f"{code} ({feed['error']})")

    size_mb = sum(f.stat().st_size for f in data_dir.iterdir()) / 1e6
    print(f"Built {len(codes)} categories into {out} in {time.time() - started:.0f}s ({size_mb:.1f} MB of data)")
    print("  " + ", ".join(f"{n} {status}" for status, n in statuses.most_common()))
    if failures:
        print("  Failed: " + "; ".join(failures))
    # If arXiv was unreachable, stop here rather than publish a site with no papers.
    if statuses["failed"] > len(codes) / 2:
        sys.exit("More than half of the categories failed to load; not publishing.")


if __name__ == "__main__":
    main()
