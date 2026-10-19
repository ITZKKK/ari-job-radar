"""
Tests that need no network.

    python -m pytest -q            (from the project folder)

Most title cases are real postings read from employers' boards on 19 Oct 2026,
so the role filter is tested against what the boards actually say.
"""

import json
import os
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import adapters, crawl, notify, store  # noqa: E402
from src.adapters import RawPosting  # noqa: E402
from src.classify import Rules, classify, years_required  # noqa: E402

CFG = yaml.safe_load(open(os.path.join(ROOT, "config.yaml")))
RULES = Rules(CFG)

# (title, location, expected relevant, expected location bucket or None, expected seniority or None)
CASES = [
    # --- should be kept -------------------------------------------------------
    ("Editorial Coordinator", "Miami, Florida, United States / New York, New York, United States", True, "MIAMI", "entry"),
    ("ARTFORUM: Editorial Assistant", "New York - New York City Office", True, "NYC", "entry"),
    ("Billboard: Staff Writer, Latin/Pop", "California - Los Angeles Office; Florida - Miami Office", True, "MIAMI", "mid"),
    ("Billboard: Associate Producer, Branded Content", "California - Los Angeles Office; New York - New York City Office", True, "NYC", "entry"),
    ("WWD: Associate Editor, Branded Content", "New York - New York City Office", True, "NYC", "entry"),
    ("Real Estate Reporter", "New York, NY", True, "NYC", "mid"),
    ("Video & Social Media Producer, Criminal", "Remote (US Only)", True, "REMOTE", None),
    ("Reporter, Axios Houston", "Remote", True, "REMOTE", "mid"),
    ("Part Time-Temp Reporter, Axios Local", "Remote", True, "REMOTE", None),
    ("Assistant Editor, Data Journalism", "New York, NY; Washington, DC", True, "NYC", "entry"),
    ("Business Correspondent, Artificial Intelligence and Emerging Technologies", "San Francisco, CA", True, "OTHER_US", None),
    ("Data Reporter, Texas", "Texas", True, "OTHER_US", None),
    ("Fact Checker, Shows", "New York, NY", True, "NYC", None),
    ("Local Investigations Fellow", "Remote - USA", True, "REMOTE", "entry"),
    ("Reporter Fellow, Local Investigations - Mississippi, Louisiana, & Arkansas", "Arkansas; Louisiana; Mississippi", True, "OTHER_US", "entry"),
    ("Audience Development Coordinator (Contract)", "Washington, DC, New York, New York or Remote", True, "NYC", "entry"),
    ("Global Business and Finance Reporter", "New York, NY", True, "NYC", None),
    ("Publicist", "New York City", True, "NYC", None),
    ("Video Producer Fellowship", "New York, NY", True, "NYC", "entry"),
    ("Associate Video Producer", "Jersey City, NJ", True, "NYC", "entry"),
    ("Forbes Fellowship", "Jersey City, NJ", True, "NYC", "entry"),
    ("Temporary Social Media Producer", "Jersey City, NJ", True, "NYC", None),
    ("Social Media Partnerships Manager | The Points Guy", "New York, NY", True, "NYC", "mid"),
    ("TikTok & Instagram Reels Strategist | The Points Guy", "New York, NY", True, "NYC", None),
    ("Editor (Secret LA)", "Los Angeles", True, "OTHER_US", None),
    ("Part-Time News Writer, TZR", "New York, NY", True, "NYC", None),
    ("Markets Creator", "New York, NY", True, "NYC", None),
    ("Weekend Writer, Daily Newsletter", "New York, NY", True, "NYC", None),
    ("Web Content Editor", "Miami, FL", True, "MIAMI", None),
    ("Content Creator", "Miami Beach, FL", True, "MIAMI", None),
    ("Copywriter", "Fort Lauderdale, FL", True, "MIAMI", None),
    ("PR Coordinator", "Coral Gables, FL", True, "MIAMI", "entry"),
    ("Social Media Coordinator", "Wynwood, Miami", True, "MIAMI", "entry"),
    ("Event Operations Coordinator", "New York", True, "NYC", "entry"),
    ("Editor, Miami (Haute Living)", "", True, "MIAMI", None),
    ("Staff Writer", "", True, "UNKNOWN", "mid"),
    # --- should be dropped ----------------------------------------------------
    ("Head of Social, Vulture", "New York, NY", False, None, "senior"),
    ("Senior Editor, Vox", "NYC or DC", False, None, "senior"),
    ("Director of Editorial, Axios Live", "Washington D.C.", False, None, "senior"),
    ("Editor at Large, Opinion", "New York, NY", False, None, "senior"),
    ("Deputy Editor, Well", "New York, NY", False, None, "senior"),
    ("Local Advertising Account Executive - Miami", "Miami, FL", False, None, None),
    ("Assistant Editor, Visuals", "Seoul, Korea", False, "FOREIGN", None),
    ("Junior Business Reporter, Singapore", "Singapore, Central, Singapore", False, "FOREIGN", None),
    ("Reporter, Gulf Region - Middle East (Remote)", "Reporter, Gulf Region - Middle East (Remote)", False, "FOREIGN", None),
    ("Content Editor - French speaker", "Madrid", False, "FOREIGN", None),
    ("Digital Content Creator (Video + Articles)", "Santiago", False, "FOREIGN", None),
    ("Paid Social Strategist | The Points Guy", "New York, NY", False, None, None),
    ("Staff Software Engineer, New York Magazine", "New York, NY", False, None, None),
    ("Talent Acquisition Coordinator", "New York, NY", False, None, None),
    ("PMC: Accounts Receivable Specialist", "New York - New York City Office", False, None, None),
    ("Account Executive, Fashion", "New York, NY", False, None, None),
    ("Commerce Analyst", "New York, NY", False, None, None),
    ("Technical Writer", "Remote", False, None, None),
    ("Grant Writer", "Miami, FL", False, None, None),
    ("Supervising Producer", "New York, NY", False, None, "senior"),
    ("Merchandiser - PT", "Russell Springs, KY", False, None, None),
]


