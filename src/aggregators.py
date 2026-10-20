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
  RSS      We Work Remotely asks for attribution; NAHJ feed is public.
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
                    posted_at=j.get("created"), extra={"via": "Adzuna"}))
            if len(res) < 50:
                break
            time.sleep(2.5)          # 25 calls/minute limit
    return out


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
            out.append(RawPosting(
                source_id="jsearch", job_id=str(j.get("job_id")), title=j.get("job_title", ""),
                company=j.get("employer_name", ""), location_raw=loc,
                url=j.get("job_apply_link") or j.get("job_google_link", ""),
                description=(j.get("job_description") or "")[:3000],
                posted_at=j.get("job_posted_at_datetime_utc"),
                extra={"via": f"JSearch / {j.get('job_publisher') or 'Google Jobs'}"}))
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
                posted_at=j.get("publication_date"), extra={"via": "Remotive"}))
        time.sleep(31)               # they block more than 2 calls a minute
    return out


# --- Generic RSS ----------------------------------------------------------------

def fetch_rss(client, feed: dict) -> list[RawPosting]:
    r = _get(client, feed["url"], headers={"Accept": "application/rss+xml, application/xml, text/xml"})
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        raise AdapterError(f"not an RSS feed ({e})") from e
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link or title).strip()
        company = ""
        if feed.get("title_format") == "company: title" and ":" in title:
            company, title = (x.strip() for x in title.split(":", 1))
        desc = strip_html(it.findtext("description") or "")
        loc = it.findtext("region") or it.findtext("location") or feed.get("default_location", "")
        if not loc:
            m = re.search(r"\b(?:Location|Headquarters)\s*:?\s*([A-Z][\w .'-]+,\s*[A-Z]{2})\b", desc)
            loc = m.group(1) if m else ""
        out.append(RawPosting(
            source_id=f"rss_{feed['id']}", job_id=guid, title=title, company=company,
            location_raw=loc, url=link, description=desc[:3000],
            posted_at=it.findtext("pubDate"), extra={"via": feed["name"]}))
    return out


# --- Which sources run this time ------------------------------------------------

def _due(state: dict, sid: str, hours: float) -> bool:
    s = (state or {}).get("sources", {}).get(sid) or {}
    return store.days_since(s.get("last_ok")) * 24 >= (hours or 0)


def enabled(cfg: dict, state: dict | None = None) -> list[dict]:
    a = cfg.get("aggregators") or {}
    out = []

    def add(sid, name, fn, c):
        if _due(state, sid, c.get("min_hours_between", 0)):
            out.append({"id": sid, "name": name, "fetch": fn, "cfg": c})

    c = a.get("adzuna") or {}
    if c.get("enabled") and os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"):
        add("adzuna", "Adzuna (job search)", fetch_adzuna, c)
    c = a.get("jsearch") or {}
    if c.get("enabled") and os.environ.get("JSEARCH_RAPIDAPI_KEY"):
        add("jsearch", "JSearch (Google for Jobs)", fetch_jsearch, c)
    c = a.get("remotive") or {}
    if c.get("enabled"):
        add("remotive", "Remotive (remote jobs)", fetch_remotive, c)
    c = a.get("rss") or {}
    if c.get("enabled"):
        for f in c.get("feeds", []):
            add(f"rss_{f['id']}", f["name"], fetch_rss, {**f, "min_hours_between": c.get("min_hours_between", 0)})
    return out
