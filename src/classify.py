"""
Decide whether a posting is relevant to Ari, and describe it.

Pure functions, no network: given a title, a location string and (optionally)
a description, return the role family, seniority, location bucket, work mode
(remote / hybrid / onsite), fit, kind of job (full-time, part-time...), beats,
whether Spanish is involved, and a relevant / not-relevant verdict with the
reason. Everything tunable lives in config.yaml so the rules can change
without touching code; `visible()` is the one rule that decides what the
dashboard shows and the email sends.

Philosophy carried over from the IB tracker: when in doubt, keep the posting
and flag it. A false positive costs Ari a few seconds; a false negative costs
her an application.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Verdict:
    relevant: bool
    reason: str                     # why it was kept or dropped, in words
    family: str | None = None       # editorial / content / social / pr_comms / marketing_copy
    tier: str | None = None         # core / also
    seniority: str = "mid"          # entry / mid / senior / intern
    location: str = "UNKNOWN"       # MIAMI / NYC / REMOTE / FLORIDA / OTHER_US / UNKNOWN / FOREIGN
    years: int | None = None        # minimum years asked for, if the description says
    work_mode: str = "unknown"      # remote / hybrid / onsite / unknown
    fit: str = "open"               # early / open / stretch — how early-career the ask is
    remote_offered: bool = False    # the location or title itself says remote ("NYC or Remote")
    flags: list[str] = field(default_factory=list)
    employment: str = "unknown"     # full_time / part_time / freelance / contract / intern / unknown
    topics: list[str] = field(default_factory=list)   # her beats this job is about (config topics ids)
    spanish: bool = False           # mentions Spanish / bilingual / Telemundo...


# What the page and the email call each kind of job. Fixed vocabulary: the
# kinds themselves are decided in code (employment() below).
EMPLOYMENT_LABELS = {"full_time": "Full-time", "part_time": "Part-time", "freelance": "Freelance",
                     "contract": "Contract / temporary", "intern": "Internship", "unknown": "Not stated"}


# Early-career fit, when config.yaml has no `fit:` block.
_FIT_EARLY_DESC = (r"\bentry[- ]level\b|\bearly[- ]career\b|\b(recent|new) grad(uate)?s?\b|"
                   r"\bno (prior |previous )?experience (necessary|required|needed)\b|"
                   r"\bup to (1|2|one|two) years?\b")
_FIT_STRETCH_TITLE = r"\bmanager\b"


class Rules:
    """config.yaml compiled once."""

    def __init__(self, cfg: dict):
        r = cfg["roles"]
        self.families = [
            (f["id"], f["tier"], [re.compile(p, re.I) for p in f["patterns"]])
            for f in r["families"]
        ]
        self.exclude = [re.compile(p, re.I) for p in r.get("exclude", [])]
        s = cfg["seniority"]
        self.entry = re.compile(s["entry_markers"], re.I)
        self.senior = re.compile(s["senior_markers"], re.I)
        self.intern = re.compile(r"\bintern(ship)?s?\b", re.I)
        self.keep_senior = bool(s.get("keep_senior", False))
        self.manager_is_mid = bool(s.get("manager_is_mid", True))
        self.keep_internships = bool(s.get("keep_internships", False))
        self.max_years = s.get("max_years_required")
        f = cfg.get("fit") or {}
        self.early_max_years = int(f.get("early_max_years", 2))
        self.early_desc = re.compile(f.get("early_description_markers", _FIT_EARLY_DESC), re.I)
        self.stretch_title = re.compile(f.get("stretch_title_markers", _FIT_STRETCH_TITLE), re.I)
        loc = cfg["locations"]
        self.buckets = [(b["id"], [m.lower() for m in b["match"]]) for b in loc["buckets"]]
        self.foreign = [m.lower() for m in loc.get("foreign", [])]
        self.us = [m.lower() for m in loc.get("us_markers", [])]
        ag = r.get("agency_titles") or {}
        self.agency_industry = ag.get("industry")
        self.agency_family = ag.get("family")
        self.agency = [re.compile(p, re.I) for p in ag.get("patterns", [])]
        tiers = {fid: tier for fid, tier, _ in self.families}
        self.agency_tier = tiers.get(self.agency_family)
        e = cfg.get("employment") or {}
        self.emp_title = [(k, re.compile(v, re.I)) for k, v in (e.get("title_markers") or {}).items()]
        self.emp_desc = [(k, re.compile(v, re.I)) for k, v in (e.get("description_markers") or {}).items()]
        self.fellowship = re.compile(e.get("fellowship_markers") or r"\bfellow(ship)?s?\b", re.I)
        self.topics = [(t["id"], re.compile(t["match"], re.I)) for t in (cfg.get("topics") or [])]
        self.topic_desc_words = int(cfg.get("topics_description_min_words", 2))
        lang = cfg.get("languages") or {}
        self.spanish = re.compile(lang["spanish_markers"], re.I) if lang.get("spanish_markers") else None


# "3+ years", "minimum of 4 years", "at least 2 years", "2-4 years of experience"
_YEARS = re.compile(
    r"(?:(?:minimum|at least|min\.?)\s+(?:of\s+)?)?(?<!\d)(\d{1,2})\s*(?:\+|-\s*\d{1,2}|to\s+\d{1,2})?\s*"
    r"(?:\+\s*)?years?(?:'|’)?\s+(?:of\s+)?(?:\w+\s+){0,4}?(?:experience|exp\b)", re.I)


_VISUAL = re.compile(r"\b(video|photo|podcast|post[- ]production|graphics(/multimedia)?|multimedia) editor\b|"
                     r"\bvideographer\b|\bshooter\b", re.I)


def years_required(text: str) -> int | None:
    """Smallest 'N years of experience' figure in a description, if any.

    The smallest, because descriptions often say "2+ years required, 5+
    preferred" and the requirement is what decides whether she can apply.
    """
    vals = [int(m.group(1)) for m in _YEARS.finditer(text or "")]
    vals = [v for v in vals if 0 <= v < 25]       # "0-2 years" is a real answer: 0
    return min(vals) if vals else None


# Work mode. Constants rather than config: these are language, not preference.
# "hybrid", or N days a week near office / on-site / in person (either order).
_WM_HYBRID = re.compile(
    r"\bhybrid\b|"
    r"\b(?:[1-4]|one|two|three|four)\s*(?:-|–|to|or)?\s*(?:[1-4]|one|two|three|four)?\s*days?\s*(?:a|per|/)\s*week\b"
    r"[\s\S]{0,40}\b(?:office|on[- ]?site|in[- ]person)\b|"
    r"\b(?:office|on[- ]?site|in[- ]person)\b[\s\S]{0,40}"
    r"\b(?:[1-4]|one|two|three|four)\s*(?:-|–|to|or)?\s*(?:[1-4]|one|two|three|four)?\s*days?\s*(?:a|per|/)\s*week\b",
    re.I)
# In a LOCATION field "Anywhere" / "Distributed" / "Virtual" mean remote…
_WM_REMOTE_HEAD = re.compile(r"\bremote\b|\bwork[- ]from[- ]home\b|\bwfh\b|\banywhere\b|\bdistributed\b|"
                             r"\bvirtual\b|\btelecommut\w*", re.I)
# …but in a TITLE ("Virtual Events Producer") only the plain words count, and a
# DESCRIPTION has to say the job itself is remote: "remote team", "remote
# interviews" and a bare "remote" are not a work mode.
_WM_REMOTE_TITLE = re.compile(r"\bremote\b|\bwfh\b|\bwork[- ]from[- ]home\b|\btelecommut\w*", re.I)
_WM_REMOTE_BODY = re.compile(r"\b(?:fully|100%|completely|entirely)[- ]remote\b|"
                             r"\bremote[- ](?:first|position|role|job|opportunity|based|friendly|eligible)\b|"
                             r"\bthis (?:position|role|job) is remote\b|\bwork(?:ing)? remotely\b|"
                             r"\bremote (?:within|anywhere in|from anywhere in) the (?:us|u\.s\.|united states)\b|"
                             r"\bwork[- ]from[- ]home\b|\bwfh\b|\btelecommut\w*", re.I)
_WM_ONSITE = re.compile(r"\bon[- ]?site\b|\bin[- ]office\b|\bin[- ]person\b|\bin the office\b|\bon[- ]location\b", re.I)
_WM_NOT_REMOTE = re.compile(r"\b(?:not|no|non|isn'?t|is not|cannot be|can'?t be)\s+(?:a\s+)?(?:remote|work[- ]from[- ]home)\b|"
                            r"\bnon-remote\b|\bremote work is not\b", re.I)


def remote_in_head(location_raw: str | None, title: str | None) -> bool:
    """Does the posting's HEAD (location field or title) offer remote?
    "Washington, DC, New York, New York or Remote" -> True: a job she can do
    from Miami even though its bucket is NYC. A remote mention only in the
    description body does not count (too many "remote teams")."""
    head = f"{location_raw or ''} | {title or ''}"
    hit = _WM_REMOTE_HEAD.search(location_raw or "") or _WM_REMOTE_TITLE.search(title or "")
    return bool(hit) and not _WM_NOT_REMOTE.search(head)


def work_mode(location_raw: str | None, title: str | None, description: str | None,
              hint: str | None = None) -> str:
    """remote / hybrid / onsite / unknown. An adapter's own field wins (Lever's
    workplaceType), then the head (location + title), then the description.
    "Remote + a city" is remote: an alternative being offered, not hybrid."""
    h = (hint or "").lower().replace("-", "").replace(" ", "")
    if h in ("remote", "hybrid", "onsite"):
        return h
    head = f"{location_raw or ''} | {title or ''}"
    if _WM_HYBRID.search(head):
        return "hybrid"
    if remote_in_head(location_raw, title):
        return "remote"
    if _WM_ONSITE.search(head):
        return "onsite"
    body = description or ""
    if _WM_HYBRID.search(body):
        return "hybrid"
    if _WM_NOT_REMOTE.search(body):
        return "onsite"
    r, o = bool(_WM_REMOTE_BODY.search(body)), bool(_WM_ONSITE.search(body))
    if r and o:
        return "hybrid"
    if r:
        return "remote"
    if o:
        return "onsite"
    return "unknown"


# The board's own "kind of job" field, in the words the platforms use:
# Lever "Full-time" / "Temporary Full Time", Workable "Contract", Ashby
# "FullTime", SmartRecruiters "Full-time" / "Intern", JSearch "FULLTIME",
# Recruitee "fulltime_fixed_term", Remotive "part_time"... Language, not
# preference, so constants. Checked in this order: "Temporary Full Time" is
# temporary, "Full-time fixed term" is a fixed term.
_HINTS = [
    ("intern", re.compile(r"\bintern(ship)?s?\b")),
    ("freelance", re.compile(r"\bfreelanc")),
    ("contract", re.compile(r"\bcontract(or|ors|ual)?\b|\btemp(orary)?\b|\bfixed ?term\b|\bseasonal\b")),
    ("part_time", re.compile(r"\bpart ?time\b|\bper ?diem\b")),
    ("full_time", re.compile(r"\bfull ?time\b|\bpermanent\b|\bregular\b")),
]


def _hint_kind(hint: str | None) -> str | None:
    """"Full-time" / "FULLTIME" / "full_time" -> full_time; None when the board
    said nothing we recognise ("Other")."""
    h = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(hint or ""))       # "FullTime" -> "Full Time"
    h = re.sub(r"[-_/]+", " ", h.lower())
    h = h.replace("fulltime", "full time").replace("parttime", "part time")
    for kind, rx in _HINTS:
        if rx.search(h):
            return kind
    return None


def employment(title: str | None, description: str | None, hint: str | None, rules: Rules) -> str:
    """full_time / part_time / freelance / contract / intern / unknown.
    The board's own field wins, then the title, then strong phrases in the
    description. A fellowship is a full-time job with an end date, so a
    "temporary" or "contract" reading of one is full_time (it is labelled
    "Fellowship" instead)."""
    t = title or ""
    kind = _hint_kind(hint)
    if kind is None and rules.intern.search(t):
        kind = "intern"
    if kind is None:
        kind = next((k for k, rx in rules.emp_title if rx.search(t)), None)
    if kind is None:
        kind = next((k for k, rx in rules.emp_desc if rx.search(description or "")), None)
    if kind == "contract" and rules.fellowship.search(t):
        kind = "full_time"
    return kind or "unknown"


def topics(title: str | None, company: str | None, description: str | None, rules: Rules) -> list[str]:
    """Her beats this job is about, in config order. The title and the company
    name count on one mention; the description needs several different words
    for the same beat (topics_description_min_words), because "some travel
    required" or "our culture" is not what a job is about."""
    head = " | ".join(x for x in (title, company) if x)
    return [tid for tid, rx in rules.topics
            if rx.search(head) or len(_distinct_words(rx, description)) >= rules.topic_desc_words]


