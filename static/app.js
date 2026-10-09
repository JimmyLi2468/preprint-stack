'use strict';

// ================================================================ storage

const STORAGE_PREFIX = 'preprint-stack.';
const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(STORAGE_PREFIX + key);
      return raw ? JSON.parse(raw) : fallback;
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value)); } catch { /* storage full or blocked */ }
  },
};

const DEFAULT_PREFS = {
  topics: [],
  include: { new: true, cross: true, replace: false },
  keywords: '',
  resume: true,
  theme: 'system',
  abstractSize: 2,
};

const prefs = (() => {
  const stored = store.get('prefs', {});
  const p = Object.assign({}, DEFAULT_PREFS, stored);
  p.include = Object.assign({}, DEFAULT_PREFS.include, p.include);
  if (!Array.isArray(p.topics)) p.topics = [];
  if ('hideSeen' in stored && !('resume' in stored)) p.resume = stored.hideSeen; // older setting name
  delete p.hideSeen;
  return p;
})();
const savePrefs = () => store.set('prefs', prefs);

let seen = store.get('seen', {});    // "2610.08792v1" -> when you moved past it
let saved = store.get('saved', []);  // saved papers, newest first
const saveSeen = () => store.set('seen', seen);
const saveSaved = () => store.set('saved', saved);

// Forget papers seen more than four months ago so storage doesn't grow forever.
(() => {
  const cutoff = Date.now() - 120 * 864e5;
  const before = Object.keys(seen).length;
  for (const key in seen) if (seen[key] < cutoff) delete seen[key];
  if (Object.keys(seen).length !== before) saveSeen();
})();

// ================================================================ helpers

const $ = (selector, root = document) => root.querySelector(selector);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const paperKey = p => p.id + p.version;
const absUrl = p => `https://arxiv.org/abs/${p.id}`;
const pdfUrl = p => `https://arxiv.org/pdf/${p.id}`;
const kindOf = type => (type === 'new' ? 'new' : type === 'cross' ? 'cross' : 'replace');
const isSaved = p => saved.some(s => s.id === p.id);
const CATEGORY_NAMES = new Map(window.ARXIV_TAXONOMY.flatMap(group => group.cats));
const TYPE_LABEL = { new: 'New', cross: 'Cross-list', replace: 'Replacement' };
const AUTHOR_PREVIEW = 8;

const ICONS = {
  pdf: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/></svg>',
  bookmark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 3.5h11v17l-5.5-4-5.5 4z"/></svg>',
  share: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V3"/><path d="m7.5 7.5 4.5-4.5 4.5 4.5"/><path d="M6 11H5v9h14v-9h-1"/></svg>',
};

function parseDay(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}
const longDay = iso => parseDay(iso)?.toLocaleDateString('en-US', { weekday: 'long', day: 'numeric', month: 'long' }) ?? '';
const barDay = iso => parseDay(iso)?.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' }) ?? '';
const shortDay = ms => new Date(ms).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
const ABSTRACT_SCALES = [0.82, 0.91, 1, 1.12, 1.26];

/** "High Energy Physics - Theory (hep-th); General Relativity and ..." with the primary subject in bold. */
function subjectsLine(paper) {
  return paper.categories.map((code, i) => {
    const label = esc(CATEGORY_NAMES.has(code) ? `${CATEGORY_NAMES.get(code)} (${code})` : code);
    return i === 0 ? `<b>${label}</b>` : label;
  }).join('; ');
}

const authorsShort = p => (p.authors.length > 3 ? `${p.authors.slice(0, 3).join(', ')} et al.` : p.authors.join(', '));

function keywordPattern() {
  const words = prefs.keywords.split(',').map(w => w.trim()).filter(Boolean);
  if (!words.length) return null;
  const alternatives = words
    .sort((a, b) => b.length - a.length)
    .map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  return new RegExp(`(${alternatives.join('|')})`, 'gi');
}

function keywordHits(paper, pattern) {
  if (!pattern) return [];
  const found = new Set();
  for (const m of `${paper.title} ${paper.abstract}`.matchAll(pattern)) found.add(m[0].toLowerCase());
  return [...found];
}

/** Put text into an element, highlighting keyword matches, then typeset any $math$. */
function fillText(el, text, pattern) {
  el.textContent = '';
  if (!pattern) el.textContent = text;
  else text.split(pattern).forEach((part, i) => {
    if (!part) return;
    if (i % 2) {
      const mark = document.createElement('mark');
      mark.textContent = part;
      el.append(mark);
    } else el.append(part);
  });
  el.classList.add('js-math');
  renderMath(el);
}

