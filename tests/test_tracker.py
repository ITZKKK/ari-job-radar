"""
Tests that need no network.

    python -m pytest -q            (from the project folder)

Most title cases are real postings read from employers' boards on 19 Oct 2026,
so the role filter is tested against what the boards actually say.
"""

import datetime as dt
import json
import os
import re
import shutil
import sys
import time

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import add_employer as add_emp  # noqa: E402
from src import adapters, crawl, inbox, notify, store  # noqa: E402
from src.adapters import RawPosting  # noqa: E402
from src.classify import (Rules, classify, employment, location_bucket, topics, visible,  # noqa: E402
                          work_mode, years_required)

CFG = yaml.safe_load(open(os.path.join(ROOT, "config.yaml")))
RULES = Rules(CFG)
EMPLOYERS_YAML = os.path.join(ROOT, "employers.yaml")

# Wednesday 21 Oct 2026, 7:21am in New York: the morning run.
MORNING = dt.datetime(2026, 10, 21, 11, 21, tzinfo=dt.timezone.utc)

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
    # Ari has 3 years: up to 3 is a good fit, 4-5 a stretch, 6+ hidden.
    ("Local Investigations Fellow", "3+ years of experience", "early"),
    ("Reporter", "Up to three years of experience in a newsroom", "early"),
    ("Staff Writer", "Minimum of 4 years of experience", "stretch"),
    ("Staff Writer", "5+ years of experience", "stretch"),
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
    assert radar["inbox"]["url"] == "https://ntfy.sh/" + CFG["inbox"]["ntfy_topic"] and "requests" not in radar
    e = {x["id"]: x for x in radar["employers"]}["the-drum"]
    assert e["aliases"] and "inbox_id" in e        # the page matches her own entries on these
    assert radar["totals"]["open_all"] == 3 and radar["totals"]["open_shown"] == 2
    assert radar["totals"]["by_location_all"] == {"NYC": 2, "MIAMI": 1}
    assert radar["totals"]["open_all"] >= radar["totals"]["open_shown"]
    assert [loc["id"] for loc in radar["locations"]] == ["MIAMI", "REMOTE"]
    drum = [e for e in radar["employers"] if e["id"] == "the-drum"][0]
    assert "linkedin_url" in drum and drum["open_relevant"] == 2   # Reporter + Newsletter Writer, not Staff Writer


# ---------------------------------------------------------------------------
# The dashboard's inbox (ntfy.sh notes signed with the passcode)
# ---------------------------------------------------------------------------

class TextResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class InboxClient:
    def __init__(self, lines):
        self.lines, self.calls = lines, []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        return TextResp("\n".join(self.lines))


def _note(passcode, **kw):
    n = {"v": 1, "kind": "add-company", "id": "n1", "at": "2026-10-20T12:00:00Z", "company": "", "website": "",
         "careers": "", "linkedin": "", "target": False, "note": "", **kw}
    n["sig"] = inbox.sign(n, passcode)
    return n


def _env(n, nid, t):
    return json.dumps({"id": nid, "time": t, "event": "message", "topic": "t", "message": json.dumps(n)})


def test_inbox_signature():
    # Pins the exact canonical form site/index.html signs; change both or neither.
    n = {"v": 1, "kind": "add-company", "id": "abc123", "at": "2026-10-20T12:00:00.000Z", "company": "World  Red Eye",
         "website": "https://worldredeye.com", "careers": "", "linkedin": "https://www.linkedin.com/company/world-red-eye/",
         "target": True, "note": ""}
    assert inbox.canonical(n) == ("1\nadd-company\nabc123\n2026-10-20T12:00:00.000Z\nWorld Red Eye\n"
                                  "https://worldredeye.com\n\nhttps://www.linkedin.com/company/world-red-eye/\n1")
    sig = inbox.sign(n, "walnut-quartz")
    assert len(sig) == 64 and inbox.verify({**n, "sig": sig.upper()}, "walnut-quartz")
    assert not inbox.verify({**n, "sig": sig}, "other")
    assert not inbox.verify({**n, "company": "Other", "sig": sig}, "walnut-quartz")
    assert not inbox.verify(n, "walnut-quartz") and not inbox.verify({**n, "sig": sig}, "")


def test_inbox_process(tmp_path):
    hand = yaml.safe_load(open(EMPLOYERS_YAML))["employers"]
    p = str(tmp_path / "data" / "inbox_employers.yaml")          # does not exist yet: created on first add
    cfg = {**CFG, "inbox": {"ntfy_topic": "t-abc"}}
    t0 = int(time.time()) - 100
    good = _note("pw", id="n1", company="Acme Weekly", careers="https://acme.example/jobs",
                 linkedin="https://www.linkedin.com/company/acme-weekly/", target=True)
    forged = _note("wrong", id="n2", company="Evil Co", website="https://evil.example")
    known = _note("pw", id="n3", company="World Red Eye", linkedin="https://www.linkedin.com/company/world-red-eye/")
    refresh = _note("pw", id="n4", kind="refresh")
    stale = _note("pw", id="n5", company="Old News")
    badurl = _note("pw", id="n6", company="Typo Co", careers="worldredeye.com/jobs")
    lines = [_env(stale, "x0", t0 - 48 * 3600), _env(good, "x1", t0),
             json.dumps({"id": "k", "time": t0, "event": "keepalive", "topic": "t"}),
             _env(forged, "x2", t0 + 1), _env(known, "x3", t0 + 2), _env(refresh, "x4", t0 + 3),
             _env(badurl, "x5", t0 + 4), "not json at all"]
    quiet = lambda *a: None  # noqa: E731
    state = store.empty()
    client = InboxClient(lines)
    c = inbox.process(state, client, cfg, "pw", path=p, known=hand, check=False, log=quiet)
    assert c == {"received": 6, "added": 1, "updated": 1, "refresh": 1, "rejected": 2, "failed": 0, "error": ""}
    assert client.calls == [("https://ntfy.sh/t-abc/json", {"poll": "1", "since": "12h"})]
    rows = {e["id"]: e for e in yaml.safe_load(open(p))["employers"]}
    assert set(rows) == {"acme-weekly"}                            # the hand file is never written
    acme = rows["acme-weekly"]
    assert acme["inbox_id"] == "n1" and acme["target"] is True and acme["careers_url"] == "https://acme.example/jobs"
    assert acme["linkedin_url"].endswith("/acme-weekly/") and "note" not in acme
    assert yaml.safe_load(open(EMPLOYERS_YAML)) == {"employers": hand}
    # Verified notes are done; refused ones are remembered with the reason, never as done.
    assert set(state["inbox"]["done"]) == {"n1", "n3", "n4"} and state["inbox"]["since"] == str(t0 + 4)
    assert state["inbox"]["rejected"]["n2"]["why"] == "signature" and "web address" in state["inbox"]["rejected"]["n6"]["why"]
    pub = inbox.public(state)
    assert set(pub["done"]) == {"n1", "n3", "n4"} and pub["rejected"] == {"n2": "signature", "n6": state["inbox"]["rejected"]["n6"]["why"]}
    # A replay of the same notes changes nothing, and the next read starts where this one ended.
    client2 = InboxClient(lines)
    c2 = inbox.process(state, client2, cfg, "pw", path=p, known=hand, check=False, log=quiet)
    assert (c2["added"], c2["updated"], c2["refresh"]) == (0, 0, 0) and client2.calls[0][1]["since"] == str(t0 + 4)
    # The same note, properly signed this time: accepted, and no longer "refused".
    fixed = _note("pw", id="n2", company="Evil Co", website="https://evil.example")
    inbox.process(state, InboxClient([_env(fixed, "x9", t0 + 5)]), cfg, "pw", path=p, known=hand, check=False, log=quiet)
    assert "n2" in state["inbox"]["done"] and "n2" not in state["inbox"]["rejected"]
    # No topic, or no passcode: silently nothing. A dead server: an error line, never an exception.
    assert inbox.process(store.empty(), InboxClient(lines), {**CFG, "inbox": {}}, "pw")["received"] == 0
    assert inbox.process(store.empty(), InboxClient(lines), cfg, "")["received"] == 0

    class Dead:
        def get(self, *a, **k):
            raise OSError("boom")
    assert inbox.process(store.empty(), Dead(), cfg, "pw", log=quiet)["error"].startswith("OSError")
    # Our own failure (an unreadable file) is counted and left for the next crawl, not raised.
    (tmp_path / "broken.yaml").write_text("employers: [\n")
    c3 = inbox.process(store.empty(), InboxClient([_env(good, "x1", t0)]), cfg, "pw", path=str(tmp_path / "broken.yaml"),
                       known=hand, check=False, log=quiet)
    assert c3["failed"] == 1 and c3["added"] == 0


