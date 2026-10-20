"""
Tests that need no network.

    python -m pytest -q            (from the project folder)

Most title cases are real postings read from employers' boards on 19 Oct 2026,
so the role filter is tested against what the boards actually say.
"""

import json
import os
import shutil
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import add_employer as add_emp  # noqa: E402
import handle_request as hreq  # noqa: E402
from src import adapters, crawl, notify, store  # noqa: E402
from src.adapters import RawPosting  # noqa: E402
from src.classify import Rules, classify, visible, work_mode, years_required  # noqa: E402

CFG = yaml.safe_load(open(os.path.join(ROOT, "config.yaml")))
RULES = Rules(CFG)
EMPLOYERS_YAML = os.path.join(ROOT, "employers.yaml")

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
    # "100 years" must not read as "00 years" = 0 and beat the real requirement.
    assert years_required("Over 100 years of experience in publishing. Requires 5+ years of experience.") == 5
    v = classify("Staff Writer", "Miami, FL", "At least 6 years of professional writing experience", RULES)
    assert not v.relevant and "6+" in v.reason


# ---------------------------------------------------------------------------
# Work mode, early-career fit, visibility (Miami or fully remote)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc,title,desc,hint,expected", [
    ("Remote - USA", "", "", None, "remote"),
    ("New York, NY", "Reporter", "", None, "unknown"),
    ("New York, NY", "Reporter", "hybrid role, 3 days a week in our office", None, "hybrid"),
    ("Miami, FL", "Writer", "This position is on-site.", None, "onsite"),
    ("Washington, DC, New York, New York or Remote", "", "", None, "remote"),
    ("Remote (Worldwide)", "", "in the Chicago office two days per week", None, "remote"),   # head wins
    ("Miami, FL", "Coordinator", "fully remote", None, "remote"),
    ("New York, NY", "Editor", "This is not a remote position", None, "onsite"),
    ("New York, NY", "Editor", "we run virtual events", None, "unknown"),
    ("New York, NY", "Editor", "", "on-site", "onsite"),
    ("New York, NY", "Editor", "three days a week in office", None, "hybrid"),
    ("New York, NY", "Editor", "work from our office\n3 days a week", None, "hybrid"),
    ("New York, NY", "Editor", "5 days a week in office", None, "onsite"),
    ("Miami, FL", "Social Media Coordinator", "coordinate with our remote team of freelancers", None, "unknown"),
    ("Miami, FL", "Social Media Coordinator", "This role is remote within the United States.", None, "remote"),
])
def test_work_mode(loc, title, desc, hint, expected):
    assert work_mode(loc, title, desc, hint) == expected


def test_board_remote_hint_counts_as_offered():
    # Lever says workplaceType "remote" for a job filed under New York: shown and emailed.
    from src.classify import visible
    v = classify("Editor", "New York, NY", "", RULES, workplace_hint="remote")
    assert v.remote_offered and v.work_mode == "remote"
    assert visible(v.location, v.work_mode, CFG["locations"], v.remote_offered)


@pytest.mark.parametrize("title,desc,expected", [
    ("Editorial Coordinator", "", "early"),
    ("Reporter", "", "open"),
    ("Reporter", "2+ years of experience", "early"),
    ("Reporter", "0-2 years of experience", "early"),
    ("Local Investigations Fellow", "3+ years of experience", "stretch"),
    ("Staff Writer", "Minimum of 4 years of experience", "stretch"),
    ("Content Marketing Manager", "", "stretch"),
    ("Reporter", "Recent graduates encouraged to apply", "early"),
    ("Writer", "entry-level position", "early"),
    ("Editor", "At least 6 years of experience", None),          # dropped, not labelled
])
def test_fit(title, desc, expected):
    v = classify(title, "Miami, FL", desc, RULES)
    if expected is None:
        assert not v.relevant
        return
    assert v.relevant and v.fit == expected, f"{title!r}: {v.fit} ({v.reason})"
    if desc.startswith("0-2"):
        assert v.years == 0


def test_entry_marker_roman_one():
    assert classify("Reporter I", "Miami, FL", "", RULES).seniority == "entry"
    assert classify("Editor II", "Miami, FL", "", RULES).seniority == "mid"
    assert classify("I Heart Radio Writer", "Miami, FL", "", RULES).seniority == "mid"


def test_visible():
    cfg = CFG["locations"]
    assert visible("MIAMI", "onsite", cfg)
    assert visible("MIAMI", "unknown", cfg)
    assert visible("REMOTE", "remote", cfg)
    assert not visible("REMOTE", "hybrid", cfg)
    assert visible("NYC", "unknown", cfg, remote_in_head=True)
    assert not visible("NYC", "unknown", cfg)
    assert not visible("NYC", "remote", cfg)                  # remote only in the description body
    assert not visible("OTHER_US", "onsite", cfg)
    assert not visible("FOREIGN", "remote", cfg, remote_in_head=True)