function fillAuthors(el, authors) {
  if (authors.length <= AUTHOR_PREVIEW + 1) {
    el.textContent = authors.join(', ');
    return;
  }
  el.textContent = `${authors.slice(0, AUTHOR_PREVIEW).join(', ')}, `;
  const more = document.createElement('button');
  more.type = 'button';
  more.className = 'link-button';
  more.textContent = `+${authors.length - AUTHOR_PREVIEW} more`;
  more.addEventListener('click', () => { el.textContent = authors.join(', '); });
  el.append(more);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Clipboard API is unavailable over plain http (e.g. on a phone via --lan); use the old way.
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* ignore */ }
    area.remove();
    return ok;
  }
}

let toastTimer;
function toast(message, action) {
  const box = $('#toast');
  const button = $('#toast-action');
  $('#toast-text').textContent = message;
  button.hidden = !action;
  button.onclick = null;
  if (action) {
    button.textContent = action.label;
    button.onclick = () => { box.classList.remove('show'); action.run(); };
  }
  box.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove('show'), action ? 5000 : 2400);
}

// ================================================================ math (KaTeX, optional)

const KATEX_BASE = 'https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/';
let mathReady = false;

function renderMath(el) {
  if (!mathReady || el.dataset.math === 'done') return;
  el.dataset.math = 'done';
  if (!/\$|\\\(/.test(el.textContent)) return;
  try {
    window.renderMathInElement(el, {
      delimiters: [
        { left: '$$', right: '$$', display: false },
        { left: '$', right: '$', display: false },
        { left: '\\(', right: '\\)', display: false },
      ],
      throwOnError: false,
    });
  } catch { /* leave the LaTeX source visible */ }
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = resolve;
    script.onerror = reject;
    document.head.append(script);
  });
}

async function loadMath() {
  try {
    await loadScript(`${KATEX_BASE}katex.min.js`);
    await loadScript(`${KATEX_BASE}contrib/auto-render.min.js`);
    mathReady = true;
    document.querySelectorAll('.js-math').forEach(renderMath);
  } catch { /* offline: math stays as LaTeX source */ }
}

// ================================================================ feed + stack

let feed = null;          // { key, data, loadedAt }
let stack = [];           // today's papers in reading order
let index = 0;            // position of the top card; stack.length means "reached the end"
let stackDirty = false;   // settings changed in a way that needs a rebuild
let loadToken = 0;

const topicsKey = () => [...prefs.topics].sort().join(',');

// The hosted copy (built by build_site.py) reads one prebuilt file per topic;
// the local copy asks server.py, which fetches arXiv live.
const HOSTED = document.querySelector('meta[name="preprint-stack-data"]')?.content === 'static';
const TYPE_RANK = { new: 0, cross: 1, replace: 2, 'replace-cross': 3 };
const rank = paper => TYPE_RANK[paper.type] ?? 9;

async function fetchServerFeed(topics) {
  const res = await fetch(`api/feed?topics=${encodeURIComponent(topics.join(','))}`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `The server answered with an error (${res.status}).`);
  return data;
}

async function fetchPublishedFeed(topics) {
  const results = await Promise.all(topics.map(async topic => {
    try {
      const res = await fetch(`data/${encodeURIComponent(topic)}.json`);
      if (!res.ok) throw new Error(res.status === 404 ? 'not on this site yet' : `download failed (${res.status})`);
      return { topic, feed: await res.json() };
    } catch (error) {
      return { topic, error };
    }
  }));
  const failed = results.filter(r => r.error);
  if (failed.length === results.length) throw failed[0].error;

  // Same merge as server.py: a paper in several topics keeps its most relevant listing.
  const errors = failed.map(r => `${r.topic}: ${r.error.message}`);
  const byId = new Map();
  let date = null;
  for (const { topic, feed } of results.filter(r => r.feed)) {
    if (feed.error) errors.push(`${topic}: ${feed.error}`);
    if (feed.date && (!date || feed.date > date)) date = feed.date;
    for (const paper of feed.papers) {
      const current = byId.get(paper.id);
      if (!current || rank(paper) < rank(current)) byId.set(paper.id, paper);
    }
  }
  const papers = [...byId.values()].sort((a, b) => rank(a) - rank(b));
  return { source: 'announcement', date, topics, papers, errors };
}