def test_load_employers_merges_the_inbox_file(tmp_path, monkeypatch):
    inbox_file = tmp_path / "inbox_employers.yaml"
    inbox_file.write_text(add_emp.INBOX_HEADER + '\n  - id: fresh-co\n    name: "Fresh Co"\n    ats: none\n'
                          '\n  - id: world-red-eye\n    name: "Impostor"\n    ats: none\n')
    monkeypatch.setattr(inbox, "INBOX_EMPLOYERS", str(inbox_file))
    got = crawl.load_employers()
    ids = [e["id"] for e in got]
    assert "fresh-co" in ids and ids.count("world-red-eye") == 1
    assert next(e for e in got if e["id"] == "world-red-eye")["name"] == "World Red Eye"   # the hand row wins


def test_add_employer_inbox_id(tmp_path):
    p = _employers_copy(tmp_path)
    rows = lambda: {r["id"]: r for r in yaml.safe_load(open(p))["employers"]}  # noqa: E731
    r = add_emp.add_employer("Fresh Co", "https://fresh.example/jobs", inbox_id="m1", path=p, check=False)
    assert r["created"] and rows()["fresh-co"]["inbox_id"] == "m1"
    r = add_emp.add_employer("Fresh Co", inbox_id="m2", path=p, check=False)
    assert not r["created"] and r["updated"] == ["inbox_id"] and rows()["fresh-co"]["inbox_id"] == "m2"
    assert r["message"] == "Fresh Co is already on the list."     # the id is bookkeeping, not news


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
    assert r["Staff Writer"]["fit"] == "early" and r["Staff Writer"]["work_mode"] == "remote"     # 3 years: fits
    assert r["Editorial Assistant"]["fit"] == "early" and r["Editorial Assistant"]["work_mode"] == "unknown"
    # Kind of job, beats and Spanish are filled from the title / company / employer row.
    assert r["Staff Writer"]["employment"] == "unknown" and r["Staff Writer"]["topics"] == []
    assert r["Staff Writer"]["spanish"] is False
    wre = [e for e in json.load(open(out))["employers"] if e["id"] == "world-red-eye"][0]
    assert wre["linkedin_url"].startswith("https://www.linkedin.com/company/")


def test_export_drops_switched_off_aggregators(tmp_path, monkeypatch):
    """JSearch is off in config until its key is subscribed; its old failure in
    state must not show as 'failing' on Ari's Stats tab."""
    monkeypatch.setenv("JSEARCH_RAPIDAPI_KEY", "k")
    st = store.empty()
    st["sources"] = {"jsearch": {"name": "JSearch (Google for Jobs)", "kind": "aggregator", "ok": False, "error": "403"},
                     "remotive": {"name": "Remotive (remote jobs)", "kind": "aggregator", "ok": True, "error": ""}}
    out = str(tmp_path / "radar.json")
    cfg = json.loads(json.dumps(CFG))
    cfg["aggregators"]["jsearch"]["enabled"] = False
    crawl.export(st, cfg, [], [], out)
    assert [a["id"] for a in json.load(open(out))["aggregators"]] == ["remotive"]
    cfg["aggregators"]["jsearch"]["enabled"] = True
    crawl.export(st, cfg, [], [], out)
    assert sorted(a["id"] for a in json.load(open(out))["aggregators"]) == ["jsearch", "remotive"]


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
    subject, html = notify.build(posts, [], [], CFG, labels, fams, now_utc=MORNING)
    assert subject == "[Job Radar] Wednesday: 4 new jobs — 3 in Miami, 1 remote"
    # ★ first; then Miami before remote (remote in its own section); then a
    # good fit before "experience not stated".
    assert (html.index("Editorial Coordinator") < html.index("Staff Writer")
            < html.index("Copywriter") < html.index("Fact Checker"))
    assert html.index(">In Miami</h3>") < html.index(">Remote</h3>") < html.index("Fact Checker")
    assert "You said you’d rather be in Miami, so these come after." in html
    assert "★</span> <b style=" in html and "The Drum</b>" in html
    assert "Good fit" in html and "Early career" not in html and "Entry level" not in html
    assert "Remote or New York" in html
    assert "Here’s what turned up since your last summary." in html
    assert "sends you this first thing each morning" in html


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
    st["postings"]["parttime"] = {"pid": "parttime", "title": "Part-Time Writer", "location": "MIAMI",
                                  "work_mode": "onsite", "employment": "part_time", **base}
    st["postings"]["fulltime"] = {"pid": "fulltime", "title": "Writer", "location": "MIAMI",
                                  "work_mode": "onsite", "employment": "full_time", **base}
    assert sorted(p["pid"] for p in notify.pending(st, CFG)) == ["fulltime", "miami", "nyc_remote", "old"]
    subject, body = notify.welcome(st, CFG)
    assert "ready (4 jobs open now)" in subject
    assert "emails you one summary first thing each morning when something <b>new</b> has come in" in body


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


# ---------------------------------------------------------------------------
# Ariella's answers (21 Oct 2026): full-time only, PR agencies, Spanish, beats,
# Miami first, one summary each morning
# ---------------------------------------------------------------------------

def test_every_regex_in_config_compiles():
    found = []

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and (key in ("patterns", "exclude", "match") or key.endswith("markers")
                                        or key in ("part_time", "freelance", "contract")):
            found.append(node)
    walk({k: CFG[k] for k in ("roles", "seniority", "fit", "employment", "topics", "languages")})
    assert len(found) > 80
    for rx in found:
        re.compile(rx, re.I)


@pytest.mark.parametrize("place,miami", [
    ("Weston, MA", False), ("Homestead, PA", False), ("Edgewater, NJ", False),
    ("Kendall Square, Cambridge, MA", False),
    ("Weston, FL", True), ("Pompano Beach, FL", True), ("Hallandale Beach, Florida", True),
    ("Weston, Florida, United States", True), ("Kendall, FL", True), ("North Miami Beach", True),
])
def test_south_florida_places(place, miami):
    assert (location_bucket(place, RULES) == "MIAMI") is miami, location_bucket(place, RULES)


@pytest.mark.parametrize("hint,kind", [
    ("Full-time", "full_time"), ("FULLTIME", "full_time"), ("full_time", "full_time"), ("FullTime", "full_time"),
    ("Permanent", "full_time"), ("fulltime_permanent", "full_time"),
    ("Part-time", "part_time"), ("PARTTIME", "part_time"), ("part_time", "part_time"),
    ("Contract", "contract"), ("CONTRACTOR", "contract"), ("Temporary", "contract"), ("Fixed term", "contract"),
    ("Temporary Full Time", "contract"), ("fulltime_fixed_term", "contract"),
    ("Freelance", "freelance"), ("Intern", "intern"), ("Internship", "intern"),
    ("Other", "unknown"), (None, "unknown"),
])
def test_employment_from_board_field(hint, kind):
    assert employment("Staff Writer", "", hint, RULES) == kind


@pytest.mark.parametrize("title,desc,hint,kind", [
    ("Freelance Writer", "", None, "freelance"),
    ("Part-time Social Media Coordinator", "", None, "part_time"),
    ("Part Time-Temp Reporter, Axios Local", "", None, "part_time"),
    ("Temporary Social Media Producer", "", None, "contract"),
    ("Audience Development Coordinator (Contract)", "", None, "contract"),
    ("Contributing Editor", "", None, "freelance"),
    ("Staff Writer", "This is a part-time position, 20 hours a week.", None, "part_time"),
    ("Staff Writer", "This is a contract role through March.", None, "contract"),
    ("Staff Writer", "You'll manage contracts with our freelance photographers.", None, "unknown"),
    ("Staff Writer", "", None, "unknown"),
    ("Freelance Writer", "", "Full-time", "freelance"),            # "Full time" is hours, not permanence: the title wins
    ("News Assignment Editor and Producer-Temp", "", "Full time", "contract"),   # Workday says Full time for temp jobs
    ("Staff Writer", "", "Full time", "full_time"),
    ("Staff Writer", "", "Part-time", "part_time"),                # a non-full-time board field still wins
    ("Freelance Writer", "", "Part-time", "part_time"),
    # A fellowship has an end date but is a full-time job: kept.
    ("Editorial Fellow", "This is a temporary position for 12 months.", None, "full_time"),
    ("Reporting Fellowship", "", "Temporary Full Time", "full_time"),
    ("Reporting Fellowship", "", "Part-time", "part_time"),
])
def test_employment_from_title_and_description(title, desc, hint, kind):
    assert employment(title, desc, hint, RULES) == kind


