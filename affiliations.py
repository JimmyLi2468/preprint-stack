"""Find where each author of a new arXiv paper works, and remember it in a small author index.

Affiliations come from the paper itself: first the title page in the LaTeX source, where we look up
the names arXiv already gives us and follow their superscript markers to the institutions, then
arXiv's HTML version for anyone still missing. (On a day of hep-th and cond-mat.str-el papers the
source answered more authors and made fewer mistakes than the HTML; the HTML's converter sometimes
guesses who belongs where.) Only confident matches are kept; anything ambiguous is left out.

The author index remembers the most recent institutions seen for each person, so a paper that
doesn't list affiliations can still show "last listed" ones. It is only ever added to, newest first.
"""
import gzip
import html
import io
import json
import re
import tarfile
import time
import unicodedata
import urllib.parse
from datetime import date
from pathlib import Path

import server

# Bump when the reading logic changes, so the next build re-reads papers instead of reusing old results.
METHOD = 2
HTML_URL = "https://arxiv.org/html/{}"
SOURCE_URL = "https://export.arxiv.org/e-print/{}"
MAX_SOURCE_BYTES = 40_000_000

INSTITUTION = re.compile(
    r"Univ|Institut|Inst\.|Department|Dept|Laborator|Cent(?:er|re)|School|Facult|Academ|College|Ecole|École|"
    r"CNRS|INFN|CERN|DESY|KEK|RIKEN|IHES|IHÉS|SISSA|ICTP|Perimeter|Kavli|Max[- ]Planck|Physics|Physik|Fisica|"
    r"Física|Physique|Research|Scuola|Escuela|Instituto|Istituto|Observatory|National|Sezione|Division|Group|"
    r"Hospital|Foundation|Company|Inc\.|Ltd|Google|IBM|Microsoft", re.I)
STREET = re.compile(
    r"\b(via|viale|rua|calle|avenida|av\.|street|st\.|road|rd\.|avenue|ave\.|blvd|boulevard|drive|dr\.|"
    r"strasse|straße|str\.|laan|weg|platz|piazza|piazzale|p\.?\s?o\.?\s?box|hall|building|bldg|room|floor)\b", re.I)
OPEN, CLOSE = "\u27e6", "\u27e7"  # ⟦marker⟧ stand-ins for superscripts like ${}^{1,2}$


# ---------------------------------------------------------------- names

def fold(text):
    """Lowercase and strip accents one character at a time, so string positions still line up."""
    out = []
    for ch in text:
        base = ch if ch in (OPEN, CLOSE) else unicodedata.normalize("NFKD", ch)[:1]
        out.append((base or ch).lower())
    return "".join(out)


def name_key(name):
    """'José Luis V. Cerdeira' -> 'jose luis v cerdeira'"""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z]+", ascii_name))


def loose_key(name):
    parts = name_key(name).split()
    return (parts[-1], parts[0][0]) if parts else ("", "")


def match_names(authors, found):
    """Map arXiv's author names onto names found in the paper (exact, then surname + initial)."""
    exact = {name_key(n): affs for n, affs in found}
    loose = {}
    for n, affs in found:
        loose.setdefault(loose_key(n), []).append(affs)
    result = {}
    for a in authors:
        affs = exact.get(name_key(a))
        if affs is None:
            candidates = loose.get(loose_key(a), [])
            affs = candidates[0] if len(candidates) == 1 else None
        if affs:
            result[a] = affs
    return result


# ---------------------------------------------------------------- affiliation text

def clean_affiliation(text, title=""):
    text = html.unescape(text)
    text = re.sub(r"^\s*Affiliation:\s*", "", text)
    text = re.sub(r"\S+@\S+|\[at\]|e-?mail:?|https?://\S+", " ", text, flags=re.I)
    text = re.sub(r"^\s*(\[\w+\]|\(\w+\)|\w\)|\d{1,2}\s)", "", text)
    text = re.sub(r"[-+]?\d*\.?\d+\s?(pt|em|ex|cm|mm|bp|mu)\b", " ", text)  # TeX spacing leftovers
    text = re.sub(r"\s+", " ", text).strip(" ,;.:")
    text = re.sub(r"(,\s*)+", ", ", text).strip(" ,")
    if not (8 <= len(text) <= 400) or not INSTITUTION.search(text):
        return ""
    if re.search(r"\babstract\b", text, re.I):
        return ""
    if re.search(r"(?:^|[\s,;])(?:[a-z]|\d{1,2})\s?[:)]\s", text):
        return ""  # an enumerated list like "a) ..., b) ...": several institutions we couldn't pair up
    folded_title = name_key(title)
    if len(folded_title) > 20 and folded_title[:25] in name_key(text):
        return ""  # picked up the paper's title, not an institution
    return text