async function loadFeed({ force = false, quiet = false } = {}) {
  if (!prefs.topics.length) {
    feed = null;
    stack = [];
    index = 0;
    resetDeck();
    renderStatus();
    renderNotice();
    setMessage(`
      <p class="eyebrow">No topics yet</p>
      <h2>Pick the arXiv categories you want in your daily stack.</h2>
      <a class="btn btn-primary" href="#settings">Choose topics</a>`);
    return;
  }
  const key = topicsKey();
  if (!force && feed && feed.key === key) {
    if (stackDirty) buildStack();
    else renderDeck(); // refresh saved flags after visiting the Saved page
    return;
  }
  const token = ++loadToken;
  if (!quiet) showLoading();
  try {
    const data = HOSTED ? await fetchPublishedFeed(prefs.topics) : await fetchServerFeed(prefs.topics);
    if (token !== loadToken) return;
    const sameStack = feed && feed.key === key && feed.data.date === data.date && feed.data.source === data.source;
    feed = { key, data, loadedAt: Date.now() };
    if (quiet && sameStack) return;
    buildStack();
  } catch (err) {
    if (token !== loadToken || quiet) return;
    const offline = HOSTED
      ? "Couldn't download the papers. Check your connection and try again."
      : "Can't reach the Preprint Stack server. Make sure python3 server.py is still running, then try again.";
    const message = err instanceof TypeError ? offline : err.message;
    renderStatus();
    setMessage(`
      <p class="eyebrow">Couldn't load papers</p>
      <p>${esc(message)}</p>
      <button class="btn btn-primary" type="button" data-action="retry">Try again</button>`);
  }
}

function buildStack() {
  stackDirty = false;
  const pattern = keywordPattern();
  let papers = feed.data.papers.filter(p => prefs.include[kindOf(p.type)]);
  if (pattern) {
    papers = papers
      .map((paper, i) => ({ paper, i, hit: keywordHits(paper, pattern).length > 0 }))
      .sort((a, b) => (b.hit - a.hit) || (a.i - b.i))
      .map(entry => entry.paper);
  }
  stack = papers;
  const firstUnseen = stack.findIndex(p => !seen[paperKey(p)]);
  index = !prefs.resume ? 0 : firstUnseen === -1 ? stack.length : firstUnseen;
  resetDeck();
  renderDeck();
  renderStatus();
  renderNotice();
}

// ================================================================ deck

const deckEl = $('#deck');
const cardEls = new Map(); // paperKey -> element, for the cards at index..index+2

function resetDeck() {
  cardEls.clear();
  deckEl.querySelectorAll('.card').forEach(el => el.remove());
}

function setMessage(html) {
  const box = $('#deck-message');
  box.hidden = !html;
  box.innerHTML = html || '';
  box.style.transform = '';
}

function showLoading() {
  stack = [];
  index = 0;
  resetDeck();
  setMessage(`
    <div class="skeleton" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span><span></span><span></span></div>
    <p>Fetching today's papers from arXiv…</p>`);
  $('#status-date').textContent = 'Loading…';
  $('#status-count').textContent = '';
  $('#progress').style.width = '0';
}

function buildCard(paper) {
  const pattern = keywordPattern();
  const hits = keywordHits(paper, pattern);
  const el = document.createElement('article');
  el.className = 'card entering';
  el.dataset.key = paperKey(paper);
  el.paper = paper;
  el.innerHTML = `
    <div class="card-body">
      <div class="card-meta">
        <span class="type type-${kindOf(paper.type)}">${TYPE_LABEL[kindOf(paper.type)]}</span>
        <span class="arxiv-id">arXiv:${esc(paper.id)}</span>
        ${hits.length ? `<span class="hit">Mentions ${esc(hits.join(', '))}</span>` : ''}
      </div>
      <h2 class="card-title"><a href="${absUrl(paper)}" target="_blank" rel="noopener"></a></h2>
      <p class="card-authors"></p>
      <p class="subjects-line"><span class="visually-hidden">Subjects: </span>${subjectsLine(paper)}</p>
      <p class="abstract"></p>
    </div>
    <div class="card-foot">
      <button class="act act-save" type="button" data-action="save" aria-pressed="false">${ICONS.bookmark}<span>Save</span></button>
      <a class="act act-primary" href="${pdfUrl(paper)}" target="_blank" rel="noopener">${ICONS.pdf}PDF</a>
      <button class="act" type="button" data-action="share">${ICONS.share}Share</button>
    </div>`;
  fillText($('.card-title a', el), paper.title, pattern);
  fillAuthors($('.card-authors', el), paper.authors);
  fillText($('.abstract', el), paper.abstract, pattern);
  el.addEventListener('animationend', () => el.classList.remove('entering'), { once: true });
  return el;
}

function renderDeck() {
  const visible = stack.slice(index, index + 3);
  const keep = new Set(visible.map(paperKey));
  for (const [key, el] of cardEls) {
    if (!keep.has(key)) { el.remove(); cardEls.delete(key); }
  }
  visible.forEach((paper, depth) => {
    const key = paperKey(paper);
    let el = cardEls.get(key);
    if (!el) {
      el = buildCard(paper);
      cardEls.set(key, el);
      deckEl.append(el);
    }
    el.dataset.depth = depth;
    el.style.zIndex = String(10 - depth);
    el.inert = depth > 0;
    const on = isSaved(paper);
    const save = $('.act-save', el);
    save.classList.toggle('is-saved', on);
    save.setAttribute('aria-pressed', String(on));
    $('span', save).textContent = on ? 'Saved' : 'Save';
  });
  setMessage(!feed || index < stack.length ? '' : endMessage());
}