def test_not_full_time_is_relevant_but_hidden():
    emp_cfg = CFG["employment"]
    v = classify("Part Time-Temp Reporter, Axios Local", "Remote", "", RULES)
    assert v.relevant and v.employment == "part_time"
    assert visible(v.location, v.work_mode, CFG["locations"], v.remote_offered)
    assert not visible(v.location, v.work_mode, CFG["locations"], v.remote_offered,
                       employment=v.employment, emp_cfg=emp_cfg)
    # Lever's commitment field: "Contract".
    v = classify("Staff Writer", "Miami, FL", "", RULES, employment_hint="Contract")
    assert v.relevant and v.employment == "contract"
    assert not visible(v.location, v.work_mode, CFG["locations"], employment=v.employment, emp_cfg=emp_cfg)
    for kind in ("full_time", "unknown"):
        assert visible("MIAMI", "onsite", CFG["locations"], employment=kind, emp_cfg=emp_cfg)
    assert visible("MIAMI", "onsite", CFG["locations"], employment="part_time")   # no emp_cfg: not this rule
    assert not visible("NYC", "onsite", CFG["locations"], employment="full_time", emp_cfg=emp_cfg)


def test_fellowship_kept_and_flagged():
    v = classify("Editorial Fellow", "Miami, FL", "This is a temporary position. 3+ years of experience.", RULES)
    assert v.relevant and v.employment == "full_time" and "Fellowship" in v.flags
    assert v.fit == "early" and not any("yrs asked" in f for f in v.flags)      # 3 years: no warning
    v = classify("Reporter", "Miami, FL", "4+ years of experience", RULES)
    assert v.fit == "stretch" and "4+ yrs asked" in v.flags and "Fellowship" not in v.flags
    v = classify("Editorial Fellowship", "Miami, FL", "", RULES, employment_hint="Intern")
    assert v.employment == "intern" and "Fellowship" not in v.flags


def test_new_role_tiers():
    tiers = {f["id"]: f["tier"] for f in CFG["roles"]["families"]}
    assert tiers == {"editorial": "core", "content": "core", "social": "also",
                     "pr_comms": "core", "marketing_copy": "core"}
    for title, fam in [("Communications Officer", "pr_comms"), ("Coordinator, Corporate Communications", "pr_comms"),
                       ("Publicity Assistant", "pr_comms"), ("Press Secretary", "pr_comms"),
                       ("Marketing Manager", "marketing_copy"), ("Hospitality Marketing Coordinator", "marketing_copy"),
                       ("Promotions Coordinator", "marketing_copy"), ("Restaurant Critic", "editorial"),
                       ("Digital Content Producer", "content"), ("Branded Content Strategist", "content")]:
        v = classify(title, "Miami, FL", "", RULES)
        assert v.relevant and v.family == fam and v.tier == "core", (title, v.family, v.reason)
    for title in ("Graphic Designer", "Product Marketing Manager", "Marketing Operations Coordinator",
                  "Business Development Associate", "Photographer", "Art Director"):
        assert not classify(title, "Miami, FL", "", RULES).relevant, title


def test_pr_agency_titles_only_at_pr_employers():
    agency = {"id": "kreps", "name": "Kreps PR & Marketing", "industry": "pr", "group": "PR agency"}
    other = {"id": "x", "name": "Some Media Co", "group": "Digital media"}
    for title in ("Account Coordinator", "Account Executive", "Junior Account Executive", "Client Associate"):
        v = classify(title, "Miami, FL", "", RULES, employer=agency)
        assert v.relevant and v.family == "pr_comms" and v.tier == "core", (title, v.reason)
        assert not classify(title, "Miami, FL", "", RULES, employer=other).relevant, title
        assert not classify(title, "Miami, FL", "", RULES).relevant, title
    # A sales job at an agency is still a sales job.
    assert not classify("Account Executive, Advertising Sales", "Miami, FL", "", RULES, employer=agency).relevant
    assert not classify("Account Executive, Fashion", "New York, NY", "", RULES).relevant


@pytest.mark.parametrize("title,fam", [
    ("Redactora", "editorial"), ("Periodista Digital", "editorial"), ("Reportero, Telemundo 51", "editorial"),
    ("Editora de Contenido", "editorial"), ("Productora de Contenido", "editorial"),
    ("Escritor Creativo", "editorial"), ("Coordinador de Comunicaciones", "pr_comms"),
    ("Relaciones Públicas", "pr_comms"), ("Especialista de Redes Sociales", "social"),
    ("Gerente de Mercadeo", "marketing_copy"),
])
def test_spanish_titles_match_the_families(title, fam):
    v = classify(title, "Doral, FL", "", RULES)
    assert v.relevant and v.family == fam, (title, v.family, v.reason)
    assert v.location == "MIAMI"


def test_topics():
    assert topics("Food & Travel Writer", "", "", RULES) == ["food", "travel"]
    assert topics("Staff Writer", "The Infatuation", "", RULES) == ["food"]            # an aggregator's company name
    assert topics("Theater Critic", "", "", RULES) == ["culture"]                      # not "eater"
    assert topics("Lifestyle Editor", "", "", RULES) == ["lifestyle"]                  # not "style"
    assert topics("Luxury Hotels Writer", "", "", RULES) == ["hospitality", "lifestyle"]  # not fashion
    # The description needs three different words for the same beat; a
    # plural is the same word as its singular.
    assert RULES.topic_desc_words == 3
    assert topics("Staff Writer", "", "Some travel required. Travel budget provided.", RULES) == []
    assert topics("Staff Writer", "", "Cover restaurants, talk to chefs, review the dining scene.", RULES) == ["food"]
    assert topics("Business Editor", "", "Join our culture of curiosity in news and entertainment.", RULES) == []
    assert topics("Staff Writer", "", "Work with our chef and our chefs on food.", RULES) == []
    assert topics("Staff Writer", "", "Chefs, chef de cuisine, food and foodies.", RULES) == ["food"]  # chef, cuisine, food, foodie


def test_topics_and_spanish_from_the_employer_row():
    # The row's `group` describes the employer, not the job: no beats from it.
    cruise = {"id": "rc", "name": "Royal Caribbean Group", "group": "Cruise line (travel & hospitality), Miami HQ"}
    v = classify("Social Media Coordinator", "Miami, FL", "", RULES, employer=cruise, company=cruise["name"])
    assert v.topics == [] and not v.spanish
    v = classify("Social Media Coordinator", "Miami, FL", "", RULES, employer={**cruise, "topics": ["travel", "hospitality"]},
                 company=cruise["name"])
    assert v.topics == ["hospitality", "travel"]                     # config order
    # A parent's display name lists its brands; they don't make every job a food job.
    people = {"id": "people-inc", "name": "People Inc. (People, Food & Wine, Travel + Leisure…)", "group": "Magazines"}
    v = classify("Staff Writer", "New York, NY", "", RULES, employer=people, company=people["name"])
    assert v.topics == []
    v = classify("Travel + Leisure: Editor", "New York, NY", "", RULES, employer=people, company=people["name"])
    assert v.topics == ["travel"]
    pmc = {"id": "pmc", "name": "Penske Media — Eater, Thrillist, Robb Report, WWD, Variety",
           "group": "Magazines & digital media (bought Eater and Thrillist from Vox Media, June 2026)"}
    v = classify("Variety: Staff Writer", "Los Angeles", "", RULES, employer=pmc, company=pmc["name"])
    assert v.topics == []
    v = classify("Eater: Restaurant Editor", "Miami, FL", "", RULES, employer=pmc, company=pmc["name"])
    assert v.topics == ["food"]
    v = classify("Staff Writer", "Miami, FL", "", RULES, employer=pmc, company="Eater")   # aggregator find at Eater
    assert v.topics == ["food"]


