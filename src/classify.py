"""
Decide whether a posting is relevant to Ari, and describe it.

Pure functions, no network: given a title, a location string and (optionally)
a description, return the role family, seniority, location bucket, work mode
(remote / hybrid / onsite), early-career fit and a relevant / not-relevant
verdict with the reason. Everything tunable lives in config.yaml so the rules
can change without touching code; `visible()` is the one rule that decides
what the dashboard shows and the email sends.

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


def visible(location: str | None, work_mode: str | None, loc_cfg: dict,
            remote_in_head: bool = False) -> bool:
    """Is this a job Ari can do from Miami? One rule for the dashboard and the
    email (the email passes {"show": locations.email}). REMOTE counts only when
    fully remote; a city job counts when its head offers remote."""
    if location == "FOREIGN":
        return False
    if location == "REMOTE":
        return work_mode not in ("hybrid", "onsite")
    if remote_in_head:
        return True
    return location in loc_cfg["show"]


def location_bucket(raw: str, rules: Rules) -> str:
    s = f" {(raw or '').lower()} "
    if not raw or not raw.strip():
        return "UNKNOWN"
    for bid, marks in rules.buckets:
        if any(m in s for m in marks):
            # "Remote - London" is not US remote.
            if bid == "REMOTE" and _is_foreign(s, rules):
                return "FOREIGN"
            return bid
    if _is_foreign(s, rules):
        return "FOREIGN"
    if any(m in s for m in rules.us) or re.search(r",\s*[a-z]{2}\b", s):
        return "OTHER_US"
    return "UNKNOWN"


def _is_foreign(s: str, rules: Rules) -> bool:
    return any(m in s for m in rules.foreign) and not any(m in s for m in rules.us)


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
             default_location: str | None = None, workplace_hint: str | None = None) -> Verdict:
    title = (title or "").strip()
    loc = location_bucket(location_raw, rules)
    if loc == "UNKNOWN":
        # "Reporter, Axios Miami" with no location field: the title says where.
        from_title = location_bucket(title, rules)
        if from_title not in ("UNKNOWN", "OTHER_US", "FOREIGN"):
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
                remote_offered=remote_in_head(location_raw, title) or hint == "remote")

    for rx in rules.exclude:
        if rx.search(title):
            v.reason = f"excluded title ({rx.pattern[:40]})"
            return v

    for fid, tier, pats in rules.families:
        if any(p.search(title) for p in pats):
            v.family, v.tier = fid, tier
            break
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
    if yrs is not None and yrs >= 3:
        v.flags.append(f"{yrs}+ yrs asked")
    if sen == "intern":
        v.flags.append("internship")
    if loc == "UNKNOWN":
        v.flags.append("location not stated")
    if assumed:
        home = {"MIAMI": "Miami", "NYC": "New York"}.get(default_location, default_location)
        v.flags += ["location not stated", f"assumed {home} (employer is {home}-based)"]
    v.relevant = True
    v.reason = f"{v.family} ({v.tier})"
    return v