function endMessage() {
  const day = feed.data.source === 'latest' ? '' : ` from ${longDay(feed.data.date)}`;
  if (!stack.length) {
    return `
      <p class="eyebrow">Nothing to show</p>
      <h2>No papers in your topics${esc(day)}.</h2>
      <p>Try adding topics, or include cross-lists and replacements.</p>
      <a class="btn btn-primary" href="#settings">Open settings</a>`;
  }
  const savedHere = feed.data.papers.filter(isSaved).length;
  return `
    <p class="eyebrow">End of the stack</p>
    <h2>That's all ${stack.length} papers${esc(day)}.</h2>
    <p>You saved ${savedHere === 1 ? '1 paper' : `${savedHere} papers`}. Swipe right to go back. arXiv announces new papers Sunday through Thursday at 8 pm US Eastern.</p>
    <div class="button-row">
      <a class="btn btn-primary" href="#saved">Review saved papers</a>
      <button class="btn" type="button" data-action="restart">Back to the first paper</button>
    </div>`;
}

function renderStatus() {
  const data = feed?.data;
  $('#status-date').textContent = !prefs.topics.length ? 'Preprint Stack'
    : !data ? 'Loading…'
    : data.source === 'latest' ? 'Latest submissions'
    : barDay(data.date);
  $('#status-topics').textContent = prefs.topics.join(', ');
  const position = Math.min(index + 1, stack.length);
  $('#status-count').textContent = data && stack.length ? `${position} of ${stack.length}` : '';
  $('#progress').style.width = data && stack.length ? `${(Math.min(index + 1, stack.length) / stack.length) * 100}%` : '0';
  $('#saved-count').textContent = saved.length || '';
}

function renderNotice() {
  const data = feed?.data;
  const parts = [];
  if (data?.source === 'latest') parts.push("arXiv didn't post an announcement today, so this stack shows the most recent submissions in your topics.");
  if (data?.errors?.length) parts.push(`Some topics didn't load: ${data.errors.join('; ')}.`);
  const notice = $('#notice');
  notice.hidden = !parts.length;
  notice.textContent = parts.join(' ');
}

// ================================================================ moving through the stack

const OFF_LEFT = 'translateX(calc(-100% - 8vw)) rotate(-14deg)';

/** Send the top card off to the left and bring up the next one. */
function next() {
  if (index >= stack.length) return;
  closeShareMenu();
  const paper = stack[index];
  const key = paperKey(paper);
  const el = cardEls.get(key);
  cardEls.delete(key);
  seen[key] = Date.now();
  saveSeen();
  index++;
  if (el) flyOut(el);
  renderDeck();
  renderStatus();
}

function flyOut(el) {
  const drag = el.drag || { x: 0, y: 0 };
  el.classList.remove('dragging', 'entering');
  el.classList.add('leaving');
  el.inert = true;
  el.style.zIndex = '20';
  const distance = window.innerWidth * 0.6 + el.offsetWidth;
  requestAnimationFrame(() => {
    el.style.transform = `translate(${-distance}px, ${drag.y + 30}px) rotate(-18deg)`;
  });
  setTimeout(() => el.remove(), 450);
}

/** Bring the previous card back from the left. `incoming` is a card already pulled in by a drag. */
function prev(incoming) {
  if (index === 0) return;
  closeShareMenu();
  const paper = stack[index - 1];
  const key = paperKey(paper);
  deckEl.querySelectorAll(`.card.leaving[data-key="${CSS.escape(key)}"]`).forEach(el => el.remove());
  let el = incoming;
  if (!el) {
    el = buildCard(paper);
    el.classList.remove('entering');
    el.style.transition = 'none';
    el.style.transform = OFF_LEFT;
    deckEl.append(el);
  }
  el.classList.remove('dragging');
  cardEls.set(key, el);
  index--;
  renderDeck();
  renderStatus();
  el.getBoundingClientRect(); // commit the starting position before animating in
  el.style.transition = '';
  el.style.transform = '';
}

function restart() {
  index = 0;
  resetDeck();
  renderDeck();
  renderStatus();
}

function toggleSave(paper = stack[index]) {
  if (!paper) return;
  if (isSaved(paper)) saved = saved.filter(s => s.id !== paper.id);
  else saved.unshift({ ...paper, savedAt: Date.now() });
  saveSaved();
  renderDeck();
  renderStatus();
}