def short_affiliation(text):
    """Drop street addresses and postcodes: keep the institution, city and country."""
    parts = [p.strip(" .;") for p in re.split(r"[,;]", text) if p.strip(" .;")]
    keep = [p for p in parts if INSTITUTION.search(p) or not (re.search(r"\d", p) or STREET.search(p))]
    if len(keep) > 6:
        keep = keep[:5] + keep[-1:]
    return ", ".join(keep) or text


# ---------------------------------------------------------------- source 1: arXiv's HTML version

AUTHOR_BLOCK = re.compile(r'<span class="ltx_creator ltx_role_author">(.*?)(?=<span class="ltx_creator ltx_role_author">|</div>)', re.S)
AFFILIATION_SPAN = re.compile(r'<span class="ltx_contact ltx_role_affiliation">(.*?)</span>\s*(?=<span class="ltx_contact|</span></span>)', re.S)


def from_html(page, title=""):
    found = []
    for block in AUTHOR_BLOCK.findall(page):
        name = re.search(r'<span class="ltx_personname">(.*?)</span>', block, re.S)
        if not name:
            continue
        clean_name = html.unescape(re.sub(r"<sup.*?</sup>|<[^>]+>", " ", name.group(1), flags=re.S))
        clean_name = re.sub(r"\s+", " ", clean_name).strip(" ,")
        affs = [clean_affiliation(re.sub(r"<[^>]+>", " ", a), title) for a in AFFILIATION_SPAN.findall(block)]
        affs = [a for a in affs if a]
        if clean_name and affs:
            found.append((clean_name, affs))
    if any(re.search(r"\d", n) for n, _ in found):
        return []  # marker digits left in the names: the converter guessed who belongs where
    return found


# ---------------------------------------------------------------- source 2: the LaTeX title page

def tex_text(data):
    """The .tex text of an arXiv source download, read in memory (nothing is unpacked to disk)."""
    if not data or len(data) > MAX_SOURCE_BYTES:
        return ""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            parts = []
            for member in tar.getmembers():
                if member.isfile() and member.name.endswith(".tex") and member.size < 3_000_000:
                    parts.append(tar.extractfile(member).read().decode("utf-8", "replace"))
            return "\n".join(parts)
    except tarfile.TarError:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                return gz.read(5_000_000).decode("utf-8", "replace")
        except OSError:
            return data[:5_000_000].decode("utf-8", "replace")


def braced(text, i):
    """Return the contents of the {...} group that opens at text[i], and the index after it."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
    return None, len(text)


def preamble_macros(preamble):
    """\\author / \\affiliation commands that JHEP-style papers put before \\begin{document}."""
    out = []
    for m in re.finditer(r"\\(author|affiliation|affil|address|institute|inst)\*?\s*(\[[^\]]*\])?\s*{", preamble):
        body, _ = braced(preamble, m.end() - 1)
        if body is not None:
            out.append(f"\\{m.group(1)}{m.group(2) or ''}{{{body}}}")
    return "\n".join(out)


def front_matter(tex):
    tex = re.sub(r"(?<!\\)%.*", "", tex)
    start = tex.find("\\begin{document}")
    start = max(start, 0)
    ends = [i for i in (tex.find(k, start) for k in (
        "\\begin{abstract}", "\\abstract{", "{\\bf Abstract}", "\\textbf{Abstract}", "{\\bf ABSTRACT}",
        "\\section", "\\tableofcontents", "\\chapter")) if i > 0]
    end = min(ends) if ends else start + 6000
    return preamble_macros(tex[:start]) + "\n" + tex[start:min(end, start + 12000)]


def marker(content):
    """Normalise a superscript like '{1,2}', '{\\rm a}', 'a)' to '1,2' / 'a'; None if it's real math."""
    key = re.sub(r"\\(rm|mathrm|textrm|it|bf|mathit|textit|textbf|ast|dagger|ddagger|star)\b|[{}()\s]", "", content)
    return key if re.fullmatch(r"[\w*†‡§¶♯♭,]{1,12}", key) else None


def marker_keys(text):
    """'1*,2' -> ['1', '2']: footnote symbols (corresponding author etc.) aren't institution labels."""
    keys = [re.sub(r"[*†‡§¶♯♭]", "", k) for k in text.split(",")]
    return [k for k in keys if k]