@pytest.mark.parametrize("title,loc,rel,bucket,sen", CASES)
def test_classify(title, loc, rel, bucket, sen):
    v = classify(title, loc, "", RULES)
    assert v.relevant is rel, f"{title!r}: {v.reason}"
    if bucket:
        assert v.location == bucket, f"{title!r} @ {loc!r} -> {v.location}"
    if sen:
        assert v.seniority == sen, f"{title!r} -> {v.seniority}"


def test_video_editor_kept_but_flagged():
    v = classify("Assistant Video Editor", "New York, NY", "", RULES)
    assert v.relevant and "video / photo editing" in v.flags


def test_years_required():
    assert years_required("Requires 2+ years of experience; 5+ years preferred") == 2
    assert years_required("Minimum of 3 years of newsroom experience") == 3
    assert years_required("1-3 years experience in social media") == 1
    assert years_required("Founded 25 years ago") is None
    v = classify("Staff Writer", "Miami, FL", "At least 6 years of professional writing experience", RULES)
    assert not v.relevant and "6+" in v.reason


def test_detect_from_url():
    d = adapters.detect_from_url
    assert d("https://job-boards.greenhouse.io/pmc/jobs/123") == {"ats": "greenhouse", "ats_token": "pmc"}
    assert d("https://apply.workable.com/the-drum/j/AE03E4894D/") == {"ats": "workable", "ats_token": "the-drum"}
    assert d("https://jobs.lever.co/BDG") == {"ats": "lever", "ats_token": "BDG"}
    assert d("https://meredith.wd5.myworkdayjobs.com/en-US/EXT/job/x") == {
        "ats": "workday", "ats_token": "meredith", "ats_dc": "wd5", "ats_site": "EXT"}
    assert d("https://hauteliving.com/careers") == {"ats": "none"}


# ---------------------------------------------------------------------------
# Adapters against real response shapes (trimmed samples from 19 Oct 2026)
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status
        self.text = json.dumps(data)
        self.content = self.text.encode()

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, data):
        self.data = data

    def get(self, url, **kw):
        return FakeResp(self.data)

    def post(self, url, json=None):
        return FakeResp(self.data)