/** The element on top of the deck: the current card, or the end-of-stack panel. */
function topElement() {
  if (!feed) return null;
  if (index < stack.length) return cardEls.get(paperKey(stack[index])) || null;
  const box = $('#deck-message');
  return box.hidden ? null : box;
}

// Swipe left for the next paper, swipe right to pull the previous one back.
(() => {
  let start = null;
  let dragging = false;
  let incoming = null;
  let dx = 0;

  const resist = d => d * 0.18;

  function dropIncoming() {
    if (!incoming) return;
    const el = incoming;
    incoming = null;
    el.classList.remove('dragging');
    el.style.transform = OFF_LEFT;
    setTimeout(() => el.remove(), 400);
  }

  function update(dy) {
    const top = start.top;
    const width = top.offsetWidth || 1;
    const isCard = top.classList.contains('card');
    if (dx <= 0) {
      dropIncoming();
      const x = isCard ? dx : resist(dx); // nothing comes after the end panel
      top.style.transform = `translate(${x}px, ${isCard ? dy * 0.25 : 0}px) rotate(${isCard ? (x / width) * 12 : 0}deg)`;
      top.drag = { x, y: dy * 0.25 };
      return;
    }
    if (index === 0) {
      top.style.transform = `translateX(${resist(dx)}px)`; // already at the first paper
      return;
    }
    top.style.transform = '';
    if (!incoming) {
      incoming = buildCard(stack[index - 1]);
      incoming.classList.remove('entering');
      incoming.classList.add('dragging');
      incoming.dataset.depth = '0';
      incoming.style.zIndex = '20';
      incoming.inert = true;
      deckEl.append(incoming);
    }
    const progress = Math.min(1, dx / width);
    incoming.style.transform = `translateX(calc(-100% - 8vw + ${dx}px)) rotate(${-14 * (1 - progress)}deg)`;
  }

  deckEl.addEventListener('pointerdown', e => {
    if (e.button > 0 || e.target.closest('a, button')) return;
    const top = topElement();
    if (!top || !top.contains(e.target)) return;
    start = { x: e.clientX, y: e.clientY, id: e.pointerId, t: performance.now(), top };
    dragging = false;
    dx = 0;
  });

  deckEl.addEventListener('pointermove', e => {
    if (!start || e.pointerId !== start.id) return;
    dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    if (!dragging) {
      if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { start = null; return; } // scrolling, not swiping
      if (Math.abs(dx) < 10) return;
      dragging = true;
      try { deckEl.setPointerCapture(e.pointerId); } catch { /* pointer already released */ }
      start.top.classList.add('dragging');
      start.top.classList.remove('entering');
      window.getSelection()?.removeAllRanges();
    }
    update(dy);
  });

  const finish = e => {
    if (!start || e.pointerId !== start.id) return;
    const { top, t } = start;
    const wasDragging = dragging;
    start = null;
    dragging = false;
    if (!wasDragging) return;
    top.classList.remove('dragging');
    const width = top.offsetWidth || 1;
    const committed = e.type !== 'pointercancel'
      && (Math.abs(dx) > width * 0.28 || (Math.abs(dx) > 50 && Math.abs(dx) / (performance.now() - t) > 0.55));
    if (committed && dx < 0 && top.classList.contains('card')) {
      next();
    } else if (committed && dx > 0 && incoming) {
      const el = incoming;
      incoming = null;
      top.style.transform = '';
      prev(el);
    } else {
      top.drag = null;
      top.style.transform = '';
      dropIncoming();
    }
  };
  deckEl.addEventListener('pointerup', finish);
  deckEl.addEventListener('pointercancel', finish);
})();

// Double-tap a card to save it, like liking a photo.
(() => {
  let lastTap = 0;
  let lastX = 0;
  let lastY = 0;
  deckEl.addEventListener('pointerup', e => {
    const card = e.target.closest('.card[data-depth="0"]');
    if (!card || e.button > 0 || e.target.closest('a, button')) { lastTap = 0; return; }
    const now = performance.now();
    if (now - lastTap < 320 && Math.hypot(e.clientX - lastX, e.clientY - lastY) < 30) {
      lastTap = 0;
      window.getSelection()?.removeAllRanges();
      if (!isSaved(card.paper)) toggleSave(card.paper);
      const pop = document.createElement('div');
      pop.className = 'pop';
      pop.innerHTML = ICONS.bookmark;
      card.append(pop);
      setTimeout(() => pop.remove(), 900);
    } else {
      lastTap = now;
      lastX = e.clientX;
      lastY = e.clientY;
    }
  });
})();