def _row(emp_id):
    return {r["id"]: r for r in yaml.safe_load(open(EMPLOYERS_YAML))["employers"]}[emp_id]


@pytest.mark.parametrize("emp_id,title,beats_", [
    ("5wpr", "Account Executive, Technology", []),            # group says "beauty & food PR"
    ("the-infatuation", "Staff Writer", ["food"]),
    ("pmc", "Billboard: Staff Writer, Latin/Pop", []),
    ("chewy", "Digital Content Creator", []), ("max-borges", "Account Coordinator", []),
    ("the-real-deal", "Reporter", []), ("hearst", "Associate Editor", []), ("people-inc", "Staff Writer", []),
    ("nclh", "Communications Manager", ["hospitality", "travel"]),
    ("seminole-hard-rock", "Marketing Specialist", ["hospitality", "travel"]),
    ("sony-music", "Social Media Coordinator", ["culture"]),
    ("sony-pictures", "Marketing Coordinator", []),           # no list on the row: the title decides
])
def test_beats_only_from_the_rows_own_list(emp_id, title, beats_):
    e = _row(emp_id)
    assert classify(title, "Miami, FL", "", RULES, employer=e, company=e["name"]).topics == beats_


def test_every_employer_topic_is_a_beat():
    ids = {t["id"] for t in CFG["topics"]}
    rows = yaml.safe_load(open(EMPLOYERS_YAML))["employers"]
    assert all(set(r.get("topics") or []) <= ids for r in rows)
    assert [r["id"] for r in rows if r.get("spanish")] == ["televisaunivision"]


def test_spanish_flag():
    tvu = {"id": "televisaunivision", "name": "TelevisaUnivision", "group": "Spanish-language TV & digital"}
    nbcu = {"id": "nbcuniversal", "name": "NBCUniversal (Telemundo, NBC 6 Miami, NBC News)", "group": "TV & digital news"}
    assert classify("Bilingual Reporter", "Miami, FL", "", RULES).spanish
    assert classify("Writer", "Miami, FL", "", RULES, employer=tvu, company=tvu["name"]).spanish
    assert classify("Writer", "Miami, FL", "", RULES, company="TelevisaUnivision").spanish
    assert classify("Content Producer", "Miami, FL", "", RULES, company="Telemundo").spanish        # aggregator find
    assert classify("Writer", "Miami, FL", "We need someone fluent in English and Spanish.", RULES).spanish
    assert not classify("Writer", "Miami, FL", "", RULES, employer=nbcu, company=nbcu["name"]).spanish
    assert classify("News Producer, Telemundo News Service Miami", "Miami, FL", "", RULES,
                    employer=nbcu, company=nbcu["name"]).spanish
    assert not classify("Writer", "Miami, FL", "", RULES).spanish


def test_fit_labels_follow_the_numbers():
    from src.classify import fit_labels
    assert fit_labels(CFG) == [{"id": "early", "label": "Good fit (up to 3 years)"},
                               {"id": "open", "label": "Experience not stated"},
                               {"id": "stretch", "label": "Stretch (4–5 years, or a manager role)"}]
    other = {**CFG, "fit": {"early_max_years": 1}, "seniority": {"max_years_required": None}}
    assert [f["label"] for f in fit_labels(other)][::2] == ["Good fit (up to 1 year)", "Stretch (2+ years, or a manager role)"]


# --- employment hints straight from the boards' payloads -------------------------

def test_adapters_pass_the_employment_field(monkeypatch):
    from src import aggregators
    lever = [{"id": "1", "text": "Staff Writer", "hostedUrl": "u", "workplaceType": "onsite",
              "categories": {"commitment": "Temporary Full Time", "location": "Miami, FL"}}]
    assert adapters.fetch_lever(FakeClient(lever), {"id": "bdg", "name": "BDG", "ats_token": "BDG"})[0].extra[
        "employment"] == "Temporary Full Time"
    ashby = {"jobs": [{"id": "a", "title": "Writer", "location": "Miami", "employmentType": "PartTime"}]}
    assert adapters.fetch_ashby(FakeClient(ashby), {"id": "a", "name": "A", "ats_token": "a"})[0].extra[
        "employment"] == "PartTime"
    sr = {"totalFound": 1, "content": [{"id": "s1", "name": "Writer", "location": {"city": "Miami"},
                                         "typeOfEmployment": {"id": "permanent", "label": "Full-time"}}]}
    assert adapters.fetch_smartrecruiters(FakeClient(sr), {"id": "n", "name": "N", "ats_token": "N"})[0].extra[
        "employment"] == "Full-time"
    wk = {"jobs": [{"title": "Reporter", "shortcode": "X1", "city": "Miami", "employment_type": "Contract"}]}
    assert adapters.fetch_workable(FakeClient(wk), {"id": "d", "name": "D", "ats_token": "d"})[0].extra[
        "employment"] == "Contract"
    ukg = {"totalCount": 2, "opportunities": [{"Id": "1", "Title": "Merchandiser - PT", "FullTime": False},
                                              {"Id": "2", "Title": "Visual Journalist", "FullTime": True}]}
    out = adapters.fetch_ukg(FakeClient(ukg), {"id": "mh", "name": "MH", "ats_token": "https://x/JobBoard/g"})
    assert [p.extra["employment"] for p in out] == ["Part-time", "Full-time"]
    breezy = [{"id": "b", "name": "Writer", "location": {"name": "Miami, FL"}, "type": {"id": "contract", "name": "Contract"}}]
    assert adapters.fetch_breezy(FakeClient(breezy), {"id": "b", "name": "B", "ats_token": "b"})[0].extra[
        "employment"] == "Contract"
    rec = {"offers": [{"id": 1, "title": "Writer", "location": "Miami", "employment_type_code": "fulltime_fixed_term"}]}
    assert adapters.fetch_recruitee(FakeClient(rec), {"id": "r", "name": "R", "ats_token": "r"})[0].extra[
        "employment"] == "fulltime_fixed_term"
    gh = {"jobs": [{"id": 1, "title": "Writer", "location": {"name": "Miami, FL"}, "absolute_url": "u"}]}
    assert "employment" not in adapters.fetch_greenhouse(FakeClient(gh), {"id": "g", "name": "G", "ats_token": "g"})[0].extra

    monkeypatch.setattr(aggregators.time, "sleep", lambda s: None)
    rem = {"jobs": [{"id": 9, "title": "Content Writer", "company_name": "Acme", "job_type": "freelance"}]}
    assert aggregators.fetch_remotive(FakeClient(rem), {"categories": ["writing"]})[0].extra["employment"] == "freelance"
    monkeypatch.setenv("ADZUNA_APP_ID", "x"); monkeypatch.setenv("ADZUNA_APP_KEY", "y")
    adz = {"results": [{"id": 1, "title": "Writer", "contract_type": "contract", "contract_time": "full_time"},
                       {"id": 2, "title": "Editor", "contract_time": "part_time"}]}
    out = aggregators.fetch_adzuna(FakeClient(adz), {"where": ["Miami, FL"], "what_or": "editor", "pages": 1})
    assert [p.extra["employment"] for p in out] == ["contract", "part_time"]
    monkeypatch.setenv("JSEARCH_RAPIDAPI_KEY", "k")
    js = {"data": [{"job_id": "j", "job_title": "Writer", "employer_name": "E", "job_employment_type": "PARTTIME"}]}
    assert aggregators.fetch_jsearch(FakeClient(js), {"queries": ["q"]})[0].extra["employment"] == "PARTTIME"

    rss = b"""<?xml version="1.0"?><rss><channel><item><title>Acme: Copywriter</title><link>l</link>
      <guid>g</guid><type>Contract</type></item></channel></rss>"""

    class RC:
        def get(self, url, **kw):
            r = FakeResp({}); r.content = rss; return r
    feed = {"id": "wwr", "name": "WWR", "url": "u", "title_format": "company: title", "default_location": "Remote"}
    assert aggregators.fetch_rss(RC(), feed)[0].extra["employment"] == "Contract"


