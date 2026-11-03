#!/usr/bin/env python3
"""VibSpec News weekly scraper.

Finds new vibrational-spectroscopy papers (Raman, SRS/CARS, SERS/TERS, IR/NIR,
photothermal IR) published in a date window, and writes one issue file:

    data/issues/<issue-date>.json      the issue (articles, categories, figures)
    data/issues/index.json             list of all issues, newest first
    figs/<article-id>.jpg              thumbnail: the article page's preview image
                                       (og:image / twitter:image, usually Fig. 1 or
                                       the graphical abstract), else the journal cover

Sources: Europe PMC, Crossref, arXiv (free APIs, no key needed).

Usage
    python scraper/vibspec_scraper.py                     # issue for this Monday, previous 7 days
    python scraper/vibspec_scraper.py --issue-date 2026-11-09
    python scraper/vibspec_scraper.py --since 2026-11-02 --until 2026-11-08
    python scraper/vibspec_scraper.py --no-figures --dry-run

Re-running for an existing issue keeps everything the editor changed
(summaries, picks, Feature of the Week, deletions are NOT re-added if listed
in the issue's "removed" list) and only adds papers that are new.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import logging
import re
import sys
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from PIL import Image

log = logging.getLogger("vibspec")
ROOT = Path(__file__).resolve().parent.parent
SGT = dt.timezone(dt.timedelta(hours=8))

GROUP_ORDER = ["Applications", "Technology", "Business News", "Miscellaneous"]
SKIP_TITLE = re.compile(r"^(correction|erratum|corrigendum|retraction|retracted|expression of concern|withdrawn|editorial board|issue information|cover image|front cover|back cover|inside cover)\b", re.I)
ORG_WORDS = re.compile(r"(universit|institut|hospital|college|laborator|centre|center|academy|school of|polytechn|max planck|riken|cnrs|inserm|cnr\b|csic|fraunhofer|leibniz|helmholtz|national lab|clinic|faculty|ecole|école|eth\b|epfl|kaist|a\*star|nih\b|corporation|inc\.|ltd|gmbh)", re.I)
DEPT_WORDS = re.compile(r"^(dep(t|artment)|division|section|unit|group|key laborator|state key|program|graduate school|faculty of|school of medicine)", re.I)


# --------------------------------------------------------------------------- config / http

def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_include"] = [re.compile(p, re.I) for p in cfg.get("include", [])]
    cfg["_exclude"] = [re.compile(p, re.I) for p in cfg.get("exclude", [])]
    cats = []
    for group, entries in (cfg.get("categories") or {}).items():
        for name, pats in entries.items():
            cats.append((group, name, [re.compile(p, re.I) for p in pats]))
    cfg["_cats"] = cats
    return cfg


class Http:
    """requests wrapper: one User-Agent, per-host delay, optional robots.txt check."""

    def __init__(self, cfg: dict):
        h = cfg.get("http", {})
        self.ua = h.get("user_agent", "VibSpecNewsBot/1.0").format(site_url=cfg.get("site", {}).get("url", ""))
        self.timeout = h.get("timeout_s", 20)
        self.delay = h.get("delay_s", 1.0)
        self.respect_robots = h.get("respect_robots_txt", True)
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": self.ua, "Accept-Language": "en"})
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _wait(self, host: str):
        last = self._last.get(host)
        if last is not None:
            gap = time.time() - last
            if gap < self.delay:
                time.sleep(self.delay - gap)
        self._last[host] = time.time()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(base + "/robots.txt", timeout=self.timeout)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                self._robots[base] = rp
            except requests.RequestException:
                self._robots[base] = None
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def get(self, url: str, check_robots: bool = False, **kw) -> requests.Response | None:
        if check_robots and not self.allowed(url):
            log.info("robots.txt disallows %s", url)
            return None
        self._wait(urlparse(url).netloc)
        try:
            r = self.s.get(url, timeout=self.timeout, allow_redirects=True, **kw)
        except requests.RequestException as e:
            log.warning("GET %s failed: %s", url, e)
            return None
        if r.status_code >= 400:
            log.info("GET %s -> HTTP %s", url, r.status_code)
            return None
        return r


# --------------------------------------------------------------------------- text helpers

def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)          # JATS / HTML tags
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(abstract|summary)[\s:.\-–]+", "", s, flags=re.I)
    return s


def trim_summary(s: str, max_chars: int) -> str:
    s = clean_text(s)
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if end > max_chars * 0.5:
        return cut[: end + 1] + " …"
    return cut.rsplit(" ", 1)[0] + " …"


def slug_for(rec: dict) -> str:
    key = rec.get("doi") or rec.get("url") or rec["title"]
    base = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:60]
    return base + "-" + hashlib.sha1(key.lower().encode()).hexdigest()[:6]


def title_key(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())[:90]


def norm_doi(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d, flags=re.I)
    return d.lower() or None


# --------------------------------------------------------------------------- relevance / classification

def is_relevant(rec: dict, cfg: dict) -> bool:
    title, abstract = rec.get("title", ""), rec.get("abstract", "")
    if SKIP_TITLE.search(title):
        return False
    text = f"{title} {abstract}"
    if any(p.search(text) for p in cfg["_exclude"]):
        return False
    if any(p.search(title) for p in cfg["_include"]):
        return True
    hits = sum(len(p.findall(abstract)) for p in cfg["_include"])
    return hits >= 2


def categorize(rec: dict, cfg: dict, limit: int = 5) -> list[str]:
    text = f"{rec.get('title', '')} {rec.get('abstract', '')}"
    found: dict[str, list[str]] = {g: [] for g in GROUP_ORDER}
    for group, name, pats in cfg["_cats"]:
        if any(p.search(text) for p in pats):
            found.setdefault(group, []).append(name)
    if rec.get("is_review") and "Reviews" not in found["Miscellaneous"]:
        found["Miscellaneous"].append("Reviews")
    # keep the most specific technology tags: drop "Spontaneous Raman" when only SERS/SRS matched by the word "raman"
    tech = found["Technology"]
    if "Spontaneous Raman" in tech and ("SERS / TERS" in tech or "SRS / CARS" in tech) and not re.search(r"spontaneous|confocal raman|raman microspectroscop", text, re.I):
        tech.remove("Spontaneous Raman")
    ordered = found["Applications"][:2] + tech[:2] + found["Business News"] + found["Miscellaneous"]
    if not ordered:
        ordered = ["General Technology"]
    return ordered[:limit]


def glyph_for(cats: list[str]) -> str:
    if "SRS / CARS" in cats:
        return "srs"
    if "Photothermal IR" in cats:
        return "mip"
    if "SERS / TERS" in cats:
        return "sers"
    if "IR / NIR" in cats:
        return "ir"
    return "raman"


def institutions_from(affiliations: list[str], limit: int = 3) -> list[str]:
    """Pick organisation names out of free-text affiliation strings."""
    out: list[str] = []
    for aff in affiliations:
        aff = re.sub(r"\S+@\S+", "", aff)                      # e-mails
        aff = re.sub(r"^\s*[\d*†‡§#]+\s*", "", aff)            # footnote markers
        parts = [p.strip(" .;") for p in re.split(r",|;", aff) if p.strip(" .;")]
        org = None
        for p in parts:
            if ORG_WORDS.search(p) and not DEPT_WORDS.search(p):
                org = p
                break
        if not org:
            for p in parts:                                     # fall back to a department-level match
                if ORG_WORDS.search(p):
                    org = p
                    break
        if org and len(org) <= 90:
            org = re.sub(r"\s+", " ", org)
            if org.lower() not in (o.lower() for o in out):
                out.append(org)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- sources

def europepmc_search(http: Http, cfg: dict, since: str, until: str) -> list[dict]:
    terms = " OR ".join(f"TITLE_ABS:{t}" if not t.startswith('"') else f"TITLE_ABS:{t}" for t in cfg.get("europepmc_terms", ["raman"]))
    query = f"({terms}) AND FIRST_PDATE:[{since} TO {until}]"
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    cursor, out, cap = "*", [], cfg["sources"].get("max_results_per_query", 400) * 3
    while len(out) < cap:
        r = http.get(url, params={"query": query, "format": "json", "resultType": "core", "pageSize": 200, "cursorMark": cursor})
        if not r:
            break
        data = r.json()
        for res in data.get("resultList", {}).get("result", []):
            out.append(parse_europepmc(res))
        nxt = data.get("nextCursorMark")
        if not nxt or nxt == cursor or not data.get("resultList", {}).get("result"):
            break
        cursor = nxt
    log.info("Europe PMC: %d records", len(out))
    return out


def parse_europepmc(res: dict) -> dict:
    authors, affs = [], []
    for a in (res.get("authorList") or {}).get("author", []):
        name = a.get("fullName") or " ".join(x for x in [a.get("firstName"), a.get("lastName")] if x)
        if name:
            authors.append(name)
        for d in (a.get("authorAffiliationDetailsList") or {}).get("authorAffiliation", []):
            if d.get("affiliation"):
                affs.append(d["affiliation"])
        if a.get("affiliation"):
            affs.append(a["affiliation"])
    ji = (res.get("journalInfo") or {}).get("journal") or {}
    journal = ji.get("title") or ji.get("medlineAbbreviation") or ""
    if res.get("source") == "PPR":
        journal = (res.get("bookOrReportDetails") or {}).get("publisher") or "Preprint"
    pubtypes = [p.lower() for p in (res.get("pubTypeList") or {}).get("pubType", [])]
    lic = (res.get("license") or "").lower()
    doi = norm_doi(res.get("doi"))
    return {
        "source": "europepmc",
        "title": clean_text(res.get("title")).rstrip("."),
        "authors": authors or [s.strip() for s in (res.get("authorString") or "").rstrip(".").split(",") if s.strip()],
        "affiliations": affs,
        "journal": journal,
        "issn": ji.get("issn") or ji.get("essn"),
        "doi": doi,
        "pmid": res.get("pmid"),
        "pmcid": res.get("pmcid"),
        "url": f"https://doi.org/{doi}" if doi else (f"https://europepmc.org/article/{res.get('source')}/{res.get('id')}"),
        "date": res.get("firstPublicationDate") or res.get("firstIndexDate") or "",
        "abstract": clean_text(res.get("abstractText")),
        "oa": res.get("isOpenAccess") == "Y" or lic.startswith("cc"),
        "license": lic.upper().replace("CC ", "CC ") if lic else "",
        "is_review": any("review" in p for p in pubtypes),
        "is_preprint": res.get("source") == "PPR",
    }


def crossref_search(http: Http, cfg: dict, since: str, until: str) -> list[dict]:
    out = []
    rows = min(cfg["sources"].get("max_results_per_query", 400), 1000)
    for q in cfg.get("crossref_queries", []):
        r = http.get("https://api.crossref.org/works", params={
            "query.bibliographic": q,
            "filter": f"from-created-date:{since},until-created-date:{until},type:journal-article",
            "rows": rows,
            "select": "DOI,title,author,container-title,ISSN,created,issued,published-online,published-print,abstract,license,type,subject",
        })
        if not r:
            continue
        for it in r.json().get("message", {}).get("items", []):
            out.append(parse_crossref(it))
    log.info("Crossref: %d records", len(out))
    return out


def _cr_date(it: dict) -> str:
    for k in ("published-online", "issued", "published-print", "created"):
        parts = (it.get(k) or {}).get("date-parts") or [[]]
        p = parts[0]
        if p and p[0]:
            y, m, d = (p + [1, 1])[:3]
            return f"{y:04d}-{m:02d}-{d:02d}"
    return ""


def parse_crossref(it: dict) -> dict:
    authors, affs = [], []
    for a in it.get("author", []) or []:
        name = " ".join(x for x in [a.get("given"), a.get("family")] if x) or a.get("name")
        if name:
            authors.append(name)
        for af in a.get("affiliation", []) or []:
            if af.get("name"):
                affs.append(af["name"])
    lic_urls = [l.get("URL", "") for l in it.get("license", []) or []]
    cc = next((u for u in lic_urls if "creativecommons.org" in u), "")
    lic = ""
    if cc:
        m = re.search(r"licenses/([a-z\-]+)/([\d.]+)", cc)
        lic = f"CC {m.group(1).upper()} {m.group(2)}" if m else "CC"
    doi = norm_doi(it.get("DOI"))
    return {
        "source": "crossref",
        "title": clean_text((it.get("title") or [""])[0]).rstrip("."),
        "authors": authors,
        "affiliations": affs,
        "journal": (it.get("container-title") or [""])[0],
        "issn": (it.get("ISSN") or [None])[0],
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else "",
        "date": _cr_date(it),
        "abstract": clean_text(it.get("abstract")),
        "oa": bool(cc),
        "license": lic,
        "is_review": False,
        "is_preprint": False,
    }


def arxiv_search(http: Http, cfg: dict, since: str, until: str) -> list[dict]:
    cats = " OR ".join(f"cat:{c}" for c in cfg.get("arxiv_categories", []))
    terms = 'abs:raman OR abs:"stimulated raman" OR abs:"infrared spectroscopy" OR abs:"vibrational spectroscopy" OR abs:"mid-infrared photothermal" OR abs:SERS'
    q = f"({cats}) AND ({terms})"
    r = http.get("http://export.arxiv.org/api/query", params={"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": cfg["sources"].get("max_results_per_query", 400)})
    if not r:
        return []
    return parse_arxiv(r.text, since, until)


def parse_arxiv(xml_text: str, since: str, until: str) -> list[dict]:
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    root = ET.fromstring(xml_text)
    for e in root.findall("a:entry", ns):
        date = (e.findtext("a:published", default="", namespaces=ns) or "")[:10]
        if not (since <= date <= until):
            continue
        abs_url = e.findtext("a:id", default="", namespaces=ns).strip()
        arxiv_id = re.sub(r"^.*/abs/", "", abs_url)
        arxiv_id_nov = re.sub(r"v\d+$", "", arxiv_id)
        authors, affs = [], []
        for a in e.findall("a:author", ns):
            authors.append(a.findtext("a:name", default="", namespaces=ns).strip())
            aff = a.findtext("arxiv:affiliation", default="", namespaces=ns)
            if aff:
                affs.append(aff)
        out.append({
            "source": "arxiv",
            "title": clean_text(e.findtext("a:title", default="", namespaces=ns)),
            "authors": authors,
            "affiliations": affs,
            "journal": "arXiv (preprint)",
            "issn": None,
            "doi": f"10.48550/arxiv.{arxiv_id_nov}".lower(),
            "url": f"https://arxiv.org/abs/{arxiv_id_nov}",
            "date": date,
            "abstract": clean_text(e.findtext("a:summary", default="", namespaces=ns)),
            "oa": True,
            "license": "",
            "is_review": False,
            "is_preprint": True,
        })
    log.info("arXiv: %d records in window", len(out))
    return out


def lookup_doi(http: Http, doi: str) -> dict | None:
    """Fetch one paper by DOI (for data/inbox.txt submissions): Europe PMC first, Crossref second."""
    r = http.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"})
    if r:
        res = r.json().get("resultList", {}).get("result", [])
        if res:
            return parse_europepmc(res[0])
    r = http.get(f"https://api.crossref.org/works/{doi}")
    if r:
        return parse_crossref(r.json().get("message", {}))
    return None


# --------------------------------------------------------------------------- merge

def merge_records(records: list[dict]) -> list[dict]:
    """Deduplicate by DOI, then by normalised title. Europe PMC wins; Crossref fills gaps."""
    rank = {"europepmc": 0, "crossref": 1, "arxiv": 2, "inbox": -1}
    records = sorted(records, key=lambda r: rank.get(r["source"], 9))
    by_doi: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    out: list[dict] = []
    for r in records:
        if not r.get("title"):
            continue
        k_doi, k_t = r.get("doi"), title_key(r["title"])
        hit = (by_doi.get(k_doi) if k_doi else None) or by_title.get(k_t)
        if hit:
            for f in ("abstract", "affiliations", "journal", "issn", "license", "date"):
                if not hit.get(f) and r.get(f):
                    hit[f] = r[f]
            hit["oa"] = hit.get("oa") or r.get("oa")
            continue
        out.append(r)
        if k_doi:
            by_doi[k_doi] = r
        by_title[k_t] = r
    return out


# --------------------------------------------------------------------------- figures

def extract_preview_image(page_html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(page_html, "html.parser")
    candidates = []
    for attrs in ({"property": "og:image"}, {"property": "og:image:url"}, {"name": "twitter:image"}, {"name": "twitter:image:src"}, {"property": "twitter:image"}):
        for m in soup.find_all("meta", attrs=attrs):
            if m.get("content"):
                candidates.append(m["content"].strip())
    link = soup.find("link", rel=lambda v: v and "image_src" in v)
    if link and link.get("href"):
        candidates.append(link["href"].strip())
    for c in candidates:
        if c and not c.startswith("data:"):
            return urljoin(base_url, c)
    return None


def looks_like_cover(url: str) -> bool:
    return bool(re.search(r"cover|journal[-_]?image|jcover|issue[-_]?image", url, re.I))


def save_thumbnail(img_bytes: bytes, dest: Path, cfg: dict) -> bool:
    fc = cfg.get("figures", {})
    try:
        im = Image.open(io.BytesIO(img_bytes))
        im.load()
    except Exception as e:  # noqa: BLE001 — any decode failure means "no usable image"
        log.info("not an image: %s", e)
        return False
    if min(im.size) < fc.get("min_side_px", 120):
        return False
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im_rgba = im.convert("RGBA")
        bg.paste(im_rgba, mask=im_rgba.split()[-1])
        im = bg
    im = im.convert("RGB")
    im.thumbnail((fc.get("max_width", 400), fc.get("max_height", 280)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "JPEG", quality=82, optimize=True, progressive=True)
    return True


class FigureFetcher:
    def __init__(self, http: Http, cfg: dict, root: Path):
        self.http, self.cfg, self.root = http, cfg, root
        self.cover_cache_path = root / "covers" / "index.json"
        try:
            self.cover_cache = json.loads(self.cover_cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.cover_cache = {}
        self.manual_covers = {k.lower(): v for k, v in (cfg.get("journal_covers") or {}).items()}

    def _download(self, url: str, dest: Path) -> bool:
        pats = self.cfg.get("figures", {}).get("reject_url_patterns", [])
        if any(p in url.lower() for p in pats):
            return False
        r = self.http.get(url, check_robots=True)
        if not r or not r.headers.get("Content-Type", "image").startswith("image"):
            return False
        return save_thumbnail(r.content, dest, self.cfg)

    def journal_key(self, rec: dict) -> str:
        return (rec.get("journal") or rec.get("issn") or "").strip().lower()

    def cover_for(self, rec: dict) -> dict | None:
        key = self.journal_key(rec)
        if not key:
            return None
        if key in self.cover_cache and (self.root / self.cover_cache[key]["src"]).exists():
            return dict(self.cover_cache[key])
        url = self.manual_covers.get(key) or (self.manual_covers.get((rec.get("issn") or "").lower()) if rec.get("issn") else None)
        if not url:
            return None
        name = "covers/" + re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:60] + ".jpg"
        if self._download(url, self.root / name):
            entry = {"src": name, "kind": "cover", "credit": f"Journal cover · {rec.get('journal', '')}"}
            self.cover_cache[key] = entry
            return dict(entry)
        return None

    def fetch(self, rec: dict, art_id: str) -> dict | None:
        mode = self.cfg.get("figures", {}).get("mode", "all")
        host_credit = urlparse(rec.get("url") or "").netloc
        if rec.get("is_preprint") and rec.get("source") == "arxiv":
            return None
        if mode == "open_access_only" and not rec.get("oa"):
            return self.cover_for(rec)
        if rec.get("url"):
            page = self.http.get(rec["url"], check_robots=True)
            if page is not None and "html" in page.headers.get("Content-Type", "html"):
                img_url = extract_preview_image(page.text, page.url)
                host_credit = urlparse(page.url).netloc.replace("www.", "")
                if img_url:
                    if looks_like_cover(img_url):
                        key = self.journal_key(rec)
                        name = "covers/" + re.sub(r"[^a-z0-9]+", "-", key or art_id).strip("-")[:60] + ".jpg"
                        if self._download(img_url, self.root / name):
                            entry = {"src": name, "kind": "cover", "credit": f"Journal cover · {rec.get('journal', '')}"}
                            if key:
                                self.cover_cache[key] = entry
                            return dict(entry)
                    else:
                        name = f"figs/{art_id}.jpg"
                        if self._download(img_url, self.root / name):
                            lic = rec.get("license") or ("Open access" if rec.get("oa") else "© the publisher")
                            return {"src": name, "kind": "figure", "credit": f"{lic} · via {host_credit}"}
        return self.cover_for(rec)

    def save_cache(self):
        self.cover_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cover_cache_path.write_text(json.dumps(self.cover_cache, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- issue assembly

def to_article(rec: dict, cfg: dict) -> dict:
    cats = categorize(rec, cfg)
    art_id = slug_for(rec)
    return {
        "id": art_id,
        "date": rec.get("date", "")[:10],
        "title": rec["title"],
        "authors": ", ".join(rec.get("authors", [])[:20]) + (" et al." if len(rec.get("authors", [])) > 20 else ""),
        "institutions": institutions_from(rec.get("affiliations", [])),
        "journal": rec.get("journal", ""),
        "doi": rec.get("doi"),
        "url": rec.get("url") or (f"https://doi.org/{rec['doi']}" if rec.get("doi") else ""),
        "oa": bool(rec.get("oa")),
        "license": rec.get("license", ""),
        "categories": cats,
        "glyph": glyph_for(cats),
        "summary": trim_summary(rec.get("abstract", ""), cfg.get("summary_max_chars", 700)),
        "why": "",
        "pick": False,
        "feature": False,
        "fig": None,
        "source": rec.get("source"),
    }


def default_issue_date(today: dt.date | None = None) -> dt.date:
    today = today or dt.datetime.now(SGT).date()
    return today - dt.timedelta(days=today.weekday())   # this week's Monday


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def update_index(root: Path, issue: dict):
    idx_path = root / "data" / "issues" / "index.json"
    idx = load_json(idx_path, {"issues": []})
    entry = {"id": issue["id"], "number": issue["number"], "date": issue["date"], "window": issue["window"], "count": len(issue["articles"])}
    idx["issues"] = [i for i in idx["issues"] if i["id"] != issue["id"]] + [entry]
    idx["issues"].sort(key=lambda i: i["id"], reverse=True)
    idx_path.write_text(json.dumps(idx, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_inbox(root: Path) -> list[str]:
    p = root / "data" / "inbox.txt"
    if not p.exists():
        return []
    dois = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            d = norm_doi(line)
            if d:
                dois.append(d)
    return dois


def clear_inbox(root: Path, done: list[str]):
    p = root / "data" / "inbox.txt"
    if not p.exists() or not done:
        return
    keep = []
    for line in p.read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if body and norm_doi(body) in done:
            continue
        keep.append(line)
    p.write_text("\n".join(keep).rstrip() + "\n", encoding="utf-8")


def run(args) -> int:
    cfg = load_config(Path(args.config))
    root = Path(args.root)
    issue_date = dt.date.fromisoformat(args.issue_date) if args.issue_date else default_issue_date()
    until = dt.date.fromisoformat(args.until) if args.until else issue_date - dt.timedelta(days=1)
    since = dt.date.fromisoformat(args.since) if args.since else until - dt.timedelta(days=6)
    s, u = since.isoformat(), until.isoformat()
    log.info("Issue %s · window %s → %s", issue_date, s, u)

    http = Http(cfg)
    records: list[dict] = []
    if cfg["sources"].get("europepmc"):
        records += europepmc_search(http, cfg, s, u)
    if cfg["sources"].get("crossref"):
        records += crossref_search(http, cfg, s, u)
    if cfg["sources"].get("arxiv"):
        records += arxiv_search(http, cfg, s, u)

    relevant = [r for r in records if is_relevant(r, cfg)]
    inbox = read_inbox(root)
    done_inbox = []
    for d in inbox:
        rec = lookup_doi(http, d)
        if rec:
            rec["source"] = "inbox"
            relevant.append(rec)
            done_inbox.append(d)
    merged = merge_records(relevant)
    log.info("%d relevant papers after de-duplication (%d raw)", len(merged), len(records))

    issue_path = root / "data" / "issues" / f"{issue_date.isoformat()}.json"
    idx = load_json(root / "data" / "issues" / "index.json", {"issues": []})
    existing = load_json(issue_path, None)
    number = existing["number"] if existing else (max([i["number"] for i in idx["issues"]], default=0) + 1)
    issue = existing or {"id": issue_date.isoformat(), "number": number, "date": issue_date.isoformat(), "window": [s, u], "articles": [], "removed": []}
    known = {a["id"] for a in issue["articles"]} | set(issue.get("removed", []))
    known_doi = {a.get("doi") for a in issue["articles"] if a.get("doi")}

    figs = FigureFetcher(http, cfg, root)
    added = 0
    for rec in merged:
        art = to_article(rec, cfg)
        if art["id"] in known or (art["doi"] and art["doi"] in known_doi):
            continue
        if not args.no_figures:
            art["fig"] = figs.fetch(rec, art["id"])
        issue["articles"].append(art)
        added += 1
    issue["articles"].sort(key=lambda a: (a.get("feature", False), a.get("pick", False), a.get("date", "")), reverse=True)
    log.info("Added %d new articles (issue now has %d)", added, len(issue["articles"]))

    if args.dry_run:
        json.dump(issue, sys.stdout, indent=2, ensure_ascii=False)
        return 0
    issue_path.parent.mkdir(parents=True, exist_ok=True)
    issue_path.write_text(json.dumps(issue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    update_index(root, issue)
    figs.save_cache()
    clear_inbox(root, done_inbox)
    print(f"ISSUE_ID={issue['id']}")
    print(f"ISSUE_NUMBER={issue['number']}")
    print(f"ADDED={added}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "scraper" / "config.yaml"))
    ap.add_argument("--root", default=str(ROOT), help="repository root (where data/ and figs/ live)")
    ap.add_argument("--issue-date", help="YYYY-MM-DD, default: this week's Monday (Singapore time)")
    ap.add_argument("--since", help="window start YYYY-MM-DD (default: issue date − 7 days)")
    ap.add_argument("--until", help="window end YYYY-MM-DD (default: issue date − 1 day)")
    ap.add_argument("--no-figures", action="store_true", help="skip preview-image download")
    ap.add_argument("--dry-run", action="store_true", help="print the issue JSON instead of writing files")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