deckEl.addEventListener('click', e => {
  const button = e.target.closest('[data-action]');
  if (!button) return;
  const action = button.dataset.action;
  if (action === 'share') share(button.closest('.card').paper, button);
  else if (action === 'save') toggleSave(button.closest('.card').paper);
  else if (action === 'retry') loadFeed({ force: true });
  else if (action === 'restart') restart();
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeShareMenu(); return; }
  if (currentView !== 'stack' || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.closest('input, textarea, select, [role="menu"]')) return;
  const top = stack[index] && cardEls.get(paperKey(stack[index]));
  switch (e.key) {
    case 'ArrowRight': next(); break;
    case 'ArrowLeft': prev(); break;
    case 's': case 'S': toggleSave(); break;
    case 'p': case 'P': if (top) window.open(pdfUrl(top.paper), '_blank', 'noopener'); break;
    case 'ArrowDown': case 'ArrowUp':
      if (top) $('.card-body', top).scrollBy({ top: e.key === 'ArrowDown' ? 120 : -120, behavior: 'smooth' });
      break;
    default: return;
  }
  e.preventDefault();
});

// ================================================================ sharing

const shareMenu = $('#share-menu');
let sharePaper = null;

function shareData(paper) {
  return { title: paper.title, text: `${paper.title} (${authorsShort(paper)})`, url: absUrl(paper) };
}

function share(paper, anchor) {
  if (!paper) return;
  // On phones the system share sheet is the natural choice; elsewhere show our own menu.
  if (navigator.share && matchMedia('(pointer: coarse)').matches) {
    navigator.share(shareData(paper)).catch(err => {
      if (err.name !== 'AbortError') openShareMenu(paper, anchor);
    });
    return;
  }
  openShareMenu(paper, anchor);
}

function openShareMenu(paper, anchor) {
  sharePaper = paper;
  const url = absUrl(paper);
  const title = paper.title;
  const set = (name, href) => { shareMenu.querySelector(`[data-share="${name}"]`).href = href; };
  set('email', `mailto:?subject=${encodeURIComponent(title)}&body=${encodeURIComponent(`${title}\n${authorsShort(paper)}\n\n${url}`)}`);
  set('x', `https://x.com/intent/post?text=${encodeURIComponent(title)}&url=${encodeURIComponent(url)}`);
  set('bluesky', `https://bsky.app/intent/compose?text=${encodeURIComponent(`${title} ${url}`)}`);
  set('linkedin', `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}`);
  shareMenu.querySelector('[data-share="native"]').hidden = !navigator.share;

  shareMenu.hidden = false;
  $('#share-backdrop').hidden = !matchMedia('(max-width: 560px)').matches;
  const r = anchor.getBoundingClientRect();
  const m = shareMenu.getBoundingClientRect();
  let top = r.top - m.height - 8;
  if (top < 8) top = Math.min(r.bottom + 8, window.innerHeight - m.height - 8);
  const left = Math.min(Math.max(8, r.left), window.innerWidth - m.width - 8);
  shareMenu.style.top = `${top}px`;
  shareMenu.style.left = `${left}px`;
  shareMenu.querySelector('button, a').focus({ preventScroll: true });
}

function closeShareMenu() {
  shareMenu.hidden = true;
  $('#share-backdrop').hidden = true;
  sharePaper = null;
}

shareMenu.addEventListener('click', async e => {
  const item = e.target.closest('[data-share]');
  if (!item || !sharePaper) return;
  const paper = sharePaper;
  const kind = item.dataset.share;
  closeShareMenu();
  if (kind === 'copy' || kind === 'cite') {
    e.preventDefault();
    const text = kind === 'copy' ? absUrl(paper) : `${paper.title}\n${authorsShort(paper)}\narXiv:${paper.id} ${absUrl(paper)}`;
    const ok = await copyText(text);
    toast(ok ? (kind === 'copy' ? 'Link copied' : 'Title, authors and link copied') : `Copy this link: ${absUrl(paper)}`);
  } else if (kind === 'native') {
    navigator.share(shareData(paper)).catch(() => {});
  }
});

document.addEventListener('pointerdown', e => {
  if (!shareMenu.hidden && !shareMenu.contains(e.target) && !e.target.closest('[data-action="share"]')) closeShareMenu();
});
window.addEventListener('resize', closeShareMenu);

// ================================================================ saved page

function renderSaved() {
  const list = $('#saved-list');
  list.replaceChildren(...saved.map(savedItem));
  $('#saved-empty').hidden = saved.length > 0;
  $('#saved-tools').hidden = !saved.length;
  $('#copy-saved').hidden = !saved.length;
  renderStatus();
}