def _distinct_words(rx: re.Pattern, text: str | None) -> set[str]:
    """The different words `rx` finds, a plural counted with its singular:
    "chef, chefs, food" is two words, not three."""
    out = set()
    for m in rx.finditer(text or ""):
        w = m.group(0).lower().strip()
        out.add(w[:-1] if w.endswith("s") else w)
    return out


def spanish(title: str | None, company: str | None, description: str | None, rules: Rules) -> bool:
    """Does the job mention Spanish (bilingual, español, Telemundo, a Spanish
    job title...)? A badge and a small ranking boost, never a filter that
    hides anything."""
    if not rules.spanish:
        return False
    return any(rules.spanish.search(x or "") for x in (title, company, description))


def beats(title: str | None, company: str | None, description: str | None,
          employer: dict | None, rules: Rules) -> tuple[list[str], bool]:
    """(topics, spanish) for one posting, with the employers.yaml row it
    belongs to. The row adds only what it says itself: `topics: [food]` for an
    employer whose every job is on those beats, `spanish: true` for a
    Spanish-language employer. Its `group` text is never read: it describes
    the employer, not the job ("PR agency (consumer, beauty & food PR)" made
    every 5WPR job a food job). Nor, for beats, is the row's own display
    name; another company name is (an aggregator's "Eater" is a food job)."""
    emp = employer or {}
    co = short_company(company)
    sp = bool(emp.get("spanish")) or spanish(title, co, description, rules)
    if emp and co == short_company(emp.get("name")):
        co = ""
    found = set(topics(title, co, description, rules))
    own = set(emp.get("topics") or [])
    return [tid for tid, _ in rules.topics if tid in own or tid in found], sp