def test_crawl_stores_employment_and_hides_it(tmp_path, monkeypatch):
    state_p, radar_p = str(tmp_path / "state.json"), str(tmp_path / "radar.json")
    monkeypatch.setattr(crawl.aggregators, "enabled", lambda cfg, state=None: [])
    monkeypatch.setattr(crawl.watch, "check", lambda *a, **k: {"watched": 0, "changed": 0, "unwatchable": 0})
    monkeypatch.setattr(crawl.time, "sleep", lambda s: None)
    jobs = [RawPosting("the-drum", "A", "Editorial Coordinator", "The Drum", "Miami, FL", "https://x/A",
                       extra={"employment": "Full-time"}),
            RawPosting("the-drum", "B", "Food Reporter", "The Drum", "Miami, FL", "https://x/B",
                       extra={"employment": "Contract"}),
            RawPosting("the-drum", "C", "Bilingual Writer", "The Drum", "Remote", "https://x/C"),
            RawPosting("the-drum", "D", "Part-time Editor", "The Drum", "New York, NY", "https://x/D")]
    fetchers = {k: (lambda client, emp: list(jobs) if emp["id"] == "the-drum" else []) for k in adapters.ADAPTERS}
    crawl.run(state_path=state_p, radar_path=radar_p, fetchers=fetchers)
    s = {p["job_id"]: p for p in store.load(state_p)["postings"].values()}
    assert s["A"]["employment"] == "full_time" and s["B"]["employment"] == "contract"
    assert s["B"]["topics"] == ["food"] and s["C"]["spanish"] is True and s["A"]["spanish"] is False
    radar = json.load(open(radar_p))
    by = {p["title"]: p for p in radar["postings"]}
    assert set(by) == {"Editorial Coordinator", "Bilingual Writer"}       # contract and part-time hidden
    assert by["Editorial Coordinator"]["employment"] == "full_time" and by["Bilingual Writer"]["employment"] == "unknown"
    assert by["Bilingual Writer"]["spanish"] is True and by["Bilingual Writer"]["topics"] == []
    # Only the Miami contract job counts as "hidden: not full-time"; the New York
    # part-time one is hidden for its place already.
    assert radar["totals"]["open_not_fulltime"] == 1
    assert radar["totals"]["open_shown"] == 2 and radar["totals"]["open_all"] == 4


def test_export_contract_fields(tmp_path):
    out = str(tmp_path / "radar.json")
    crawl.export(store.empty(), CFG, [], [], out)
    r = json.load(open(out))
    assert r["fit_years"] == 3
    assert r["fits"] == [{"id": "early", "label": "Good fit (up to 3 years)"},
                         {"id": "open", "label": "Experience not stated"},
                         {"id": "stretch", "label": "Stretch (4–5 years, or a manager role)"}]
    assert [t["id"] for t in r["topics"]] == ["food", "hospitality", "travel", "fashion", "lifestyle", "culture"]
    assert r["topics"][0] == {"id": "food", "label": "Food & dining"}
    assert r["preferred_location"] == "MIAMI"
    assert r["employment"] == {"show": ["full_time", "unknown"], "labels": {
        "full_time": "Full-time", "part_time": "Part-time", "freelance": "Freelance",
        "contract": "Contract / temporary", "intern": "Internship", "unknown": "Not stated"}}
    assert r["email"] == {"digest": "daily", "after_hour": 6, "timezone": "America/New_York"}
    assert r["totals"]["open_not_fulltime"] == 0 and r["postings"] == []


def test_export_backfills_employment_from_the_title(tmp_path):
    base = {"kind": "employer", "employer_id": "the-drum", "company": "The Drum", "location": "MIAMI",
            "location_raw": "Miami, FL", "status": "open", "relevant": True, "seniority": "mid", "years": None,
            "flags": [], "first_seen": "2026-10-19T10:00:00+00:00"}
    st = store.empty()
    st["postings"] = {"f": {"pid": "f", "title": "Freelance Food Writer", **base},
                      "s": {"pid": "s", "title": "Restaurant Reporter", **base}}
    out = str(tmp_path / "radar.json")
    crawl.export(st, CFG, yaml.safe_load(open(EMPLOYERS_YAML))["employers"], [], out)
    r = json.load(open(out))
    assert [p["title"] for p in r["postings"]] == ["Restaurant Reporter"]
    assert r["postings"][0]["topics"] == ["food"] and r["postings"][0]["employment"] == "unknown"
    assert r["totals"]["open_not_fulltime"] == 1


# --- the morning summary ----------------------------------------------------------

def _at(hh, mm=0, day=21, month=10):
    """A New York wall-clock time on a day in 2026, as UTC."""
    from zoneinfo import ZoneInfo
    return dt.datetime(2026, month, day, hh, mm, tzinfo=ZoneInfo("America/New_York")).astimezone(dt.timezone.utc)


def test_digest_due(monkeypatch):
    monkeypatch.delenv("RADAR_EMAIL_NOW", raising=False)
    st = store.empty()
    assert notify.digest_due(st, CFG, _at(5, 0))[0] is False                  # too early
    assert "too early" in notify.digest_due(st, CFG, _at(5, 59))[1]
    assert notify.digest_due(st, CFG, _at(7, 21)) == (True, "morning summary due (2026-10-21 07:21 local)")
    assert notify.digest_due(st, CFG, _at(6, 21, day=2, month=12))[0] is True  # winter: the same run is at 6:21
    st["last_digest_day"] = "2026-10-21"
    assert notify.digest_due(st, CFG, _at(13, 21)) == (False, "already sent today's morning summary (2026-10-21)")
    assert notify.digest_due(st, CFG, _at(23, 30))[0] is False                 # 3:30 UTC next day, still the 21st here
    assert notify.digest_due(st, CFG, _at(7, 21, day=22))[0] is True
    assert notify.digest_due(st, CFG, _at(13, 21), force=True)[0] is True
    monkeypatch.setenv("RADAR_EMAIL_NOW", "1")
    assert notify.digest_due(st, CFG, _at(3, 0))[0] is True
    assert notify.digest_due(st, CFG, _at(3, 0), force=False)[0] is False
    each = {**CFG, "alerts": {**CFG["alerts"], "digest": "each_run"}}
    monkeypatch.delenv("RADAR_EMAIL_NOW")
    assert notify.digest_due(st, each, _at(3, 0))[0] is True


def test_morning_summary_flow(tmp_path, monkeypatch):
    """Three crawls a day, one email: the first run after 6am sends, later runs
    keep their finds for tomorrow, email_now sends an extra without using up
    the morning, and a morning with nothing new sends nothing."""
    import src.env
    monkeypatch.setattr(src.env, "load_env", lambda *a, **k: None)
    monkeypatch.delenv("RADAR_EMAIL_NOW", raising=False)
    sent = []
    monkeypatch.setattr(notify, "send", lambda subject, body: sent.append(subject) or True)
    p = str(tmp_path / "state.json")
    st = store.empty()
    st["welcomed"], st["runs"] = "2026-10-20T11:21:00+00:00", [{"started_at": "x"}]
    now = store.now_iso()

    def job(pid, title):
        return {"pid": pid, "title": title, "company": "The Drum", "employer_id": "the-drum", "kind": "employer",
                "location": "MIAMI", "location_raw": "Miami, FL", "work_mode": "onsite", "employment": "full_time",
                "relevant": True, "status": "open", "tier": "core", "family": "editorial", "url": "https://x/" + pid,
                "first_seen": now, "flags": []}
    st["postings"] = {"a": job("a", "Reporter")}
    store.save(st, p)

    notify.main([], state_path=p, now_utc=_at(2, 0))             # 2am: too early, nothing sent
    assert sent == [] and "last_digest_day" not in store.load(p)
    notify.main([], state_path=p, now_utc=_at(7, 21))            # the morning run sends
    assert len(sent) == 1 and sent[0].startswith("[Job Radar] Wednesday: 1 new job — 1 in Miami")
    s = store.load(p)
    assert s["last_digest_day"] == "2026-10-21" and s["postings"]["a"]["emailed_at"]

    s["postings"]["b"] = job("b", "Staff Writer")                # found by the 1:20pm crawl
    store.save(s, p)
    notify.main([], state_path=p, now_utc=_at(13, 21))
    assert len(sent) == 1 and not store.load(p)["postings"]["b"].get("emailed_at")

    # --preview renders whatever is waiting, whatever the time, and writes no state.
    previews = []
    monkeypatch.setattr(notify, "_preview", lambda built: previews.append(built[0]))
    before = open(p).read()
    notify.main(["--preview"], state_path=p, now_utc=_at(13, 22))
    assert previews == ["[Job Radar] Wednesday: 1 new job — 1 in Miami"] and open(p).read() == before and len(sent) == 1

    monkeypatch.setenv("RADAR_EMAIL_NOW", "1")                   # "Run workflow" with email_now ticked
    notify.main([], state_path=p, now_utc=_at(2, 0, day=22))
    assert len(sent) == 2 and store.load(p)["last_digest_day"] == "2026-10-21"   # the 22nd's summary still to come
    monkeypatch.delenv("RADAR_EMAIL_NOW")

    notify.main([], state_path=p, now_utc=_at(7, 21, day=22))   # nothing new: no email, the day is done
    assert len(sent) == 2 and store.load(p)["last_digest_day"] == "2026-10-22"


