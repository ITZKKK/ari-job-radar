"""
Aggregate sources: job-search APIs and feeds spanning many employers.

Each returns RawPosting rows like an employer adapter, with `company` set to
the hiring company as the source reports it. crawl.py matches that name
against employers.yaml (name + aliases) so a find at one of Ari's targets
carries the star.

Terms, checked 19 Oct 2026 (see README "Sources"):
  Adzuna   free key; personal use allowed; listings must credit Adzuna.
  JSearch  RapidAPI key; Google for Jobs data (LinkedIn/Indeed postings included).
  Remotive public API; credit Remotive and link to the posting; max ~4 calls/day.
  RSS      We Work Remotely asks for attribution; the NAHJ and NABJ career
           centre feeds are public.
The dashboard shows "via <source>" on every aggregator posting for that reason.
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET

from src import store
from src.adapters import AdapterError, RawPosting, _get, strip_html


def _join(*parts) -> str:
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


# --- Adzuna --------------------------------------------------------------------

def fetch_adzuna(client, c: dict) -> list[RawPosting]:
    app_id, key = os.environ["ADZUNA_APP_ID"], os.environ["ADZUNA_APP_KEY"]
    out, seen = [], set()
    for where in c["where"]:
        for page in range(1, int(c.get("pages", 2)) + 1):
            r = _get(client, f"https://api.adzuna.com/v1/api/jobs/us/search/{page}", params={
                "app_id": app_id, "app_key": key, "what_or": c["what_or"], "where": where,
                "distance": c.get("distance_km", 40), "max_days_old": c.get("max_days_old", 10),
                "results_per_page": 50, "sort_by": "date", "content-type": "application/json"})
            res = r.json().get("results", [])
            for j in res:
                jid = str(j.get("id"))
                if jid in seen:
                    continue
                seen.add(jid)
                out.append(RawPosting(
                    source_id="adzuna", job_id=jid, title=strip_html(j.get("title", "")),
                    company=strip_html((j.get("company") or {}).get("display_name", "")),
                    location_raw=(j.get("location") or {}).get("display_name", "") or where,
                    url=j.get("redirect_url", ""), description=strip_html(j.get("description", "")),
                    posted_at=j.get("created"), extra={"via": "Adzuna", "employment": _adzuna_kind(j)}))
            if len(res) < 50:
                break
            time.sleep(2.5)          # 25 calls/minute limit
    return out


def _adzuna_kind(j: dict) -> str | None:
    """contract_type ("permanent" / "contract") and contract_time ("full_time" /
    "part_time"): a contract is a contract whatever its hours."""
    if j.get("contract_type") == "contract":
        return "contract"
    return j.get("contract_time") or j.get("contract_type")


# --- JSearch (RapidAPI) ----------------------------------------------------------

def fetch_jsearch(client, c: dict) -> list[RawPosting]:
    key = os.environ["JSEARCH_RAPIDAPI_KEY"]
    out = []
    for q in c["queries"]:
        # v5 of the API moved Job Search from /search to /search-v2.
        r = _get(client, "https://jsearch.p.rapidapi.com/search-v2", params={
            "query": q, "num_pages": 1, "country": "us",
            "date_posted": c.get("date_posted", "3days")},
            headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"})
        data = r.json().get("data") or []
        if isinstance(data, dict):
            data = data.get("jobs") or []
        for j in data:
            loc = _join(j.get("job_city"), j.get("job_state"), j.get("job_country"))
            if j.get("job_is_remote"):
                loc = _join(loc, "Remote")
            # "FULLTIME" / "PARTTIME" / "CONTRACTOR" / "INTERN" (a list in newer responses)
            kind = j.get("job_employment_type") or next(iter(j.get("job_employment_types") or []), None)
            out.append(RawPosting(
                source_id="jsearch", job_id=str(j.get("job_id")), title=j.get("job_title", ""),
                company=j.get("employer_name", ""), location_raw=loc,
                url=j.get("job_apply_link") or j.get("job_google_link", ""),
                description=(j.get("job_description") or "")[:3000],
                posted_at=j.get("job_posted_at_datetime_utc"),
                extra={"via": f"JSearch / {j.get('job_publisher') or 'Google Jobs'}", "employment": kind}))
        time.sleep(1.5)
    return out


# --- Remotive ------------------------------------------------------------------

def fetch_remotive(client, c: dict) -> list[RawPosting]:
    out = []
    for cat in c["categories"]:
        r = _get(client, "https://remotive.com/api/remote-jobs", params={"category": cat})
        for j in r.json().get("jobs", []):
            out.append(RawPosting(
                source_id="remotive", job_id=str(j.get("id")), title=j.get("title", ""),
                company=j.get("company_name", ""),
                location_raw=f"Remote ({j.get('candidate_required_location') or 'anywhere'})",
                url=j.get("url", ""), description=strip_html(j.get("description", ""))[:3000],
                posted_at=j.get("publication_date"),
                extra={"via": "Remotive", "employment": j.get("job_type")}))   # full_time / contract / freelance...
        time.sleep(31)               # they block more than 2 calls a minute
    return out


# --- Generic RSS ----------------------------------------------------------------

def _ym_title(title: str) -> str:
    """YM Careers feeds (NAHJ, NABJ) send the page's URL slug as the title:
    "Director---Vice-President,-Sales" -> "Director – Vice President, Sales",
    "Senior-News-Reporter-(NY-or-DC)" -> "Senior News Reporter (NY or DC)"."""
    return " ".join(title.replace("---", " – ").replace("-", " ").split())


def _ym_head(raw_desc: str) -> tuple[str, str]:
    """(city, company) from the start of a YM Careers description,
    "Washington, U.S. News & World Report<br/>...". The city may be blank
    (", WCVB TV"); the company may hold a comma ("Reno, University of Nevada, Reno")."""
    city, _, company = raw_desc.split("<br", 1)[0].partition(",")
    return strip_html(city), strip_html(company)


def fetch_rss(client, feed: dict) -> list[RawPosting]:
    """title_format: "title" (as is), "company: title" (We Work Remotely), or
    "ymcareers" (the NAHJ / NABJ career centres: slug titles, and the city and
    company at the start of the description)."""
    r = _get(client, feed["url"], headers={"Accept": "application/rss+xml, application/xml, text/xml"})
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        raise AdapterError(f"not an RSS feed ({e})") from e
    fmt = feed.get("title_format")
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link or title).strip()
        raw_desc = it.findtext("description") or ""
        company, city = "", ""
        if fmt == "company: title" and ":" in title:
            company, title = (x.strip() for x in title.split(":", 1))
        elif fmt == "ymcareers":
            title = _ym_title(title)
            city, company = _ym_head(raw_desc)
        desc = strip_html(raw_desc)
        loc = it.findtext("region") or it.findtext("location") or city or feed.get("default_location", "")
        if not loc:
            m = re.search(r"\b(?:Location|Headquarters)\s*:?\s*([A-Z][\w .'-]+,\s*[A-Z]{2})\b", desc)
            loc = m.group(1) if m else ""
        out.append(RawPosting(
            source_id=f"rss_{feed['id']}", job_id=guid, title=title, company=company,
            location_raw=loc, url=link, description=desc[:3000],
            posted_at=it.findtext("pubDate"),
            # We Work Remotely: <type>Full-Time</type> / <type>Contract</type>
            extra={"via": feed["name"], "employment": (it.findtext("type") or "").strip() or None}))
    return out


# --- Which sources run this time ------------------------------------------------

def _due(state: dict, sid: str, hours: float) -> bool:
    s = (state or {}).get("sources", {}).get(sid) or {}
    return store.days_since(s.get("last_ok")) * 24 >= (hours or 0)


def _sources(cfg: dict) -> list[dict]:
    """Every source config (and the keys in the environment) turns on, due or
    not: {"id", "name", "fetch", "cfg"} rows."""
    a = cfg.get("aggregators") or {}
    out = []
    c = a.get("adzuna") or {}
    if c.get("enabled") and os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"):
        out.append({"id": "adzuna", "name": "Adzuna (job search)", "fetch": fetch_adzuna, "cfg": c})
    c = a.get("jsearch") or {}
    if c.get("enabled") and os.environ.get("JSEARCH_RAPIDAPI_KEY"):
        out.append({"id": "jsearch", "name": "JSearch (Google for Jobs)", "fetch": fetch_jsearch, "cfg": c})
    c = a.get("remotive") or {}
    if c.get("enabled"):
        out.append({"id": "remotive", "name": "Remotive (remote jobs)", "fetch": fetch_remotive, "cfg": c})
    c = a.get("rss") or {}
    if c.get("enabled"):
        for f in c.get("feeds", []):
            out.append({"id": f"rss_{f['id']}", "name": f["name"], "fetch": fetch_rss,
                        "cfg": {**f, "min_hours_between": c.get("min_hours_between", 0)}})
    return out


def enabled(cfg: dict, state: dict | None = None) -> list[dict]:
    """The sources that run this time (configured, and due by min_hours_between)."""
    return [s for s in _sources(cfg) if _due(state, s["id"], s["cfg"].get("min_hours_between", 0))]


def configured(cfg: dict) -> set[str]:
    """Ids of every source config turns on, due or not. The dashboard export
    uses it so a source switched off in config (JSearch until its key is
    subscribed) keeps its history in state but stops showing as "failing"."""
    return {s["id"] for s in _sources(cfg)}
