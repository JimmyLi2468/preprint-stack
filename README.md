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
| Save to your reading list (tap again to unsave) | Save button | S |
| Open the PDF | PDF button | P |
| Share | Share button | |
| Scroll the abstract | scroll | ↑ ↓ |

The app remembers where you stopped: reopening it starts at the first paper you haven't moved past, and earlier papers are still a swipe away.

**Settings** has your topics (any arXiv category), whether to include cross-lists and replacements, priority keywords (matching papers go first and are highlighted), whether to start where you left off, and light or dark mode.

## Share it with friends (GitHub Pages)

The workflow in `.github/workflows/publish.yml` publishes a hosted copy that anyone with the link can use:

- Every weekday at 05:30 UTC, shortly after arXiv refreshes its feeds, it runs `build_site.py`. That downloads the latest announcement for every arXiv category (about 8 MB of data) and deploys it with the app to GitHub Pages.
- It also runs on every push to `main`, and you can start it by hand from the repository's Actions tab.
- On weekends and holidays the site keeps showing the most recent announcement.
- Each person picks their own topics. Settings and saved papers stay in each person's own browser.

To set it up, push this folder to a GitHub repository, then under **Settings → Pages** set **Source** to **GitHub Actions**. The site appears at `https://<your-username>.github.io/<repository>/`.

GitHub pauses scheduled workflows in a repository that has had no commits for 60 days. If the site stops updating, open the Actions tab and re-enable the workflow.

To try the hosted build on your computer: `python3 build_site.py`, then serve the `_site` folder with any static file server.

## How it works

- Each day's papers come from arXiv's announcement RSS feeds (`rss.arxiv.org`), one feed per topic. A paper that shows up in several of your topics appears once.
- arXiv announces new papers Sunday through Thursday at 8 pm US Eastern. On days without an announcement, the stack falls back to the most recent submissions from the arXiv API.
- The server exists because arXiv doesn't let browsers fetch its feeds directly. It caches each feed for 15 minutes.
- Your settings, saved papers and reading position are stored in your browser (localStorage). They stay on the device and browser where you made them.

## Files

- `server.py`: local web server and arXiv fetching
- `build_site.py`: builds the hosted copy with every category's papers prefetched
- `.github/workflows/publish.yml`: rebuilds and publishes the hosted copy each weekday
- `static/index.html`, `static/app.css`, `static/app.js`: the app
- `static/taxonomy.js`: arXiv's category list, generated from https://arxiv.org/category_taxonomy