def test_remote_offered_is_a_head_signal():
    v = classify("Audience Development Coordinator", "Washington, DC, New York, New York or Remote", "", RULES)
    assert v.location == "NYC" and v.remote_offered and v.work_mode == "remote"
    assert visible(v.location, v.work_mode, CFG["locations"], v.remote_offered)
    v = classify("Virtual Events Producer", "New York, NY", "", RULES)
    assert v.relevant and not v.remote_offered
    assert not visible(v.location, v.work_mode, CFG["locations"], v.remote_offered)
    v = classify("Reporter", "New York, NY", "You can work remotely some days", RULES)
    assert not v.remote_offered


def test_default_location():
    v = classify("Freelance Writer", "", "", RULES, default_location="MIAMI")
    assert v.location == "MIAMI"
    assert "assumed Miami (employer is Miami-based)" in v.flags and "location not stated" in v.flags
    v = classify("Freelance Writer", "New York, NY", "", RULES, default_location="MIAMI")
    assert v.location == "NYC" and not any("assumed" in f for f in v.flags)
    v = classify("Freelance Writer", "", "", RULES)
    assert v.location == "UNKNOWN" and v.flags == ["location not stated"]


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

    # Run 5: a New York job (hidden: Ari wants Miami or remote) and a
    # "New York or Remote" one (shown, as Remote).
    boards["the-drum"] += [_post("the-drum", "D", "Staff Writer", "New York, NY"),
                           _post("the-drum", "E", "Newsletter Writer", "New York, NY or Remote")]
    crawl.run(state_path=state_p, radar_path=radar_p, fetchers=fetchers)
    s = store.load(state_p)
    e = [p for p in s["postings"].values() if p["job_id"] == "E"][0]
    assert e["location"] == "NYC" and e["remote_offered"] and e["work_mode"] == "remote"   # state: true bucket
    radar = json.load(open(radar_p))
    by_title = {p["title"]: p for p in radar["postings"]}
    assert "Staff Writer" not in by_title
    assert by_title["Newsletter Writer"]["location"] == "REMOTE"
    assert by_title["Newsletter Writer"]["location_raw"] == "New York, NY or Remote"
    assert {p["location"] for p in radar["postings"]} <= {"MIAMI", "REMOTE"}
    assert all(p["work_mode"] and p["fit"] for p in radar["postings"])
    assert by_title["Editorial Coordinator"]["fit"] == "early" and by_title["Reporter"]["fit"] == "open"
    assert [w["id"] for w in radar["work_modes"]] == ["remote", "hybrid", "onsite", "unknown"]
    assert [f["id"] for f in radar["fits"]] == ["early", "open", "stretch"]
    assert radar["requests"]["add_company_url"].endswith("template=add-company.yml")
    assert radar["requests"]["refresh_url"].endswith("template=refresh.md")
    assert set(radar["requests"]) == {"repo_url", "add_company_url", "refresh_url", "actions_url"}
    assert radar["totals"]["open_all"] == 3 and radar["totals"]["open_shown"] == 2
    assert radar["totals"]["by_location_all"] == {"NYC": 2, "MIAMI": 1}
    assert radar["totals"]["open_all"] >= radar["totals"]["open_shown"]
    assert [loc["id"] for loc in radar["locations"]] == ["MIAMI", "REMOTE"]
    drum = [e for e in radar["employers"] if e["id"] == "the-drum"][0]
    assert "linkedin_url" in drum and drum["open_relevant"] == 2   # Reporter + Newsletter Writer, not Staff Writer


