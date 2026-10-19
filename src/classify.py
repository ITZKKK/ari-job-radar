"""
Decide whether a posting is relevant to Ari, and describe it.

Pure functions, no network: given a title, a location string and (optionally)
a description, return the role family, seniority, location bucket and a
relevant / not-relevant verdict with the reason. Everything tunable lives in
config.yaml so the rules can change without touching code.

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
    flags: list[str] = field(default_factory=list)


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
        loc = cfg["locations"]
        self.buckets = [(b["id"], [m.lower() for m in b["match"]]) for b in loc["buckets"]]
        self.foreign = [m.lower() for m in loc.get("foreign", [])]
        self.us = [m.lower() for m in loc.get("us_markers", [])]


# "3+ years", "minimum of 4 years", "at least 2 years", "2-4 years of experience"
_YEARS = re.compile(
    r"(?:(?:minimum|at least|min\.?)\s+(?:of\s+)?)?(\d{1,2})\s*(?:\+|-\s*\d{1,2}|to\s+\d{1,2})?\s*"
    r"(?:\+\s*)?years?(?:'|’)?\s+(?:of\s+)?(?:\w+\s+){0,4}?(?:experience|exp\b)", re.I)


_VISUAL = re.compile(r"\b(video|photo|podcast|post[- ]production|graphics(/multimedia)?|multimedia) editor\b|"
                     r"\bvideographer\b|\bshooter\b", re.I)


def years_required(text: str) -> int | None:
    """Smallest 'N years of experience' figure in a description, if any.

    The smallest, because descriptions often say "2+ years required, 5+
    preferred" and the requirement is what decides whether she can apply.
    """
    vals = [int(m.group(1)) for m in _YEARS.finditer(text or "")]
    vals = [v for v in vals if 0 < v < 25]
    return min(vals) if vals else None


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


def classify(title: str, location_raw: str, description: str, rules: Rules) -> Verdict:
    title = (title or "").strip()
    loc = location_bucket(location_raw, rules)
    if loc == "UNKNOWN":
        # "Reporter, Axios Miami" with no location field: the title says where.
        from_title = location_bucket(title, rules)
        if from_title not in ("UNKNOWN", "OTHER_US", "FOREIGN"):
            loc = from_title
    sen = seniority(title, rules)
    yrs = years_required(description)
    v = Verdict(relevant=False, reason="", seniority=sen, location=loc, years=yrs)

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

    if _VISUAL.search(title):
        v.flags.append("video / photo editing")
    if yrs is not None and yrs >= 3:
        v.flags.append(f"{yrs}+ yrs asked")
    if sen == "intern":
        v.flags.append("internship")
    if loc == "UNKNOWN":
        v.flags.append("location not stated")
    v.relevant = True
    v.reason = f"{v.family} ({v.tier})"
    return v