def test_email_lists_beats_spanish_and_years():
    labels = {b["id"]: b["label"] for b in CFG["locations"]["buckets"]}
    fams = {f["id"]: f["label"] for f in CFG["roles"]["families"]}
    base = {"company": "Some Co", "employer_id": None, "tier": "core", "family": "editorial", "flags": [],
            "location_raw": "Miami, FL", "seniority": "mid"}
    posts = [
        {**base, "title": "Remote Writer", "location": "REMOTE", "location_raw": "Remote", "url": "https://x/r",
         "fit": "early", "topics": ["food"]},
        {**base, "title": "Staff Writer", "location": "MIAMI", "url": "https://x/s", "fit": "early", "years": 3},
        {**base, "title": "Food Editor", "location": "MIAMI", "url": "https://x/f", "fit": "stretch", "years": 4,
         "topics": ["food", "culture"], "spanish": True, "flags": ["Fellowship"]},
    ]
    subject, html = notify.build(posts, [], [], CFG, labels, fams, now_utc=MORNING)
    assert subject == "[Job Radar] Wednesday: 3 new jobs — 2 in Miami, 1 remote"
    # Miami first; within Miami a job on her beats comes before one that isn't.
    assert html.index("Food Editor") < html.index("Staff Writer") < html.index("Remote Writer")
    assert "Writing &amp; editing · Asks for 4+ years · Food &amp; dining · Arts &amp; culture · Spanish · Fellowship" in html
    assert "Asks for 3+ years" not in html
    one = notify.build(posts[1:2], [], [], CFG, labels, fams, now_utc=MORNING)
    assert one[0] == "[Job Radar] Wednesday: 1 new job — 1 in Miami"
    assert ">In Miami</h3>" not in one[1]                       # one place: no sections
    # Same beats and fit: a Spanish role comes first.
    pair = [{**posts[1], "title": "Writer A", "url": "https://x/a", "company": "A Co"},
            {**posts[1], "title": "Writer B", "url": "https://x/b", "company": "B Co", "spanish": True}]
    two = notify.build(pair, [], [], CFG, labels, fams, now_utc=MORNING)[1]
    assert two.index("Writer B") < two.index("Writer A")
    rem = notify.build(posts[:1], [], [], CFG, labels, fams, now_utc=MORNING)[0]
    assert rem == "[Job Radar] Wednesday: 1 new job — 1 remote"
    assert notify.build([], [], [], CFG, labels, fams, now_utc=MORNING) is None
    empty = notify.build([], [], [], CFG, labels, fams, now_utc=MORNING, empty_ok=True)
    assert empty[0] == "[Job Radar] Wednesday: nothing new this morning"


# ---------------------------------------------------------------------------
# Review fixes (21 Oct 2026, round 2)
# ---------------------------------------------------------------------------

class WorkdayBoard:
    """A Workday board as the real one answers: the job list gives the
    search's total on the first page only (later pages say 0), and each job
    has its own page with the description, every location and timeType."""

    def __init__(self, jobs, details):
        self.jobs, self.details, self.list_calls, self.detail_calls = jobs, details, 0, []

    def post(self, url, json=None):
        self.list_calls += 1
        off = json["offset"]
        return FakeResp({"total": len(self.jobs) if off == 0 else 0, "jobPostings": self.jobs[off:off + 20]})

    def get(self, url, **kw):
        self.detail_calls.append(url)
        path = url.split("/External", 1)[1]
        if path not in self.details:
            return FakeResp({}, 404)
        return FakeResp({"jobPostingInfo": self.details[path]})


def _wd_job(i, title, loc):
    return {"title": title, "externalPath": f"/job/x/{title.replace(' ', '-')}_R{1000 + i}",
            "locationsText": loc, "postedOn": "Posted Today", "bulletFields": [f"R{1000 + i}"]}


def _wd_board():
    jobs = [_wd_job(0, "Social Media Specialist", "2 Locations"),
            _wd_job(1, "Content Writer", "Plantation, FL"),
            _wd_job(2, "Copywriter", "Plantation, FL")]
    jobs += [_wd_job(i, f"Content Coordinator {i}", "Plantation, FL") for i in range(3, 30)]
    jobs += [_wd_job(i, f"Warehouse Associate {i}", "Ocala, FL") for i in range(30, 51)]
    det = {j["externalPath"]: {"title": j["title"], "location": "Plantation, FL", "additionalLocations": [],
                               "timeType": "Full time", "jobDescription": "<p>Write for our pets.</p>"}
           for j in jobs}
    det[jobs[0]["externalPath"]] = {"title": "Social Media Specialist", "location": "Hollywood, FL",
                                    "additionalLocations": ["Richardson, TX"], "timeType": "Full time",
                                    "remoteType": "Hybrid",
                                    "jobDescription": "<p>You have <b>4+ years of experience</b> in social.</p>"}
    det[jobs[1]["externalPath"]]["timeType"] = "Part time"
    del det[jobs[2]["externalPath"]]                      # this job's page fails: skipped, the board still reads
    return WorkdayBoard(jobs, det)


def test_workday_reads_every_page_and_the_job_page(monkeypatch):
    monkeypatch.setattr(adapters.time, "sleep", lambda s: None)
    board = _wd_board()
    emp = {"id": "chewy", "name": "Chewy", "ats_token": "chewy", "ats_dc": "wd5", "ats_site": "External",
           "queries": [""]}
    out = adapters.fetch_workday(board, emp)
    assert len(out) == 51 and board.list_calls == 3             # 20 + 20 + 11: not stopped after page 2
    assert out[0].extra["detail_url"] == ("https://chewy.wd5.myworkdayjobs.com/wday/cxs/chewy/External"
                                          "/job/x/Social-Media-Specialist_R1000")
    d = adapters.workday_detail(board, out[0].extra["detail_url"])
    assert d == {"description": "You have 4+ years of experience in social.",
                 "location_raw": "Hollywood, FL / Richardson, TX", "employment": "Full time", "workplace": "hybrid"}
    # Each employer words remoteType its own way (iHeartMedia, 21 Oct 2026).
    assert [adapters._workplace(x) for x in ("Remote Position", "In-Office Position", "Hybrid", "On-Site", None)] == [
        "remote", "onsite", "hybrid", "onsite", None]


