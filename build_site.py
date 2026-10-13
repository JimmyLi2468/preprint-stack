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

import affiliations
import server

ROOT = Path(__file__).resolve().parent
STATIC_MARKER = '<meta name="preprint-stack-data" content="static">'
# Categories whose papers (including cross-lists into them) get author affiliations.
AFFILIATION_TOPICS = "hep-th,cond-mat.str-el"
INDEX_PATH = ROOT / "index" / "authors.json"
OVERRIDES_PATH = ROOT / "index" / "overrides.json"


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


def published_affiliations(previous_url, codes):
    """Affiliations the published site already has, so a rebuild doesn't fetch the papers again."""
    known = {}
    for code in codes if previous_url else []:
        try:
            published = json.loads(server.fetch(f"{previous_url.rstrip('/')}/data/{code}.json"))
        except Exception:
            continue
        for paper in published.get("papers", []):
            if "affiliations" in paper:
                known[paper["id"] + paper["version"]] = paper["affiliations"]
    return known


def from_paper(entry):
    return bool(entry) and "asOf" not in entry and "note" not in entry


def add_affiliations(feeds, topics, previous_url, index, today):
    """Attach an affiliations list (one entry per author, or null) to papers in the chosen topics."""
    targets = {}
    for code in topics:
        for paper in feeds.get(code, {}).get("papers", []):
            targets.setdefault(paper["id"], paper)
    known = published_affiliations(previous_url, topics)
    fetcher = affiliations.Fetcher()
    stats = Counter()
    results = {}
    for paper_id, paper in targets.items():
        key = paper["id"] + paper["version"]
        if "affiliations" in paper:          # carried over from the published site (weekends)
            entries = paper["affiliations"]
        elif key in known:                   # worked out earlier today
            entries = known[key]
            stats["reused"] += 1
        elif paper["type"] in ("new", "cross"):
            found = affiliations.paper_affiliations(paper, fetcher)
            entries = [{"inst": found[a]} if found.get(a) else None for a in paper["authors"]]
            stats["read"] += 1
            if stats["read"] % 25 == 0:
                print(f"  read {stats['read']} of up to {len(targets)} papers", flush=True)
        else:                                # updated versions of older papers: rely on the index
            entries = [None] * len(paper["authors"])
        day = paper.get("date") or today
        for author, entry in zip(paper["authors"], entries):
            if from_paper(entry):
                index.add(author, entry["inst"], day, paper["id"], paper["categories"])
        entries = [entry if from_paper(entry) else index.fallback(author, paper["categories"], today)
                   for author, entry in zip(paper["authors"], entries)]
        results[paper_id] = entries
        stats["authors"] += len(entries)
        stats["from paper"] += sum(1 for e in entries if from_paper(e))
        stats["from index"] += sum(1 for e in entries if e and not from_paper(e))
    for feed in feeds.values():
        for paper in feed.get("papers", []):
            if paper["id"] in results:
                paper["affiliations"] = results[paper["id"]]
    return len(targets), stats


def main():
    parser = argparse.ArgumentParser(description="Build the static, hostable version of Preprint Stack.")
    parser.add_argument("--out", default="_site", help="output folder (default: _site)")
    parser.add_argument("--previous", default="", help="address of the currently published site")
    parser.add_argument("--affiliations", default=AFFILIATION_TOPICS,
                        help="comma-separated categories to find author affiliations for ('' to skip)")
    parser.add_argument("--index", default=str(INDEX_PATH), help="author index file to read and update")
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
    feeds = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for code, (feed, status) in zip(codes, pool.map(lambda c: build_category(c, args.previous), codes)):
            feed.setdefault("builtAt", built_at)
            feeds[code] = feed
            statuses[status] += 1
            if status == "failed":
                failures.append(f"{code} ({feed['error']})")
    print(f"Fetched {len(codes)} categories in {time.time() - started:.0f}s")
    print("  " + ", ".join(f"{n} {status}" for status, n in statuses.most_common()))
    if failures:
        print("  Failed: " + "; ".join(failures))
    # If arXiv was unreachable, stop here rather than publish a site with no papers.
    if statuses["failed"] > len(codes) / 2:
        sys.exit("More than half of the categories failed to load; not publishing.")

    topics = [t.strip() for t in args.affiliations.split(",") if t.strip() in feeds]
    if topics:
        started = time.time()
        index = affiliations.AuthorIndex(args.index, OVERRIDES_PATH)
        today = datetime.now(timezone.utc).date().isoformat()
        count, stats = add_affiliations(feeds, topics, args.previous, index, today)
        index.save()
        print(f"Affiliations for {count} papers in {', '.join(topics)} in {time.time() - started:.0f}s: "
              f"read {stats['read']}, reused {stats['reused']}; {stats['from paper']} of {stats['authors']} authors "
              f"from the paper, {stats['from index']} from the author index")

    for code, feed in feeds.items():
        payload = json.dumps({"category": code, **feed}, ensure_ascii=False, separators=(",", ":"))
        (data_dir / f"{code}.json").write_text(payload, encoding="utf-8")
    size_mb = sum(f.stat().st_size for f in data_dir.iterdir()) / 1e6
    print(f"Wrote {len(codes)} categories to {out} ({size_mb:.1f} MB of data)")


if __name__ == "__main__":
    main()
