"""Offline tests for the VibraNews scraper (no network: HTTP is faked)."""
import io
import json
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import vibranews_scraper as vs  # noqa: E402

CFG = vs.load_config(HERE.parent / "config.yaml")


# ----------------------------------------------------------------- fixtures

EPMC_RESULT = {
    "id": "40000001", "source": "MED", "pmid": "40000001", "doi": "10.1038/s41467-026-00001-x",
    "title": "Hyperspectral stimulated Raman scattering imaging of lipid droplets in living cells.",
    "authorString": "Tan A, Lim B, Huang Z.",
    "authorList": {"author": [
        {"fullName": "Tan A", "authorAffiliationDetailsList": {"authorAffiliation": [{"affiliation": "Department of Biomedical Engineering, National University of Singapore, Singapore 117576. a.tan@nus.edu.sg"}]}},
        {"fullName": "Lim B", "authorAffiliationDetailsList": {"authorAffiliation": [{"affiliation": "Institute of Bioengineering and Bioimaging, A*STAR, Singapore"}]}},
        {"fullName": "Huang Z", "authorAffiliationDetailsList": {"authorAffiliation": [{"affiliation": "Department of Biomedical Engineering, National University of Singapore, Singapore"}]}},
    ]},
    "journalInfo": {"journal": {"title": "Nature Communications", "essn": "2041-1723"}},
    "firstPublicationDate": "2026-11-04",
    "abstractText": "<h4>Abstract</h4>Lipid droplets are dynamic organelles. We use hyperspectral SRS microscopy in the C-H region to follow their chemistry in living cells. A deep learning denoiser improves single-pixel sensitivity tenfold.",
    "isOpenAccess": "Y", "license": "cc by",
    "pubTypeList": {"pubType": ["research-article", "Journal Article"]},
}

EPMC_NOISE = {  # fNIRS paper: must be excluded
    "id": "40000002", "source": "MED", "doi": "10.1000/fnirs.1",
    "title": "Functional near-infrared spectroscopy of prefrontal cortex during working memory",
    "authorList": {"author": [{"fullName": "Doe J"}]},
    "journalInfo": {"journal": {"title": "NeuroImage"}},
    "firstPublicationDate": "2026-11-03", "abstractText": "fNIRS was used.", "isOpenAccess": "N",
}

CROSSREF_ITEM = {
    "DOI": "10.1002/JRS.70999",
    "title": ["Tip-enhanced Raman spectroscopy of graphene edges"],
    "author": [{"given": "Maria", "family": "Rossi", "affiliation": [{"name": "Dipartimento di Fisica, Politecnico di Milano, Milan, Italy"}]}],
    "container-title": ["Journal of Raman Spectroscopy"], "ISSN": ["0377-0486"],
    "created": {"date-parts": [[2026, 11, 5]]}, "published-online": {"date-parts": [[2026, 11, 5]]},
    "abstract": "<jats:p>We report TERS maps of graphene edges with 2 nm resolution.</jats:p>",
    "license": [{"URL": "http://onlinelibrary.wiley.com/termsAndConditions"}],
}
CROSSREF_DUP = {  # same DOI as the Europe PMC record, should merge away
    "DOI": "10.1038/s41467-026-00001-x", "title": ["Hyperspectral stimulated Raman scattering imaging of lipid droplets in living cells"],
    "author": [], "container-title": ["Nature Communications"], "created": {"date-parts": [[2026, 11, 4]]},
    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
}

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2611.01234v1</id>
    <published>2026-11-06T10:00:00Z</published>
    <title>Quantum-enhanced stimulated Raman microscopy with squeezed light</title>
    <summary>We demonstrate stimulated Raman imaging below the shot-noise limit.</summary>
    <author><name>Ana Silva</name><arxiv:affiliation>University of Queensland</arxiv:affiliation></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2610.00001v2</id>
    <published>2026-10-01T10:00:00Z</published>
    <title>Old Raman paper outside the window</title>
    <summary>Raman.</summary>
    <author><name>X Y</name></author>
  </entry>