def to_text(tex):
    def sup(m):
        key = marker(m.group(1))
        return OPEN + key + CLOSE if key else " "
    tex = re.sub(r"\\(def|newcommand|renewcommand|providecommand|DeclareMathOperator)\b[^\n]*", " ", tex)
    s = re.sub(r"\\author\s*\[([^\]]+)\]\s*{([^{}]*)}", lambda m: "\n" + m.group(2) + OPEN + m.group(1) + CLOSE + "\n", tex)
    s = re.sub(r"\\(affiliation|affil|address|institute)\s*\[([^\]]+)\]\s*{", lambda m: "\n" + OPEN + m.group(2) + CLOSE + "{", s)
    s = re.sub(r"\\(author|affiliation|affil|address|institute)\s*{", "\n{", s)
    s = re.sub(r"\$\s*(?:\{\s*\})?\s*\^\s*(\{(?:[^{}]|\{[^{}]*\})*\}|[^\s$])\s*\$", sup, s)
    def sup_or_text(m):
        key = marker(m.group(2))
        if key:
            return OPEN + key + CLOSE
        return "\n" + m.group(2) + "\n" if m.group(1) == "inst" else " "  # JPSJ puts institutions in \inst{...}
    s = re.sub(r"\\(textsuperscript|inst)\s*{([^{}]*(?:{[^{}]*}[^{}]*)*)}", sup_or_text, s)
    s = re.sub(r"\$[^$]*\$", " ", s)
    s = re.sub(r"\\(footnote|thanks|email|emailAdd|ead|orcid|vspace|hspace|vskip|hskip|date|subheader|preprint|"
               r"includegraphics|title|maketitle|keywords|pacs|thispagestyle|pagestyle|pagenumbering|setcounter|"
               r"addtocounter|setlength|label|cite|ref|eqref|color|textcolor|bibliographystyle|hypersetup|"
               r"input|include|usepackage)\*?\s*(\[[^\]]*\])?\s*{[^{}]*({[^{}]*}[^{}]*)*}", " ", s)
    s = re.sub(r"\\(begin|end)\s*{[^}]*}", "\n", s)
    s = re.sub(r"\\\\(\[[^\]]*\])?|\\par\b|\\newline\b", "\n", s)
    s = s.replace("~", " ").replace("\\&", "&").replace("\\,", " ")
    s = server.detex_accents(s)
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)
    s = re.sub(r"[{}#]", " ", s)
    return re.sub(r"[ \t]+", " ", s)


def find_name(folded, name, start):
    full = fold(name)
    i = folded.find(full, start)
    if i >= 0:
        return i, i + len(full)
    surname = full.split()[-1] if full.split() else ""
    m = re.search(r"\b" + re.escape(surname) + r"\b", folded[start:]) if surname else None
    return (start + m.start(), start + m.end()) if m else (-1, -1)


def from_source(data, authors, title=""):
    tex = tex_text(data)
    if not tex:
        return {}
    text = to_text(front_matter(tex))
    folded = fold(text)
    located, cursor = [], 0
    for a in authors:
        s, e = find_name(folded, a, cursor)
        if s >= 0:
            located.append((a, s, e))
            cursor = e
    if not located:
        return {}
    tail = max(e for _, _, e in located)

    # 1) Names carry superscript markers; institutions start with the same markers.
    blocks = {}
    for m in re.finditer(OPEN + r"([^" + CLOSE + r"]+)" + CLOSE + r"([^" + OPEN + r"]+)", text[tail:]):
        aff = clean_affiliation(m.group(2), title)
        for key in marker_keys(m.group(1)):
            if aff and key not in blocks:
                blocks[key] = aff
    if blocks:
        result = {}
        for a, s, e in located:
            keys, after = [], e
            for mk in re.finditer(OPEN + r"([^" + CLOSE + r"]+)" + CLOSE, text[e:e + 60]):
                if text[after:e + mk.start()].strip(" ,"):
                    break  # something else came between: this marker isn't the name's
                keys += marker_keys(mk.group(1))
                after = e + mk.end()
            affs = [blocks[k] for k in keys if k in blocks]
            if affs:
                result[a] = list(dict.fromkeys(affs))
        return result

    # 2) No markers: an institution applies to the authors listed just before it (REVTeX's rule,
    #    and how most hand-made title pages with one shared address read).
    order = sorted(located, key=lambda x: x[1])
    result, pending = {}, []
    for i, (a, s, e) in enumerate(order):
        stop = order[i + 1][1] if i + 1 < len(order) else len(text)
        segment = re.sub(r"^(\s*(,|and\b|&))+\s*", "", text[e:stop].strip())
        if OPEN in segment:
            return {}  # markers we couldn't pair up: don't guess
        paragraphs = [re.sub(r"\s*\n\s*", ", ", p) for p in re.split(r"\n\s*\n", segment)]
        affs = [x for x in (clean_affiliation(p, title) for p in paragraphs) if x]
        leftover = re.sub(r"\S+@\S+|https?://\S+|[-+]?\d*\.?\d+\s?(pt|em|ex|cm|mm)\b", " ", segment)
        if not affs and re.search(r"[A-Za-z]{3,}", leftover):
            # Something is listed for these authors but it isn't an institution (a home address,
            # say). Leave them blank rather than let them inherit the next person's institution.
            pending = []
            continue
        pending.append(a)
        if affs:
            if len(pending) > 1 and len(affs) > 1:
                return {}  # several institutions shared by several people: probably markers we missed
            for p in pending:
                result[p] = affs
            pending = []
    return result