def test_crawl_enriches_workday_postings(tmp_path, monkeypatch):
    state_p, radar_p = str(tmp_path / "state.json"), str(tmp_path / "radar.json")
    monkeypatch.setattr(crawl.time, "sleep", lambda s: None)
    board = _wd_board()
    crawl.run(only="chewy", state_path=state_p, radar_path=radar_p, client=board)
    s = store.load(state_p)
    by = {p["title"]: p for p in s["postings"].values() if p["source_id"] == "chewy"}
    assert len(by) == 51 and s["sources"]["chewy"]["ok"] and s["sources"]["chewy"]["count_relevant"] == 30
    # At most 25 job pages a run; the "2 Locations" one is among them and lands in Miami.
    assert len(board.detail_calls) == crawl.ENRICH_PER_RUN == 25
    sm = by["Social Media Specialist"]
    assert sm["location"] == "MIAMI" and sm["location_raw"] == "Hollywood, FL / Richardson, TX"
    assert sm["employment"] == "full_time" and sm["work_mode"] == "hybrid"
    assert sm["years"] == 4 and sm["fit"] == "stretch" and "4+ yrs asked" in sm["flags"] and sm["enriched"]
    assert by["Content Writer"]["employment"] == "part_time"           # timeType "Part time": hidden
    assert not by["Copywriter"].get("enriched") and by["Copywriter"]["relevant"]   # its page failed
    assert sum(1 for p in by.values() if p.get("enriched")) == 24

    # Next run: the list again has no descriptions. What the job pages said
    # stays; only the ones not read yet are fetched (the failed one again).
    crawl.run(only="chewy", state_path=state_p, radar_path=radar_p, client=board)
    s = store.load(state_p)
    by = {p["title"]: p for p in s["postings"].values() if p["source_id"] == "chewy"}
    assert len(board.detail_calls) == 25 + 6
    sm = by["Social Media Specialist"]
    assert (sm["years"], sm["fit"], sm["location"], sm["employment"]) == (4, "stretch", "MIAMI", "full_time")
    assert sm["location_raw"] == "Hollywood, FL / Richardson, TX" and "4+ yrs asked" in sm["flags"]
    assert sum(1 for p in by.values() if p.get("enriched")) == 29
    radar = json.load(open(radar_p))
    assert "Content Writer" not in {p["title"] for p in radar["postings"]}
    assert {"Social Media Specialist", "Content Coordinator 3"} <= {p["title"] for p in radar["postings"]}

    # A rule edit since then: the job pages are read again, not trusted.
    for p in s["postings"].values():
        if p.get("enriched"):
            p["enriched_rules"] = "an older rule set"
    store.save(s, state_p)
    crawl.run(only="chewy", state_path=state_p, radar_path=radar_p, client=board)
    assert len(board.detail_calls) == 31 + 25
    emp = {"name": "Chewy"}
    assert crawl.rules_fingerprint(CFG, emp) != crawl.rules_fingerprint({**CFG, "fit": {"early_max_years": 2}}, emp)
    assert crawl.rules_fingerprint(CFG, emp) != crawl.rules_fingerprint(CFG, {**emp, "home": "MIAMI"})


@pytest.mark.parametrize("loc,bucket", [
    ("Plantation - FL", "MIAMI"), ("Remote - USA", "REMOTE"), ("Florida - Miami Office", "MIAMI"),
    ("Remote-Florida", "REMOTE"), ("Toronto, Canada (Remote)", "FOREIGN"), ("Kuala Lumpur, Malaysia", "FOREIGN"),
    ("Toronto, Ontario, Canada", "FOREIGN"), ("Boston, MA", "OTHER_US"), ("Hollywood, CA", "OTHER_US"),
    ("Seminole Hard Rock Hotel & Casino Hollywood", "UNKNOWN"), ("Starwood Hotels - Florida", "FLORIDA"),
    ("United States - Business Locations", "OTHER_US"), ("Austin, TX", "OTHER_US"),
])
def test_location_dashes_and_state_codes(loc, bucket):
    assert location_bucket(loc, RULES) == bucket


@pytest.mark.parametrize("emp_id,loc,bucket", [
    ("seminole-hard-rock", "Seminole Hard Rock Hotel & Casino Hollywood", "MIAMI"),
    ("seminole-hard-rock", "Support Services Headquarters Building", "MIAMI"),
    ("seminole-hard-rock", "Hollywood, CA", "OTHER_US"),
    ("seminole-hard-rock", "Seminole Hard Rock Hotel & Casino Tampa", "FLORIDA"),
    ("four-seasons", "Four Seasons Surf Club", "MIAMI"),
    ("sh-hotels", "Starwood Hotels - Florida", "MIAMI"),
    ("sh-hotels", "Starwood Hotels - California", "OTHER_US"),
    ("5wpr", "Florida", "MIAMI"),
    ("virgin-voyages", "Plantation - FL", "MIAMI"),
    ("virgin-voyages", "", "MIAMI"),
    ("the-drum", "Seminole Hard Rock Hotel & Casino Hollywood", "UNKNOWN"),     # only at that employer
])
def test_employer_miami_places(emp_id, loc, bucket):
    e = {r["id"]: r for r in yaml.safe_load(open(EMPLOYERS_YAML))["employers"]}[emp_id]
    v = classify("Marketing Coordinator", loc, "", RULES, employer=e, company=e["name"],
                 default_location=e.get("home"))
    assert v.location == bucket, (emp_id, loc, v.location)
    assert "title says Miami" not in v.flags


def test_title_says_miami():
    v = classify("Reporter - NY, Miami, LA", "United States - Business Locations", "", RULES)
    assert v.relevant and v.location == "MIAMI" and "title says Miami" in v.flags
    for loc in ("New York, NY", "Tampa, FL", ""):
        v = classify("Staff Writer, Miami", loc, "", RULES)
        assert v.location == "MIAMI" and "title says Miami" in v.flags, loc
    v = classify("Reporter, Axios New York", "", "", RULES)
    assert v.location == "NYC" and "title says Miami" not in v.flags
    v = classify("Reporter, Axios Miami", "Remote", "", RULES)
    assert v.location == "REMOTE"                                   # remote already counts: left alone


@pytest.mark.parametrize("title,rel", [
    # University of Miami posts hundreds of these: lab and hospital jobs.
    ("Clinical Research Coordinator 2", False), ("Research Associate 2", False), ("Research Assistant 1", False),
    ("Sr. Research Associate", False), ("Postdoctoral Associate", False), ("Research Associate 2, SOM (A)", False),
    ("Assistant Professor, Film and Media Production", False), ("Medical Editor", False), ("Lab Coordinator", False),
    ("Customs Entry Writer", False), ("Claims Writer", False),
    # Newsroom research is still newsroom work.
    ("News Research Assistant", True), ("Editorial Researcher", True), ("Research Editor", True),
    ("Newsroom Researcher", True), ("Editorial Assistant", True), ("News Associate", True),
])
def test_research_titles(title, rel):
    v = classify(title, "Miami, FL", "", RULES)
    assert v.relevant is rel, (title, v.reason)


@pytest.mark.parametrize("title,kind", [
    ("PT Traffic Anchor/Editor", "part_time"), ("P/T News Writer - Weekends and Holiday Mornings", "part_time"),
    ("Editor (PT)", "part_time"), ("PT Promotions Assistant", "part_time"),
    ("Promotions Coordinator - On Call", "part_time"), ("On-Call Producer", "part_time"),
    ("PTA Newsletter Editor", "unknown"), ("Script Writer", "unknown"), ("Casino Promotions Coordinator - FT", "unknown"),
    ("Casual Dining Marketing Coordinator", "unknown"),           # a restaurant job, not a "casual" contract
])
def test_part_time_abbreviations(title, kind):
    assert employment(title, "", None, RULES) == kind


def test_spanish_needs_more_than_latin():
    wmg, sony, tvu = _row("warner-music"), _row("sony-music"), _row("televisaunivision")
    # Their groups say "Latin office in Miami Beach"; the job itself doesn't.
    for e in (wmg, sony):
        assert not classify("Social Media Coordinator", "Miami, FL", "", RULES, employer=e, company=e["name"]).spanish
    for title in ("Redactor de Contenido Digital", "Periodista", "Coordinador de Mercadeo", "Reportera",
                  "Latin Music Editor", "Editor, Latin/Pop", "Manager, Communications (Latin America)",
                  "Writer, Latino Culture"):
        assert classify(title, "Miami, FL", "", RULES).spanish, title
    for title in ("Reporter", "Content Producer", "Coordinator", "Editor"):
        assert not classify(title, "Miami, FL", "", RULES).spanish, title
    assert not classify("Editor", "Miami, FL", "Send a writing sample in Latin or Greek.", RULES).spanish
    # `spanish: true` on the row: every job there.
    assert classify("Marketing Coordinator", "Doral, FL", "", RULES, employer=tvu, company=tvu["name"]).spanish
    assert classify("Marketing Coordinator", "Doral, FL", "", RULES, employer={"name": "X", "spanish": True}).spanish