def test_export_backfills_old_state(tmp_path):
    """Rows written before work_mode / fit / remote_offered existed."""
    st = store.empty()
    st["postings"] = {
        "o": {"pid": "o", "kind": "employer", "employer_id": "the-drum", "company": "The Drum", "title": "Staff Writer",
              "location": "NYC", "location_raw": "New York or Remote", "status": "open", "relevant": True,
              "first_seen": "2026-10-19T10:00:00+00:00", "seniority": "mid", "years": 3, "flags": []},
        "u": {"pid": "u", "kind": "employer", "employer_id": "the-drum", "company": "The Drum", "title": "Editorial Assistant",
              "location": "MIAMI", "location_raw": "Miami, FL", "status": "open", "relevant": True,
              "first_seen": "2026-10-19T10:00:00+00:00", "seniority": "entry", "years": None, "flags": []},
    }
    out = str(tmp_path / "radar.json")
    emps = yaml.safe_load(open(EMPLOYERS_YAML))["employers"]
    crawl.export(st, CFG, emps, [], out)
    r = {p["title"]: p for p in json.load(open(out))["postings"]}
    assert r["Staff Writer"]["location"] == "REMOTE" and r["Staff Writer"]["remote_offered"]
    assert r["Staff Writer"]["fit"] == "stretch" and r["Staff Writer"]["work_mode"] == "remote"
    assert r["Editorial Assistant"]["fit"] == "early" and r["Editorial Assistant"]["work_mode"] == "unknown"
    wre = [e for e in json.load(open(out))["employers"] if e["id"] == "world-red-eye"][0]
    assert wre["linkedin_url"].startswith("https://www.linkedin.com/company/")


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
         "tier": "core", "family": "editorial", "url": "https://x/2", "location_raw": "Miami, FL", "seniority": "entry",
         "fit": "early", "flags": []},
        {"title": "Fact Checker", "company": "Vox", "employer_id": None, "location": "NYC", "tier": "core",
         "family": "editorial", "url": "https://x/3", "location_raw": "New York, NY or Remote", "seniority": "mid",
         "fit": "open", "remote_offered": True, "work_mode": "remote", "flags": []},
        {"title": "Staff Writer", "company": "Billboard", "employer_id": None, "location": "MIAMI", "tier": "core",
         "family": "editorial", "url": "https://x/4", "location_raw": "Miami, FL", "seniority": "mid", "years": 2,
         "fit": "early", "flags": []},
    ]
    subject, html = notify.build(posts, [], [], CFG, labels, fams)
    assert subject.startswith("[Job Radar] 4 new jobs for you")
    # ★ first; then core early-career before core "open"; "also" tier last.
    assert (html.index("Editorial Coordinator") < html.index("Staff Writer")
            < html.index("Fact Checker") < html.index("Copywriter"))
    assert "★</span> <b style=" in html and "The Drum</b>" in html
    assert "Early career" in html and "Entry level" not in html
    assert "Remote or New York" in html


def test_pending_uses_the_dashboard_rule():
    now = store.now_iso()
    base = {"relevant": True, "status": "open", "tier": "core", "first_seen": now}
    st = store.empty()
    st["postings"] = {
        "nyc": {"pid": "nyc", "title": "Reporter", "location": "NYC", "work_mode": "unknown", **base},
        "nyc_remote": {"pid": "nyc_remote", "title": "Writer", "location": "NYC", "work_mode": "remote",
                       "remote_offered": True, **base},
        "hybrid": {"pid": "hybrid", "title": "Editor", "location": "REMOTE", "work_mode": "hybrid", **base},
        "miami": {"pid": "miami", "title": "Copywriter", "location": "MIAMI", "work_mode": "onsite", **base},
        "old": {"pid": "old", "title": "Old Row", "location": "MIAMI", **base},     # no work_mode yet
    }
    assert sorted(p["pid"] for p in notify.pending(st, CFG)) == ["miami", "nyc_remote", "old"]
    assert "ready (3 jobs open now)" in notify.welcome(st, CFG)[0]


# ---------------------------------------------------------------------------
# Adding a company: the script, and the issue-form handler behind the button
# ---------------------------------------------------------------------------

def _employers_copy(tmp_path) -> str:
    p = str(tmp_path / "employers.yaml")
    shutil.copy(EMPLOYERS_YAML, p)
    return p