def test_workable_merges_cities():
    data = {"jobs": [
        {"title": "Editorial Coordinator", "shortcode": "AE03E4894D", "city": "Miami", "state": "Florida",
         "country": "United States", "telecommuting": False, "url": "https://apply.workable.com/j/AE03E4894D",
         "published_on": "2026-06-11", "description": "<p>Drive coordination</p>"},
        {"title": "Editorial Coordinator", "shortcode": "AE03E4894D", "city": "New York", "state": "New York",
         "country": "United States", "telecommuting": False, "url": "https://apply.workable.com/j/AE03E4894D",
         "published_on": "2026-06-11", "description": "<p>Drive coordination</p>"},
    ]}
    out = adapters.fetch_workable(FakeClient(data), {"id": "the-drum", "name": "The Drum", "ats_token": "the-drum"})
    assert len(out) == 1
    assert "Miami" in out[0].location_raw and "New York" in out[0].location_raw


def test_ukg_shape():
    data = {"totalCount": 1, "opportunities": [{
        "Id": "abc", "Title": "Kentucky Men's Basketball Insider", "PostedDate": "2026-10-10",
        "BriefDescription": "<p>Cover UK</p>",
        "Locations": [{"Address": {"City": "Lexington", "State": {"Code": "KY"}}, "LocalizedDescription": "Lexington Herald Leader"}]}]}
    out = adapters.fetch_ukg(FakeClient(data), {"id": "mh", "name": "Miami Herald", "ats_token": "https://x/JobBoard/g"})
    assert out[0].location_raw == "Lexington, KY"
    assert out[0].url.endswith("OpportunityDetail?opportunityId=abc")


# ---------------------------------------------------------------------------
# The crawl's state machine, end to end, with fake boards
# ---------------------------------------------------------------------------

def _post(src, jid, title, loc, company="The Drum"):
    return RawPosting(source_id=src, job_id=jid, title=title, company=company, location_raw=loc, url=f"https://x/{jid}")


def test_crawl_lifecycle(tmp_path, monkeypatch):
    state_p, radar_p = str(tmp_path / "state.json"), str(tmp_path / "radar.json")
    monkeypatch.setattr(crawl.aggregators, "enabled", lambda cfg, state=None: [])
    monkeypatch.setattr(crawl.watch, "check", lambda *a, **k: {"watched": 0, "changed": 0, "unwatchable": 0})
    monkeypatch.setattr(crawl.time, "sleep", lambda s: None)

    boards = {"the-drum": [_post("the-drum", "A", "Editorial Coordinator", "Miami, FL"),
                           _post("the-drum", "B", "Finance Manager", "Miami, FL")]}
    broken = {"flag": False}

    def fake_fetch(client, emp):
        if emp["id"] != "the-drum":
            return []
        if broken["flag"]:
            raise adapters.AdapterError("boom")
        return list(boards["the-drum"])

    fetchers = {k: fake_fetch for k in adapters.ADAPTERS}

    # Run 1: baseline. Everything is marked baseline; no "new" alerts later.
    crawl.run(state_path=state_p, radar_path=radar_p, fetchers=fetchers)
    s = store.load(state_p)
    rel = [p for p in s["postings"].values() if p["relevant"]]
    assert len(rel) == 1 and rel[0]["baseline"]
    assert notify.pending(s, CFG) == []

    # Run 2: a new Miami reporter job appears -> pending for email.
    boards["the-drum"].append(_post("the-drum", "C", "Reporter", "Miami, FL"))
    crawl.run(state_path=state_p, radar_path=radar_p, fetchers=fetchers)
    s = store.load(state_p)
    pend = notify.pending(s, CFG)
    assert [p["title"] for p in pend] == ["Reporter"]

    # Run 3: the board FAILS. Nothing may be marked closed.
    broken["flag"] = True
    crawl.run(state_path=state_p, radar_path=radar_p, fetchers=fetchers)
    s = store.load(state_p)
    assert all(p["status"] == "open" for p in s["postings"].values())
    assert s["sources"]["the-drum"]["consecutive_failures"] == 1

    # Run 4: the board is read and job A is gone -> closed.
    broken["flag"] = False
    boards["the-drum"] = [p for p in boards["the-drum"] if p.job_id != "A"]
    crawl.run(state_path=state_p, radar_path=radar_p, fetchers=fetchers)
    s = store.load(state_p)
    a = [p for p in s["postings"].values() if p["job_id"] == "A"][0]
    assert a["status"] == "closed"

    radar = json.load(open(radar_p))
    titles = {p["title"]: p["status"] for p in radar["postings"]}
    assert titles == {"Editorial Coordinator": "closed", "Reporter": "open"}
    assert "Finance Manager" not in titles                     # irrelevant never exported
    drum = [e for e in radar["employers"] if e["id"] == "the-drum"][0]
    assert drum["mode"] == "read" and drum["open_relevant"] == 1