def short_company(name: str | None) -> str:
    """"NBCUniversal (Telemundo, NBC 6 Miami)" -> "NBCUniversal". The display
    names in employers.yaml list a parent's brands; those brands say nothing
    about which beat THIS job is on, so beats and Spanish read the short name."""
    return re.split(r"\s+[—(/]", name or "")[0].strip()


def fit(sen: str | None, years: int | None, title: str | None, description: str | None,
        rules: Rules) -> str:
    """early / open / stretch. Years beat titles: "Staff Writer, 2+ years" is
    early-career whatever the title says; a fellowship asking 3 is a stretch."""
    if years is not None:
        return "early" if years <= rules.early_max_years else "stretch"
    if rules.early_desc.search(description or ""):
        return "early"
    if rules.stretch_title.search(title or ""):
        return "stretch"
    if sen in ("entry", "intern"):
        return "early"
    return "open"


def fit_labels(cfg: dict) -> list[dict]:
    """The three fit labels, worded from config numbers so they never disagree
    with the rule: "Good fit (up to 3 years)", "Stretch (4–5 years, or a
    manager role)". Used by the dashboard export and the email."""
    early = int((cfg.get("fit") or {}).get("early_max_years", 2))
    cap = (cfg.get("seniority") or {}).get("max_years_required")
    lo, hi = early + 1, (int(cap) - 1 if cap else None)
    span = f"{lo}+" if hi is None else (f"{lo}" if hi <= lo else f"{lo}–{hi}")
    return [{"id": "early", "label": f"Good fit (up to {early} year{'' if early == 1 else 's'})"},
            {"id": "open", "label": "Experience not stated"},
            {"id": "stretch", "label": f"Stretch ({span} year{'' if span == '1' else 's'}, or a manager role)"}]