def test_add_employer(tmp_path):
    p = _employers_copy(tmp_path)
    rows = lambda: {r["id"]: r for r in yaml.safe_load(open(p))["employers"]}  # noqa: E731
    n0 = len(rows())

    r = add_emp.add_employer("Acme Media", "https://job-boards.greenhouse.io/acmemedia", path=p, check=False)
    # Unchecked board: verified false, so the crawl watches the page until someone flips it.
    assert r["created"] and r["id"] == "acme-media" and r["mode"] == "watch" and r["ats"] == "greenhouse"
    row = rows()["acme-media"]
    assert row["verified"] is False and row["ats_token"] == "acmemedia" and row["aliases"] == ["Acme Media"]

    r = add_emp.add_employer("Plain Co", "https://plainco.example/careers", path=p, check=False)
    assert r["created"] and r["mode"] == "watch"
    assert rows()["plain-co"]["careers_url"] == "https://plainco.example/careers" and "watch" not in rows()["plain-co"]

    r = add_emp.add_employer("Linked Co", "https://www.linkedin.com/company/linked-co/", path=p, check=False)
    assert r["created"] and r["mode"] == "manual"
    row = rows()["linked-co"]
    assert row["linkedin_url"] == "https://www.linkedin.com/company/linked-co/" and row["watch"] is False
    assert row["ats"] == "none" and "careers_url" not in row and "LinkedIn" in row["note"]

    r = add_emp.add_employer("Nothing Known", path=p, check=False)
    assert r["created"] and r["mode"] == "manual" and rows()["nothing-known"]["note"] == "No careers page given"

    # Already on the list, under an alias: no new row, LinkedIn link added.
    r = add_emp.add_employer("Haute Media Group", linkedin_url="https://www.linkedin.com/company/haute-living/",
                             target=True, path=p, check=False)
    assert not r["created"] and r["id"] == "haute-living" and r["updated"] == ["linkedin_url"]
    assert rows()["haute-living"]["linkedin_url"].endswith("/haute-living/") and rows()["haute-living"]["target"] is True
    # Same board, different name: the Greenhouse token is the identity, not the host.
    r = add_emp.add_employer("Some Other Name", "https://job-boards.greenhouse.io/pmc", path=p, check=False)
    assert not r["created"] and r["id"] == "pmc"
    r = add_emp.add_employer("Elsewhere", "https://plainco.example/careers/", path=p, check=False)
    assert not r["created"] and r["id"] == "plain-co"

    r = add_emp.add_employer("Foo: Bar #1", "https://foobar.example/jobs", path=p, check=False)
    assert r["created"] and rows()[r["id"]]["name"] == "Foo: Bar #1"
    ids = [x["id"] for x in yaml.safe_load(open(p))["employers"]]
    assert len(ids) == len(set(ids)) == n0 + 5
    text = open(p).read()
    assert text.startswith("# Employers the radar watches") and "target_match: 'eater|thrillist'" in text

    with pytest.raises(ValueError):
        add_emp.add_employer("Bad", "ftp://nope", path=p, check=False)
    with pytest.raises(ValueError):
        add_emp.add_employer("Bad", linkedin_url="https://linkedin.com/in/someone", path=p, check=False)
    with pytest.raises(ValueError):
        add_emp.add_employer("x" * 81, path=p, check=False)


SAMPLE_BODY = ("### Company name\n\nWorld Red Eye\n\n### Website or careers page\n\nhttps://worldredeye.com\n\n"
               "### LinkedIn page\n\n_No response_\n\n### Your list\n\n- [x] This is one of my picks (give it a ★)")


def test_parse_issue_form():
    assert hreq.parse_issue_form(SAMPLE_BODY) == {
        "company name": "World Red Eye", "website or careers page": "https://worldredeye.com",
        "linkedin page": "", "your list": True}
    assert hreq.parse_issue_form("") == {} and hreq.parse_issue_form(None) == {}
    unticked = SAMPLE_BODY.replace("- [x]", "- [ ]").replace("\n", "\r\n")
    assert hreq.parse_issue_form(unticked)["your list"] is False
    # The template's labels are the parser's keys.
    tpl = yaml.safe_load(open(os.path.join(ROOT, ".github", "ISSUE_TEMPLATE", "add-company.yml")))
    assert {b["id"]: b["attributes"]["label"] for b in tpl["body"] if b["type"] != "markdown"} == hreq.LABELS


def test_handle_request(tmp_path):
    p = _employers_copy(tmp_path)
    body = SAMPLE_BODY.replace("World Red Eye", "Acme Weekly").replace("worldredeye.com", "acmeweekly.example")

    r = hreq.handle({"title": "Add company: Acme Weekly", "body": body, "user": "stranger", "labels": "add-company"},
                    CFG, path=p, check=False)
    assert r["kind"] == "declined" and not r["changed"] and not r["dispatch"]
    assert r["close_reason"] == "not planned" and "allow-list" in r["comment"]
    assert "acme-weekly" not in open(p).read()

    r = hreq.handle({"title": "Refresh now", "body": "", "user": "itzkkk", "labels": "refresh"}, CFG, path=p)
    assert r["kind"] == "refresh" and r["dispatch"] and not r["changed"] and r["close_reason"] == "completed"
    assert "5 minutes" in r["comment"] and "itzkkk.github.io/ari-job-radar" in r["comment"]

    r = hreq.handle({"title": "Add company: Acme Weekly", "body": body, "user": "ITZKKK", "labels": ""}, CFG, path=p, check=False)
    assert r["kind"] == "add-company" and r["changed"] and r["dispatch"] and r["close_reason"] == "completed"
    row = {x["id"]: x for x in yaml.safe_load(open(p))["employers"]}["acme-weekly"]
    assert row["target"] is True and row["careers_url"] == "https://acmeweekly.example"

    r = hreq.handle({"title": "Add company: Acme Weekly", "body": body, "user": "ITZKKK", "labels": ""}, CFG, path=p, check=False)
    assert r["kind"] == "add-company" and not r["changed"] and not r["dispatch"] and "already on the list" in r["comment"]

    assert hreq.handle({"title": "Hello?", "body": "hi", "user": "ITZKKK", "labels": ""}, CFG, path=p)["kind"] == "ignore"
    assert hreq.detect_kind("something odd", "add-company") == "add-company"      # label as fallback


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