def test_duplicates_collapse_to_employer_copy():
    st = store.empty()
    st["postings"] = {
        "e": {"pid": "e", "kind": "employer", "employer_id": "the-drum", "company": "The Drum", "title": "Reporter",
              "location": "MIAMI", "status": "open", "relevant": True, "first_seen": "2026-10-19T10:00:00+00:00"},
        "a": {"pid": "a", "kind": "aggregator", "employer_id": "the-drum", "company": "The Drum Ltd", "title": "Reporter (Miami)",
              "location": "MIAMI", "status": "open", "relevant": True, "first_seen": "2026-10-19T09:00:00+00:00"},
    }
    crawl.mark_duplicates(st)
    assert st["postings"]["a"]["dupe_of"] == "e" and "dupe_of" not in st["postings"]["e"]


def test_email_builds_and_orders_targets_first():
    labels = {b["id"]: b["label"] for b in CFG["locations"]["buckets"]}
    fams = {f["id"]: f["label"] for f in CFG["roles"]["families"]}
    posts = [
        {"title": "Copywriter", "company": "Some Agency", "employer_id": None, "location": "MIAMI", "tier": "also",
         "family": "marketing_copy", "url": "https://x/1", "location_raw": "Miami, FL", "seniority": "mid", "flags": []},
        {"title": "Editorial Coordinator", "company": "The Drum", "employer_id": "the-drum", "target": True, "location": "MIAMI",
         "tier": "core", "family": "editorial", "url": "https://x/2", "location_raw": "Miami, FL", "seniority": "entry", "flags": []},
    ]
    subject, html = notify.build(posts, [], [], CFG, labels, fams)
    assert subject.startswith("[Job Radar] 2 new jobs for you")
    assert html.index("Editorial Coordinator") < html.index("Copywriter")
    assert "★</span> <b style=" in html and "The Drum</b>" in html


def test_rss_and_adzuna_shapes(monkeypatch):
    from src import aggregators

    class R(FakeResp):
        pass
    rss = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Acme Media: Content Writer</title><link>https://weworkremotely.com/x</link>
      <guid>wwr-1</guid><region>Anywhere in the World</region><pubDate>Sat, 18 Oct 2026 10:00:00 +0000</pubDate>
      <description>&lt;p&gt;Write things&lt;/p&gt;</description></item></channel></rss>"""

    class RC:
        def get(self, url, **kw):
            r = FakeResp({}); r.content = rss; return r
    out = aggregators.fetch_rss(RC(), {"id": "wwr", "name": "WWR", "url": "u", "title_format": "company: title",
                                       "default_location": "Remote"})
    assert out[0].company == "Acme Media" and out[0].title == "Content Writer"

    monkeypatch.setenv("ADZUNA_APP_ID", "x"); monkeypatch.setenv("ADZUNA_APP_KEY", "y")
    monkeypatch.setattr(aggregators.time, "sleep", lambda s: None)
    data = {"results": [{"id": 1, "title": "<strong>Editorial</strong> Assistant", "company": {"display_name": "Haute Living"},
                         "location": {"display_name": "Miami, Miami-Dade County"}, "redirect_url": "https://adzuna/1",
                         "description": "Write", "created": "2026-10-18T00:00:00Z"}]}
    out = aggregators.fetch_adzuna(FakeClient(data), {"where": ["Miami, FL"], "what_or": "editor", "pages": 1})
    assert out[0].title == "Editorial Assistant" and out[0].company == "Haute Living"
    v = classify(out[0].title, out[0].location_raw, "", RULES)
    assert v.relevant and v.location == "MIAMI"
    match = crawl.employer_matcher(yaml.safe_load(open(os.path.join(ROOT, "employers.yaml")))["employers"])
    assert match("Haute Living") == "haute-living" and match("Eater") == "pmc"