def visible(location: str | None, work_mode: str | None, loc_cfg: dict,
            remote_in_head: bool = False, employment: str | None = None,
            emp_cfg: dict | None = None) -> bool:
    """Is this a job Ari would take, from Miami? THE one rule for the dashboard
    and the email (the email passes {"show": locations.email}); nothing else
    hides a relevant job. REMOTE counts only when fully remote; a city job
    counts when its head offers remote. With `employment` and `emp_cfg`
    (config's employment block), a kind of job not in emp_cfg["show"]
    (part-time, freelance...) is not shown either."""
    if employment is not None and emp_cfg is not None and employment not in emp_cfg.get("show", []):
        return False
    if location == "FOREIGN":
        return False
    if location == "REMOTE":
        return work_mode not in ("hybrid", "onsite")
    if remote_in_head:
        return True
    return location in loc_cfg["show"]


# A US state code after a comma ("Austin, TX"). A pattern rather than plain
# text in config.yaml's us_markers, so ", CA" doesn't match ", Canada" and
# ", MA" doesn't match ", Malaysia". Language, not preference: a constant.
_US_STATE = re.compile(r",\s*(al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|ms|mo|mt|"
                       r"ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy|dc)\b(?!\w)")
# A dash with spaces round it separates parts like a comma does: "Plantation
# - FL", "Remote - USA", "Florida - Miami Office". "Remote-Florida" and
# "Miami-Dade" keep theirs.
_PLACE_DASH = re.compile(r"\s+[-–—]\s+")