</feed>"""

ARTICLE_HTML = """<html><head>
<meta property="og:title" content="paper">
<meta property="og:image" content="/figures/fig1.png">
</head><body></body></html>"""
COVER_HTML = """<html><head><meta name="twitter:image" content="https://cdn.example.org/jrs/cover_image.jpg"></head></html>"""


def png_bytes(w=600, h=400, color=(30, 120, 160)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class FakeResp:
    def __init__(self, url, body=b"", ctype="text/html", status=200):
        self.url, self.status_code = url, status
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("utf-8", "ignore")
        self.headers = {"Content-Type": ctype}

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    calls: list = []

    def __init__(self, cfg):
        self.cfg = cfg

    def get(self, url, check_robots=False, params=None, **kw):
        FakeHttp.calls.append((url, params))
        if "europepmc" in url:
            q = (params or {}).get("query", "")
            if q.startswith("DOI:"):
                return FakeResp(url, json.dumps({"resultList": {"result": []}}), "application/json")
            if (params or {}).get("cursorMark") == "*":
                return FakeResp(url, json.dumps({"nextCursorMark": "AAA", "resultList": {"result": [EPMC_RESULT, EPMC_NOISE]}}), "application/json")
            return FakeResp(url, json.dumps({"nextCursorMark": "AAA", "resultList": {"result": []}}), "application/json")
        if "api.crossref.org/works/" in url:   # single-DOI lookup (inbox)
            item = dict(CROSSREF_ITEM, DOI="10.1364/OE.999999", title=["Inbox paper: Raman fibre probe"])
            return FakeResp(url, json.dumps({"message": item}), "application/json")
        if "api.crossref.org/works" in url:
            return FakeResp(url, json.dumps({"message": {"items": [CROSSREF_ITEM, CROSSREF_DUP]}}), "application/json")
        if "arxiv.org/api" in url:
            return FakeResp(url, ARXIV_XML, "application/atom+xml")
        if url == "https://doi.org/10.1038/s41467-026-00001-x":
            return FakeResp("https://www.nature.com/articles/s41467-026-00001-x", ARTICLE_HTML)
        if url == "https://www.nature.com/figures/fig1.png":
            return FakeResp(url, png_bytes(), "image/png")
        if url == "https://doi.org/10.1002/jrs.70999":
            return FakeResp("https://onlinelibrary.wiley.com/doi/10.1002/jrs.70999", COVER_HTML)
        if url == "https://cdn.example.org/jrs/cover_image.jpg":
            return FakeResp(url, png_bytes(300, 400, (200, 60, 60)), "image/jpeg")
        return None


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "data" / "issues").mkdir(parents=True)
    (tmp_path / "data" / "inbox.txt").write_text("# submissions\n10.1364/OE.999999\n", encoding="utf-8")
    monkeypatch.setattr(vs, "Http", FakeHttp)
    FakeHttp.calls = []
    return tmp_path


# ----------------------------------------------------------------- unit tests

def test_parse_europepmc_fields():
    r = vs.parse_europepmc(EPMC_RESULT)
    assert r["title"].endswith("living cells")
    assert r["doi"] == "10.1038/s41467-026-00001-x"
    assert r["oa"] is True and r["license"] == "CC BY"
    assert r["journal"] == "Nature Communications"
    assert "Lipid droplets are dynamic" in r["abstract"] and "Abstract" not in r["abstract"][:9]


def test_institutions_strip_departments_and_emails():
    r = vs.parse_europepmc(EPMC_RESULT)
    inst = vs.institutions_from(r["affiliations"])
    assert inst[0] == "National University of Singapore"
    assert any("A*STAR" in i or "Institute of Bioengineering" in i for i in inst)
    assert len(inst) == 2  # NUS de-duplicated


def test_relevance_filters():
    assert vs.is_relevant(vs.parse_europepmc(EPMC_RESULT), CFG)
    assert not vs.is_relevant(vs.parse_europepmc(EPMC_NOISE), CFG)
    assert not vs.is_relevant({"title": "Correction to: Raman imaging of cells", "abstract": ""}, CFG)
    # abstract-only match needs two hits
    assert not vs.is_relevant({"title": "Cell mechanics", "abstract": "We also measured one Raman spectrum."}, CFG)
    assert vs.is_relevant({"title": "Cell mechanics", "abstract": "Raman spectra and Brillouin maps were acquired."}, CFG)


def test_categories_and_glyph():
    r = vs.parse_europepmc(EPMC_RESULT)
    cats = vs.categorize(r, CFG)
    assert "SRS / CARS" in cats and "Cell Biology" in cats
    assert vs.glyph_for(cats) == "srs"
    assert cats.index("Cell Biology") < cats.index("SRS / CARS")  # applications listed first, OCT-style


def test_crossref_and_arxiv_parsing():
    c = vs.parse_crossref(CROSSREF_ITEM)
    assert c["doi"] == "10.1002/jrs.70999" and c["date"] == "2026-11-05" and not c["oa"]
    assert vs.parse_crossref(CROSSREF_DUP)["license"] == "CC BY 4.0"
    a = vs.parse_arxiv(ARXIV_XML, "2026-11-02", "2026-11-08")
    assert len(a) == 1 and a[0]["url"] == "https://arxiv.org/abs/2611.01234"


def test_merge_dedupes_by_doi_and_fills_license():
    recs = [vs.parse_crossref(CROSSREF_DUP), vs.parse_europepmc(EPMC_RESULT), vs.parse_crossref(CROSSREF_ITEM)]
    merged = vs.merge_records(recs)
    assert len(merged) == 2
    assert merged[0]["source"] == "europepmc"


def test_trim_summary_sentence_boundary():
    s = "First sentence is here. " * 60
    t = vs.trim_summary(s, 200)
    assert len(t) <= 205 and t.endswith("…") and ". …" in t


def test_preview_image_extraction_and_cover_detection():
    assert vs.extract_preview_image(ARTICLE_HTML, "https://www.nature.com/articles/x") == "https://www.nature.com/figures/fig1.png"
    url = vs.extract_preview_image(COVER_HTML, "https://onlinelibrary.wiley.com/doi/x")
    assert vs.looks_like_cover(url)
    assert vs.extract_preview_image("<html></html>", "https://x.org") is None


def test_save_thumbnail_rejects_logos(tmp_path):
    assert not vs.save_thumbnail(png_bytes(64, 64), tmp_path / "a.jpg", CFG)
    assert vs.save_thumbnail(png_bytes(1600, 1000), tmp_path / "b.jpg", CFG)
    im = Image.open(tmp_path / "b.jpg")
    assert im.size[0] <= 400 and im.size[1] <= 280


# ----------------------------------------------------------------- end-to-end (fake network)

def run_cli(repo, *extra):
    return vs.main(["--root", str(repo), "--issue-date", "2026-11-09", *extra])


def test_end_to_end_issue_with_figure_cover_and_inbox(repo):
    assert run_cli(repo) == 0
    issue = json.loads((repo / "data/issues/2026-11-09.json").read_text())
    assert issue["window"] == ["2026-11-02", "2026-11-08"]
    titles = [a["title"] for a in issue["articles"]]
    assert any("lipid droplets" in t for t in titles)
    assert any("Tip-enhanced" in t for t in titles)
    assert any("Quantum-enhanced" in t for t in titles)     # arXiv
    assert any("Inbox paper" in t for t in titles)          # submitted DOI
    assert not any("Functional near-infrared" in t for t in titles)
    by = {a["title"][:20]: a for a in issue["articles"]}
    srs = next(a for a in issue["articles"] if "lipid droplets" in a["title"])
    assert srs["fig"]["kind"] == "figure" and (repo / srs["fig"]["src"]).exists()
    assert "CC BY" in srs["fig"]["credit"]
    ters = next(a for a in issue["articles"] if "Tip-enhanced" in a["title"])
    assert ters["fig"]["kind"] == "cover" and ters["fig"]["src"].startswith("covers/")
    qe = next(a for a in issue["articles"] if "Quantum-enhanced" in a["title"])
    assert qe["fig"] is None and qe["glyph"] == "srs"
    idx = json.loads((repo / "data/issues/index.json").read_text())
    assert idx["issues"][0]["id"] == "2026-11-09" and idx["issues"][0]["number"] == 1
    assert "10.1364" not in (repo / "data/inbox.txt").read_text()   # processed DOI removed
    assert json.loads((repo / "covers/index.json").read_text())["journal of raman spectroscopy"]["kind"] == "cover"


def test_rerun_keeps_editor_changes_and_removed(repo):
    run_cli(repo, "--no-figures")
    p = repo / "data/issues/2026-11-09.json"
    issue = json.loads(p.read_text())
    srs = next(a for a in issue["articles"] if "lipid droplets" in a["title"])
    srs["feature"] = True
    srs["summary"] = "Editor's own summary."
    victim = next(a for a in issue["articles"] if "Tip-enhanced" in a["title"])
    issue["articles"].remove(victim)
    issue["removed"].append(victim["id"])
    p.write_text(json.dumps(issue))
    run_cli(repo, "--no-figures")
    again = json.loads(p.read_text())
    srs2 = next(a for a in again["articles"] if "lipid droplets" in a["title"])
    assert srs2["feature"] and srs2["summary"] == "Editor's own summary."
    assert not any("Tip-enhanced" in a["title"] for a in again["articles"])
    assert again["articles"][0]["id"] == srs2["id"]  # feature sorts first


def test_open_access_only_mode_skips_paywalled_figures(repo, monkeypatch):
    cfg = vs.load_config(HERE.parent / "config.yaml")
    cfg["figures"]["mode"] = "open_access_only"
    f = vs.FigureFetcher(FakeHttp(cfg), cfg, repo)
    paywalled = vs.parse_crossref(CROSSREF_ITEM)
    assert f.fetch(paywalled, "x") is None          # no manual cover configured -> glyph
    oa = vs.parse_europepmc(EPMC_RESULT)
    assert f.fetch(oa, "y")["kind"] == "figure"