# ---------------------------------------------------------------- fetching

class Fetcher:
    """Spaces out requests per host so we stay well within arXiv's guidelines."""

    def __init__(self):
        self.last = {}

    def get(self, url, gap):
        host = urllib.parse.urlsplit(url).netloc
        wait = gap - (time.time() - self.last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        try:
            return server.fetch(url)
        except Exception:
            return b""  # no HTML version, or a hiccup: fall back or skip
        finally:
            self.last[host] = time.time()


GENERIC_WORDS = {
    "school", "department", "dept", "faculty", "institute", "division", "laboratory", "group", "center", "centre",
    "of", "the", "and", "for", "in", "physics", "physical", "sciences", "science", "mathematics", "mathematical",
    "theoretical", "applied", "research", "engineering", "graduate", "astronomy", "natural"}


def is_vague(institutions):
    """True when every entry is generic words only, e.g. 'School of Physical Sciences'."""
    return all(set(name_key(x).split()) <= GENERIC_WORDS for x in institutions)


def paper_affiliations(paper, fetcher):
    """{author: [institution, ...]} for the authors we could match confidently."""
    ident = paper["id"] + paper["version"]
    authors = paper["authors"]
    source = fetcher.get(SOURCE_URL.format(ident), gap=3.0)
    result = {a: v for a, v in from_source(source, authors, paper["title"]).items() if v}
    missing = [a for a in authors if a not in result]
    vague = [a for a in result if is_vague(result[a])]
    if missing or vague:
        page = fetcher.get(HTML_URL.format(ident), gap=1.0)
        from_page = match_names(authors, from_html(page.decode("utf-8", "replace"), paper["title"])) if page else {}
        for a in missing + vague:
            if from_page.get(a) and not is_vague(from_page[a]):
                result[a] = from_page[a]
            elif a in missing and from_page.get(a):
                result[a] = from_page[a]
    return {a: list(dict.fromkeys(short_affiliation(x) for x in affs)) for a, affs in result.items()}


# ---------------------------------------------------------------- the author index

def archives(categories):
    return {c.split(".")[0] for c in categories}


class AuthorIndex:
    """Newest-first institutions per person, from papers that listed them. Only ever added to."""

    KEEP = 3              # entries per person
    MAX_AGE_DAYS = 548    # don't fall back on listings older than ~18 months

    def __init__(self, path, overrides_path=None):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.data.setdefault("authors", {})
        self.overrides = {}
        if overrides_path and Path(overrides_path).exists():
            raw = json.loads(Path(overrides_path).read_text(encoding="utf-8"))
            self.overrides = {name_key(k): v for k, v in raw.items() if not k.startswith("_")}

    def add(self, name, institutions, day, paper_id, categories):
        entries = self.data["authors"].setdefault(name_key(name), [])
        entries[:] = [e for e in entries if e["paper"] != paper_id]  # a re-read replaces the old entry
        entries.append({"name": name, "inst": institutions, "date": day, "paper": paper_id,
                        "fields": sorted(archives(categories))})
        entries.sort(key=lambda e: e["date"], reverse=True)
        del entries[self.KEEP:]

    def forget(self, name, paper_id):
        """Drop what an earlier read of this paper said about this person."""
        key = name_key(name)
        entries = [e for e in self.data["authors"].get(key, []) if e["paper"] != paper_id]
        if entries:
            self.data["authors"][key] = entries
        else:
            self.data["authors"].pop(key, None)

    def fallback(self, name, categories, today, paper_id=None):
        """A manual correction, else the newest listing in the same field from the last ~18 months."""
        key = name_key(name)
        if key in self.overrides:
            return {"inst": self.overrides[key], "note": "manual"}
        for e in self.data["authors"].get(key, []):
            if e["paper"] == paper_id:
                continue  # never offer a paper's own (earlier) reading as a fallback for itself
            recent = (date.fromisoformat(today) - date.fromisoformat(e["date"])).days <= self.MAX_AGE_DAYS
            if recent and archives(categories) & set(e["fields"]):
                return {"inst": e["inst"], "asOf": e["date"], "via": e["paper"]}
        return None

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