def location_bucket(raw: str, rules: Rules) -> str:
    if not raw or not raw.strip():
        return "UNKNOWN"
    s = f" {_PLACE_DASH.sub(', ', raw.lower())} "
    for bid, marks in rules.buckets:
        if any(m in s for m in marks):
            # "Remote - London" is not US remote.
            if bid == "REMOTE" and _is_foreign(s, rules):
                return "FOREIGN"
            return bid
    if _is_foreign(s, rules):
        return "FOREIGN"
    if _is_us(s, rules):
        return "OTHER_US"
    return "UNKNOWN"


def _is_us(s: str, rules: Rules) -> bool:
    return any(m in s for m in rules.us) or bool(_US_STATE.search(s))


def _is_foreign(s: str, rules: Rules) -> bool:
    return any(m in s for m in rules.foreign) and not _is_us(s, rules)


def seniority(title: str, rules: Rules) -> str:
    t = title or ""
    if rules.intern.search(t):
        return "intern"
    if rules.senior.search(t):
        hits = {m.group(0).lower() for m in rules.senior.finditer(t)}
        if rules.manager_is_mid and hits <= {"manager"}:
            return "mid"
        return "senior"
    if rules.entry.search(t):
        return "entry"
    return "mid"


def classify(title: str, location_raw: str, description: str, rules: Rules, *,
             default_location: str | None = None, workplace_hint: str | None = None,
             employer: dict | None = None, company: str | None = None,
             employment_hint: str | None = None) -> Verdict:
    """`employer` is the employers.yaml row the posting came from or was
    matched to: its `industry: pr` turns agency titles into PR jobs, its
    `topics` / `spanish` add beats and the Spanish badge, and its
    `miami_match` names its Miami places. `company` is the hiring
    company as the source names it. `employment_hint` is the board's own
    full-time / part-time field."""
    title = (title or "").strip()
    emp = employer or {}
    loc = location_bucket(location_raw, rules)
    loc_flags = []
    from_title = location_bucket(title, rules)
    if emp.get("miami_match") and re.search(emp["miami_match"], location_raw or "", re.I):
        # The employer's own name for a Miami place: "Four Seasons Surf Club",
        # "Seminole Hard Rock Hotel & Casino Hollywood" (employers.yaml).
        loc = "MIAMI"
    elif loc in ("UNKNOWN", "OTHER_US", "FLORIDA", "NYC") and from_title == "MIAMI":
        # "Reporter - NY, Miami, LA" filed under "United States - Business
        # Locations", or "Editor, Miami" with no location at all.
        loc = "MIAMI"
        loc_flags.append("title says Miami")
    elif loc == "UNKNOWN" and from_title not in ("UNKNOWN", "OTHER_US", "FOREIGN"):
        # "Reporter, Axios New York" with no location field: the title says where.
        loc = from_title
    assumed = False
    if loc == "UNKNOWN" and default_location:
        # Employer opted in with `home: MIAMI`: its jobs can only be there.
        loc, assumed = default_location, True
    sen = seniority(title, rules)
    yrs = years_required(description)
    hint = (workplace_hint or "").lower().replace("-", "").replace(" ", "")
    v = Verdict(relevant=False, reason="", seniority=sen, location=loc, years=yrs,
                work_mode=work_mode(location_raw, title, description, workplace_hint),
                # The board's own "remote" field counts like the location saying it.
                remote_offered=remote_in_head(location_raw, title) or hint == "remote",
                employment=employment(title, description, employment_hint, rules))

    v.topics, v.spanish = beats(title, company, description, emp, rules)

    # PR / marketing agencies: "Account Coordinator" is their entry-level PR job.
    agency = bool(rules.agency_industry) and emp.get("industry") == rules.agency_industry and any(
        rx.search(title) for rx in rules.agency)
    for rx in rules.exclude:
        m = rx.search(title)
        if m and not (agency and any(a.search(m.group(0)) for a in rules.agency)):
            v.reason = f"excluded title ({rx.pattern[:40]})"
            return v

    for fid, tier, pats in rules.families:
        if any(p.search(title) for p in pats):
            v.family, v.tier = fid, tier
            break
    if not v.family and agency and rules.agency_tier:
        v.family, v.tier = rules.agency_family, rules.agency_tier
    if not v.family:
        v.reason = "not a writing / editorial / content / social / PR / marketing title"
        return v

    if loc == "FOREIGN":
        v.reason = "outside the US"
        return v
    if sen == "senior" and not rules.keep_senior:
        v.reason = "senior title"
        return v
    if sen == "intern" and not rules.keep_internships:
        v.reason = "internship"
        return v
    if yrs is not None and rules.max_years and yrs >= rules.max_years:
        v.reason = f"asks for {yrs}+ years"
        return v

    v.fit = fit(sen, yrs, title, description, rules)
    if _VISUAL.search(title):
        v.flags.append("video / photo editing")
    if yrs is not None and yrs > rules.early_max_years:
        v.flags.append(f"{yrs}+ yrs asked")
    if sen == "intern":
        v.flags.append("internship")
    elif rules.fellowship.search(title) and v.employment != "intern":
        v.flags.append("Fellowship")
    v.flags += loc_flags
    if loc == "UNKNOWN":
        v.flags.append("location not stated")
    if assumed:
        home = {"MIAMI": "Miami", "NYC": "New York"}.get(default_location, default_location)
        v.flags += ["location not stated", f"assumed {home} (employer is {home}-based)"]
    v.relevant = True
    v.reason = f"{v.family} ({v.tier})"
    return v