function savedItem(paper) {
  const li = document.createElement('li');
  li.className = 'saved-item';
  li.paper = paper;
  li.innerHTML = `
    <p class="saved-meta">
      <span>arXiv:${esc(paper.id)}</span>
      <span class="primary">${esc(paper.categories[0] || '')}</span>
      <span>Saved ${esc(shortDay(paper.savedAt))}</span>
    </p>
    <h2 class="saved-title"><a href="${absUrl(paper)}" target="_blank" rel="noopener"></a></h2>
    <p class="saved-authors"></p>
    <details><summary>Show abstract</summary><p class="abstract"></p></details>
    <div class="saved-actions">
      <a class="act act-primary" href="${pdfUrl(paper)}" target="_blank" rel="noopener">${ICONS.pdf}PDF</a>
      <button class="act" type="button" data-action="share">${ICONS.share}Share</button>
      <button class="act act-quiet" type="button" data-action="remove">Remove</button>
    </div>`;
  fillText($('.saved-title a', li), paper.title, null);
  $('.saved-authors', li).textContent = authorsShort(paper);
  fillText($('.abstract', li), paper.abstract, null);
  return li;
}

$('#saved-list').addEventListener('click', e => {
  const button = e.target.closest('[data-action]');
  if (!button) return;
  const paper = button.closest('.saved-item').paper;
  if (button.dataset.action === 'share') share(paper, button);
  if (button.dataset.action === 'remove') {
    const position = saved.findIndex(s => s.id === paper.id);
    saved.splice(position, 1);
    saveSaved();
    renderSaved();
    toast('Removed from saved', {
      label: 'Undo',
      run: () => { saved.splice(position, 0, paper); saveSaved(); renderSaved(); },
    });
  }
});

$('#copy-saved').addEventListener('click', async () => {
  const text = saved.map(p => `- ${p.title} (${authorsShort(p)}) ${absUrl(p)}`).join('\n');
  toast((await copyText(text)) ? `Copied ${saved.length} papers` : 'Copying isn\'t available in this browser');
});

function confirmButton(button, label, run) {
  if (button.classList.contains('is-confirming')) {
    clearTimeout(button.confirmTimer);
    button.classList.remove('is-confirming');
    button.textContent = button.dataset.label;
    run();
    return;
  }
  button.dataset.label = button.textContent;
  button.textContent = label;
  button.classList.add('is-confirming');
  button.confirmTimer = setTimeout(() => {
    button.classList.remove('is-confirming');
    button.textContent = button.dataset.label;
  }, 4000);
}

$('#clear-saved').addEventListener('click', e => confirmButton(e.currentTarget, 'Tap again to clear all saved papers', () => {
  const previous = saved;
  saved = [];
  saveSaved();
  renderSaved();
  toast('Cleared saved papers', { label: 'Undo', run: () => { saved = previous; saveSaved(); renderSaved(); } });
}));

// ================================================================ settings page

let settingsBuilt = false;

function buildSettings() {
  settingsBuilt = true;
  const groups = $('#topic-groups');
  groups.innerHTML = window.ARXIV_TAXONOMY.map(group => `
    <details class="topic-group">
      <summary><span class="group-name">${esc(group.name)}</span><span class="group-count"></span></summary>
      <div class="topic-list">
        ${group.cats.map(([code, name]) => `
          <label class="topic" for="topic-${esc(code)}" data-search="${esc(`${code} ${name}`.toLowerCase())}">
            <input type="checkbox" id="topic-${esc(code)}" value="${esc(code)}">
            <span class="topic-text"><span class="topic-name">${esc(name)}</span><span class="topic-code">${esc(code)}</span></span>
            <span class="check" aria-hidden="true"></span>
          </label>`).join('')}
      </div>
    </details>`).join('');
  // Open the groups that already hold chosen topics.
  groups.querySelectorAll('.topic-group').forEach(g => {
    g.open = [...g.querySelectorAll('input')].some(i => prefs.topics.includes(i.value));
  });

  groups.addEventListener('change', e => {
    if (e.target.matches('input[type="checkbox"]')) toggleTopic(e.target.value, e.target.checked);
  });
  $('#chosen-topics').addEventListener('click', e => {
    const chip = e.target.closest('[data-code]');
    if (chip) toggleTopic(chip.dataset.code, false);
  });
  $('#topic-search').addEventListener('input', e => filterTopics(e.target.value));

  for (const kind of ['new', 'cross', 'replace']) {
    $(`#inc-${kind}`).addEventListener('change', e => {
      prefs.include[kind] = e.target.checked;
      savePrefs();
      stackDirty = true;
    });
  }
  let keywordTimer;
  $('#keywords').addEventListener('input', e => {
    clearTimeout(keywordTimer);
    keywordTimer = setTimeout(() => {
      prefs.keywords = e.target.value;
      savePrefs();
      stackDirty = true;
    }, 300);
  });
  $('#resume').addEventListener('change', e => {
    prefs.resume = e.target.checked;
    savePrefs();
    stackDirty = true;
  });
  $('#forget-seen').addEventListener('click', e => confirmButton(e.currentTarget, 'Tap again to forget', () => {
    seen = {};
    saveSeen();
    stackDirty = true;
    syncSettings();
    toast('Reading position cleared');
  }));
  document.querySelectorAll('input[name="theme"]').forEach(radio => radio.addEventListener('change', e => {
    prefs.theme = e.target.value;
    savePrefs();
    applyTheme();
  }));
  $('#abstract-size').addEventListener('input', e => {
    prefs.abstractSize = Number(e.target.value);
    savePrefs();
    applyAbstractSize();
  });
}

