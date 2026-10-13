# Preprint Stack

Swipe through each day's arXiv papers like a deck of cards. Every card shows the title, authors, subjects and abstract, with buttons to open the PDF or share the paper.

## Run it

```bash
python3 server.py
```

That opens http://localhost:8000. On a Mac you can also double-click **Preprint Stack.command**.
No installs needed: the server uses only the Python standard library.

To use it on your phone, start it with `python3 server.py --lan` and open the address it prints from a phone on the same Wi-Fi.

## Using it

| Action | Touch | Keyboard |
| --- | --- | --- |
| Next paper | swipe left | → |
| Previous paper | swipe right | ← |
| Save to your reading list (tap again to unsave) | bookmark button, or double-tap the card | S |
| Open the PDF | PDF button | P |
| Share | share button | |
| See every paper in today's stack, and jump to one | list button at the top | |
| Scroll the abstract | scroll | ↑ ↓ |

On an iPhone, open the site in Safari, tap Share, then **Add to Home Screen**. It then opens full screen like an app, with its own icon.

The app remembers where you stopped: reopening it starts at the first paper you haven't moved past, and earlier papers are still a swipe away.

**Settings** has your topics (any arXiv category), whether to include cross-lists and replacements, priority keywords (matching papers go first and are highlighted), whether to start where you left off, light or dark mode, and the abstract text size.

## Share it with friends (GitHub Pages)

The workflow in `.github/workflows/publish.yml` publishes a hosted copy that anyone with the link can use:

- Every weekday at 05:30 UTC, shortly after arXiv refreshes its feeds, it runs `build_site.py`. That downloads the latest announcement for every arXiv category (about 8 MB of data) and deploys it with the app to GitHub Pages.
- It also runs on every push to `main`, and you can start it by hand from the repository's Actions tab.
- On weekends and holidays the site keeps showing the most recent announcement.
- Each person picks their own topics. Settings and saved papers stay in each person's own browser.

To set it up, push this folder to a GitHub repository, then under **Settings → Pages** set **Source** to **GitHub Actions**. The site appears at `https://<your-username>.github.io/<repository>/`.

GitHub pauses scheduled workflows in a repository that has had no commits for 60 days. If the site stops updating, open the Actions tab and re-enable the workflow.

To try the hosted build on your computer: `python3 build_site.py`, then serve the `_site` folder with any static file server.

## Author affiliations

For papers in hep-th and cond-mat.str-el, including papers cross-listed into them, author names with a dotted underline show their institution when tapped.

- The daily job reads affiliations from each paper's title page in the LaTeX source, then arXiv's HTML version for anyone still missing. In a comparison on a day of hep-th and cond-mat.str-el papers, the source answered more authors and made fewer mistakes than the HTML or the PDF. It only keeps confident matches and leaves the rest out rather than guessing.
- When a paper doesn't list someone's institution, the app shows the most recent one seen on their other papers from the last ~18 months, labeled "Last listed" with the date. Those come from `index/authors.json`, which the job adds to and saves back to the repository each day. That daily save also keeps GitHub from pausing the job.
- To fix a mistake, add the person to `index/overrides.json`.
- To change which categories get affiliations, edit `AFFILIATION_TOPICS` in `build_site.py`. Each category adds a few minutes to the build.

## How it works

- Each day's papers come from arXiv's announcement RSS feeds (`rss.arxiv.org`), one feed per topic. A paper that shows up in several of your topics appears once.
- arXiv announces new papers Sunday through Thursday at 8 pm US Eastern. On days without an announcement, the stack falls back to the most recent submissions from the arXiv API.
- The server exists because arXiv doesn't let browsers fetch its feeds directly. It caches each feed for 15 minutes.
- Your settings, saved papers and reading position are stored in your browser (localStorage). They stay on the device and browser where you made them.

## Files

- `server.py`: local web server and arXiv fetching
- `build_site.py`: builds the hosted copy with every category's papers prefetched
- `affiliations.py`: reads author affiliations from papers and keeps the author index
- `index/authors.json`, `index/overrides.json`: the author index and manual corrections
- `.github/workflows/publish.yml`: rebuilds and publishes the hosted copy each weekday
- `static/index.html`, `static/app.css`, `static/app.js`: the app
- `static/taxonomy.js`: arXiv's category list, generated from https://arxiv.org/category_taxonomy