def test_welcome_neither_eats_the_morning_nor_repeats_its_jobs(tmp_path, monkeypatch):
    import src.env
    monkeypatch.setattr(src.env, "load_env", lambda *a, **k: None)
    monkeypatch.delenv("RADAR_EMAIL_NOW", raising=False)
    sent = []
    monkeypatch.setattr(notify, "send", lambda subject, body: sent.append(subject) or True)
    p = str(tmp_path / "state.json")
    now = store.now_iso()

    def job(pid, title, **kw):
        return {"pid": pid, "title": title, "company": "The Drum", "employer_id": "the-drum", "kind": "employer",
                "location": "MIAMI", "location_raw": "Miami, FL", "work_mode": "onsite", "employment": "full_time",
                "relevant": True, "status": "open", "tier": "core", "family": "editorial", "url": "https://x/" + pid,
                "first_seen": now, "flags": [], **kw}
    st = store.empty()
    st["runs"] = [{"started_at": "x"}]
    # Found by crawls after the first one (not baseline), before the welcome went out.
    st["postings"] = {"a": job("a", "Reporter"), "b": job("b", "Staff Writer"),
                      "ny": job("ny", "Editor", location="NYC", location_raw="New York, NY")}
    store.save(st, p)
    notify.main([], state_path=p, now_utc=_at(2, 0))               # 2am: the welcome goes whenever it can
    s = store.load(p)
    assert sent == ["[Job Radar] Hi Ari, your job radar is ready (2 jobs open now)"]
    assert s["welcomed"] and "last_digest_day" not in s
    assert s["postings"]["a"]["emailed_at"] and s["postings"]["b"]["emailed_at"]
    assert not s["postings"]["ny"].get("emailed_at")                 # not on her radar: not counted

    s["postings"]["c"] = job("c", "Copywriter")                      # turns up after the welcome
    store.save(s, p)
    notify.main([], state_path=p, now_utc=_at(7, 21))              # same morning: the summary still comes
    assert len(sent) == 2 and sent[1] == "[Job Radar] Wednesday: 1 new job — 1 in Miami"
    assert store.load(p)["last_digest_day"] == "2026-10-21"


def test_export_refreshes_the_fit_from_years(tmp_path):
    st = store.empty()
    base = {"kind": "employer", "employer_id": "the-drum", "company": "The Drum", "location": "MIAMI",
            "location_raw": "Miami, FL", "status": "closed", "closed_at": store.now_iso(), "relevant": True,
            "seniority": "mid", "first_seen": "2026-10-19T10:00:00+00:00"}
    st["postings"] = {
        # Classified before Ari's answers, when 3 years was a stretch.
        "o": {"pid": "o", "title": "Staff Writer", "years": 3, "fit": "stretch",
              "flags": ["3+ yrs asked", "location not stated"], **base},
        "s": {"pid": "s", "title": "Reporter", "years": 5, "fit": "early", "flags": [], **base},
        "n": {"pid": "n", "title": "Editor", "years": None, "fit": "open", "flags": ["video / photo editing"], **base},
    }
    out = str(tmp_path / "radar.json")
    crawl.export(st, CFG, yaml.safe_load(open(EMPLOYERS_YAML))["employers"], [], out)
    r = {p["title"]: p for p in json.load(open(out))["postings"]}
    assert r["Staff Writer"]["fit"] == "early" and r["Staff Writer"]["flags"] == ["location not stated"]
    assert r["Reporter"]["fit"] == "stretch" and r["Reporter"]["flags"] == ["5+ yrs asked"]
    assert r["Editor"]["fit"] == "open" and r["Editor"]["flags"] == ["video / photo editing"]
    assert st["postings"]["o"]["flags"] == ["3+ yrs asked", "location not stated"]   # state itself untouched


_YM = """<?xml version="1.0" encoding="utf-8" ?><rss version="2.0"><channel><title>Latest Listings</title>
  <item><title><![CDATA[Director---Vice-President,-Sales]]></title>
    <link>https://{site}/display-job/1/Director---Vice-President,-Sales.html</link><guid>https://{site}/display-job/1</guid>
    <description><![CDATA[Washington, \n\t\tU.S. News & World Report<br/><p>Lead sales.</p>]]></description></item>
  <item><title><![CDATA[Senior-News-Reporter-(NY-or-DC)]]></title>
    <link>https://{site}/display-job/2/x.html</link><guid>https://{site}/display-job/2</guid>
    <description><![CDATA[Washington, \n\t\tU.S. News & World Report<br/><p>Report.</p>]]></description></item>
  <item><title><![CDATA[Bilingual-Reporter---Spanish-Language-News]]></title>
    <link>https://{site}/display-job/3/x.html</link><guid>https://{site}/display-job/{n}</guid>
    <description><![CDATA[Miami, \n\t\tMiami Today<br/><p>Cover Miami-Dade.</p>]]></description></item>
  <item><title><![CDATA[News-Producer]]></title>
    <link>https://{site}/display-job/4/News-Producer.html</link><guid>https://{site}/display-job/4</guid>
    <description><![CDATA[, \n\t\tWCVB TV<br/><p>Produce.</p>]]></description></item>
</channel></rss>"""


class FeedClient:
    def __init__(self, xml):
        self.xml = xml.encode()

    def get(self, url, **kw):
        r = FakeResp({})
        r.content = self.xml
        return r


def _ym_feed(fid):
    return next(f for f in CFG["aggregators"]["rss"]["feeds"] if f["id"] == fid)


def test_ymcareers_feeds_read_like_job_titles():
    assert _ym_feed("nahj")["title_format"] == _ym_feed("nabj")["title_format"] == "ymcareers"
    assert _ym_feed("nabj")["url"] == "https://nabjcareers.org/listing-feeds/?feedId=10"
    from src import aggregators
    out = aggregators.fetch_rss(FeedClient(_YM.format(site="nahjcareers.org", n=3)), _ym_feed("nahj"))
    assert [p.title for p in out] == ["Director – Vice President, Sales", "Senior News Reporter (NY or DC)",
                                      "Bilingual Reporter – Spanish Language News", "News Producer"]
    assert [(p.location_raw, p.company) for p in out] == [
        ("Washington", "U.S. News & World Report"), ("Washington", "U.S. News & World Report"),
        ("Miami", "Miami Today"), ("", "WCVB TV")]
    assert out[2].description.startswith("Miami, Miami Today Cover Miami-Dade.")
    v = classify(out[2].title, out[2].location_raw, out[2].description, RULES, company=out[2].company)
    assert v.relevant and v.location == "MIAMI" and v.spanish


def test_the_same_job_on_both_feeds_is_shown_once(tmp_path, monkeypatch):
    from src import aggregators
    state_p, radar_p = str(tmp_path / "state.json"), str(tmp_path / "radar.json")
    monkeypatch.setattr(crawl.watch, "check", lambda *a, **k: {"watched": 0, "changed": 0, "unwatchable": 0})
    monkeypatch.setattr(crawl.time, "sleep", lambda s: None)
    feeds = [{"id": f"rss_{fid}", "name": _ym_feed(fid)["name"], "cfg": _ym_feed(fid),
              "fetch": lambda client, c: aggregators.fetch_rss(
                  FeedClient(_YM.format(site=f"{c['id']}careers.org", n=30 + len(c["id"]))), c)}
             for fid in ("nahj", "nabj")]
    monkeypatch.setattr(crawl.aggregators, "enabled", lambda cfg, state=None: feeds)
    crawl.run(state_path=state_p, radar_path=radar_p, fetchers={k: (lambda c, e: []) for k in adapters.ADAPTERS})
    s = store.load(state_p)
    copies = [p for p in s["postings"].values() if p["title"].startswith("Bilingual Reporter")]
    assert len(copies) == 2 and sum(1 for p in copies if p.get("dupe_of")) == 1
    shown = [p for p in json.load(open(radar_p))["postings"] if p["title"].startswith("Bilingual Reporter")]
    assert len(shown) == 1 and shown[0]["via"] in ("NAHJ Career Center", "NABJ Career Center")


def test_every_ats_is_known_and_aliases_dont_clash():
    rows = yaml.safe_load(open(EMPLOYERS_YAML))["employers"]
    known = set(adapters.ADAPTERS) | adapters.MANUAL
    assert sorted({r["id"] for r in rows if r.get("ats") not in known}) == []
    owner = {}
    for r in rows:
        for n in {crawl.norm_company(x) for x in [r["name"], *(r.get("aliases") or [])]} - {""}:
            assert owner.setdefault(n, r["id"]) == r["id"], (n, owner[n], r["id"])
    match = crawl.employer_matcher(rows)
    assert match("Voice Media Group") == "voice-media-group" and match("Miami New Times") == "miami-new-times"