function toggleTopic(code, on) {
  prefs.topics = prefs.topics.filter(t => t !== code);
  if (on) prefs.topics.push(code);
  savePrefs();
  syncSettings();
}

function filterTopics(query) {
  const q = query.trim().toLowerCase();
  document.querySelectorAll('.topic-group').forEach(group => {
    let matches = 0;
    group.querySelectorAll('.topic').forEach(label => {
      const show = !q || label.dataset.search.includes(q);
      label.hidden = !show;
      if (show) matches++;
    });
    group.hidden = Boolean(q) && !matches;
    if (q) group.open = matches > 0;
  });
}

function syncSettings() {
  $('#welcome').hidden = prefs.topics.length > 0;
  $('#chosen-topics').innerHTML = prefs.topics.map(code => `
    <button class="chosen-chip" type="button" data-code="${esc(code)}" aria-label="Remove ${esc(CATEGORY_NAMES.get(code) || code)}">
      ${esc(code)}<span class="x" aria-hidden="true">×</span>
    </button>`).join('');
  $('#topic-total').textContent = prefs.topics.length ? `· ${prefs.topics.length} selected` : '';
  document.querySelectorAll('#topic-groups input').forEach(input => { input.checked = prefs.topics.includes(input.value); });
  document.querySelectorAll('.topic-group').forEach(group => {
    const n = [...group.querySelectorAll('input')].filter(i => i.checked).length;
    group.querySelector('.group-count').textContent = n ? `${n} selected` : '';
  });
  for (const kind of ['new', 'cross', 'replace']) $(`#inc-${kind}`).checked = prefs.include[kind];
  if (document.activeElement !== $('#keywords')) $('#keywords').value = prefs.keywords;
  $('#resume').checked = prefs.resume;
  const seenCount = Object.keys(seen).length;
  $('#seen-total').textContent = seenCount ? `You've moved past ${seenCount} ${seenCount === 1 ? 'paper' : 'papers'}.` : '';
  $('#forget-seen').disabled = !seenCount;
  const themeInput = $(`#theme-${prefs.theme}`) || $('#theme-system');
  themeInput.checked = true;
  $('#abstract-size').value = String(prefs.abstractSize);

  $('.page-cta').hidden = !onboarding;
  const cta = $('#settings-cta');
  cta.setAttribute('aria-disabled', String(!prefs.topics.length));
  cta.textContent = prefs.topics.length ? "Show today's papers" : 'Pick at least one topic';
}

function applyAbstractSize() {
  const scale = ABSTRACT_SCALES[prefs.abstractSize] ?? 1;
  document.documentElement.style.setProperty('--abstract-scale', String(scale));
}

function applyTheme() {
  if (prefs.theme === 'light' || prefs.theme === 'dark') document.documentElement.dataset.theme = prefs.theme;
  else delete document.documentElement.dataset.theme;
}

// ================================================================ routing

const VIEWS = ['stack', 'saved', 'settings'];
const TITLES = { saved: 'Saved', settings: 'Settings' };
let currentView = null;
let onboarding = !prefs.topics.length; // first visit: Settings shows a big "Show today's papers" button

function route() {
  const requested = location.hash.slice(1);
  const view = VIEWS.includes(requested) ? requested : prefs.topics.length ? 'stack' : 'settings';
  currentView = view;
  if (view === 'stack') onboarding = false;
  $('.app').dataset.view = view;
  $('#bar-title').textContent = TITLES[view] || '';
  for (const v of VIEWS) $(`#view-${v}`).hidden = v !== view;
  document.querySelectorAll('[data-nav]').forEach(link => {
    if (link.dataset.nav === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  closeShareMenu();
  if (view === 'stack') { renderStatus(); loadFeed(); }
  else if (view === 'saved') renderSaved();
  else {
    if (!settingsBuilt) buildSettings();
    syncSettings();
  }
}

window.addEventListener('hashchange', route);

// Pick up a new day's papers if the tab has been sitting open for a while.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && feed && Date.now() - feed.loadedAt > 30 * 60e3) {
    loadFeed({ force: true, quiet: true });
  }
});

applyTheme();
applyAbstractSize();
route();
loadMath();
