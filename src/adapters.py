"""
Job-board adapters.

Same design as the IB tracker: no per-employer scrapers. Almost every employer
rents an applicant tracking system (ATS), and most of those expose structured
JSON. One adapter per platform, one row per employer in employers.yaml.

Adapters only fetch and normalise. They never classify, filter by location, or
decide what is interesting; that is classify.py's job. Keeping that boundary is
what makes the pipeline testable without a network.

`extra["employment"]` carries the board's own full-time / part-time field, as
the board words it, wherever the listing payload has one (checked against live
boards, 21 Oct 2026): Lever, Ashby, SmartRecruiters, Workable, BambooHR,
Recruitee, Breezy, UKG, Oracle. Greenhouse doesn't say, so the title and
description decide there (classify.employment()). Workday's job list doesn't
either, nor does it carry a description or every location ("3 Locations"):
its postings carry `extra["detail_url"]`, and crawl.py reads that page
(workday_detail()) for the relevant ones.

Platforms:
  greenhouse, lever, ashby, smartrecruiters, workday     (ported from the IB tracker)
  workable, bamboohr, recruitee, breezy                  (common at small media companies)
Aggregators (search across many employers) are in aggregators.py.
"""

from __future__ import annotations

import html as _html
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RawPosting:
    source_id: str           # employer id, or aggregator id
    job_id: str              # the platform's own id; stable across URL changes
    title: str
    company: str             # employer display name
    location_raw: str
    url: str
    description: str = ""
    posted_at: str | None = None
    # e.g. {"workplace": "remote", "employment": "Full-time", "via": "Adzuna"}
    extra: dict[str, Any] = field(default_factory=dict)


class AdapterError(RuntimeError):
    """A fetch failed in a way the crawl should record, not crash on."""


def _get(client: httpx.Client, url: str, *, retries: int = 2, **kw) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = client.get(url, **kw)
            if r.status_code == 404:
                raise AdapterError(f"404 (board not found) {url}")
            if r.status_code in (401, 403):
                raise AdapterError(f"{r.status_code} (blocked or private) {url}")
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except AdapterError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise AdapterError(f"GET failed {url}: {last}")


def _post(client: httpx.Client, url: str, json: dict, *, retries: int = 2) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = client.post(url, json=json)
            if r.status_code == 404:
                raise AdapterError(f"404 (board not found) {url}")
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except AdapterError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise AdapterError(f"POST failed {url}: {last}")


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", _html.unescape(_html.unescape(s))).strip()


def _join(*parts) -> str:
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


# --- Greenhouse --------------------------------------------------------------
# token = the slug in job-boards.greenhouse.io/<token>

def fetch_greenhouse(client, emp: dict) -> list[RawPosting]:
    token = emp["ats_token"]
    data = _get(client, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true").json()
    out = []
    for j in data.get("jobs", []):
        offices = ", ".join(o.get("name", "") for o in (j.get("offices") or []) if o.get("name"))
        loc = (j.get("location") or {}).get("name", "") or offices
        out.append(RawPosting(
            source_id=emp["id"], job_id=str(j.get("id")), title=j.get("title", ""),
            company=emp["name"], location_raw=loc, url=j.get("absolute_url", ""),
            description=strip_html(j.get("content", ""))[:3000], posted_at=j.get("updated_at"),
            extra={"departments": [d.get("name") for d in (j.get("departments") or [])]},
        ))
    return out


# --- Lever -------------------------------------------------------------------
# token = the segment in jobs.lever.co/<token>

def fetch_lever(client, emp: dict) -> list[RawPosting]:
    token = emp["ats_token"]
    data = _get(client, f"https://api.lever.co/v0/postings/{token}?mode=json").json()
    out = []
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        out.append(RawPosting(
            source_id=emp["id"], job_id=str(j.get("id")), title=j.get("text", ""),
            company=emp["name"], location_raw=" / ".join(x for x in locs if x),
            url=j.get("hostedUrl", ""),
            description=strip_html(j.get("descriptionPlain") or j.get("description", ""))[:3000],
            posted_at=None,
            extra={"team": cats.get("team"), "commitment": cats.get("commitment"),
                   "workplace": j.get("workplaceType"),
                   # "Full-time", "Part-time", "Temporary Full Time"...
                   "employment": cats.get("commitment")},
        ))
    return out


# --- Ashby -------------------------------------------------------------------

def fetch_ashby(client, emp: dict) -> list[RawPosting]:
    token = emp["ats_token"]
    data = _get(client, f"https://api.ashbyhq.com/posting-api/job-board/{token}").json()
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location", "") or ""
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = _join(loc, "Remote")
        out.append(RawPosting(
            source_id=emp["id"], job_id=str(j.get("id")), title=j.get("title", ""),
            company=emp["name"], location_raw=loc, url=j.get("jobUrl", ""),
            description=strip_html(j.get("descriptionPlain", ""))[:3000],
            posted_at=j.get("publishedAt"),
            extra={"employment": j.get("employmentType")},      # FullTime / PartTime / Intern / Contract / Temporary
        ))
    return out


# --- SmartRecruiters -----------------------------------------------------------

def fetch_smartrecruiters(client, emp: dict) -> list[RawPosting]:
    token = emp["ats_token"]
    out, seen = [], set()
    for q in (emp.get("queries") or [""]):
        for p in _sr_query(client, emp, token, q):
            if p.job_id not in seen:
                seen.add(p.job_id)
                out.append(p)
    return out


def _sr_query(client, emp: dict, token: str, q: str) -> list[RawPosting]:
    out, offset = [], 0
    while True:
        data = _get(client, f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
                    params={"limit": 100, "offset": offset, **({"q": q} if q else {})}).json()
        content = data.get("content", [])
        if not content:
            break
        for j in content:
            loc = j.get("location") or {}
            where = _join(loc.get("city"), loc.get("region"), loc.get("country", "").upper())
            if loc.get("remote"):
                where = _join(where, "Remote")
            toe = j.get("typeOfEmployment") or {}          # {"id": "permanent", "label": "Full-time"}
            out.append(RawPosting(
                source_id=emp["id"], job_id=str(j.get("id")), title=j.get("name", ""),
                company=emp["name"], location_raw=where,
                url=f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                posted_at=j.get("releasedDate"),
                extra={"employment": toe.get("label") or toe.get("id")},
            ))
        offset += len(content)
        if offset >= data.get("totalFound", 0) or offset > 3000:
            break
    return out


# --- Workday -------------------------------------------------------------------
# Not a documented API, but the career site is a thin client over
#   POST https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs
# Big media groups (NBCUniversal, Paramount, Hearst...) post thousands of jobs,
# so this adapter asks the server to search for each role keyword instead of
# paging through everything. searchText is filtered server-side (verified on
# the IB tracker); a posting whose text has none of these words would be
# dropped by classify() anyway.

WORKDAY_QUERIES = ("editor", "editorial", "writer", "reporter", "journalist", "producer",
                   "content", "social media", "copywriter", "communications",
                   "public relations", "marketing coordinator", "fellow")
# Postings read per search at most (20 a page). A search that hits this is too
# broad for the board: give the row narrower `queries`.
WORKDAY_MAX_PER_QUERY = 300

_WD_REQ = re.compile(r"_([A-Za-z0-9][A-Za-z0-9.-]*?)(?:-\d{1,2})?$")


def workday_job_id(j: dict) -> str:
    """Requisition id from the job path (ported as-is from the IB tracker,
    where reading it from bulletFields made five postings share one row)."""
    path = j.get("externalPath", "") or ""
    m = _WD_REQ.search(path)
    if m and any(ch.isdigit() for ch in m.group(1)):
        return m.group(1)
    bullets = [str(b) for b in (j.get("bulletFields") or []) if b]
    return bullets[0] if bullets else path


def fetch_workday(client, emp: dict) -> list[RawPosting]:
    tenant, site, dc = emp["ats_token"], emp.get("ats_site", ""), emp.get("ats_dc", "wd1")
    if not site:
        raise AdapterError("workday needs ats_site")
    base = emp.get("ats_host") or f"https://{tenant}.{dc}.myworkdayjobs.com"
    endpoint = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    queries = emp.get("queries") or WORKDAY_QUERIES
    seen: dict[str, RawPosting] = {}
    for q in queries:
        offset, total = 0, None
        while True:
            data = _post(client, endpoint, {"appliedFacets": {}, "limit": 20,
                                            "offset": offset, "searchText": q}).json()
            # Workday gives the search's total on the first page only; later
            # pages say 0. Reading it from every page stopped each search after
            # 40 postings (Seminole Hard Rock: 200 read of 576, 21 Oct 2026).
            total = data.get("total") or total
            posts = data.get("jobPostings", [])
            if not posts:
                break
            for j in posts:
                jid = workday_job_id(j)
                if jid in seen:
                    continue
                path = j.get("externalPath", "") or ""
                seen[jid] = RawPosting(
                    source_id=emp["id"], job_id=jid, title=j.get("title", ""),
                    company=emp["name"], location_raw=j.get("locationsText", "") or "",
                    url=(f"{base}/recruiting/{tenant}/{site}{path}"
                         if "myworkdaysite" in base else
                         f"{base}/en-US/{site}{path}"),
                    posted_at=j.get("postedOn"),
                    # The job's own page: description, every location, full-time or not.
                    extra={"detail_url": f"{base}/wday/cxs/{tenant}/{site}{path}"},
                )
            offset += 20
            if ((total is not None and offset >= total) or len(posts) < 20
                    or offset >= WORKDAY_MAX_PER_QUERY):
                break
            time.sleep(0.3)
    return list(seen.values())


def workday_detail(client, url: str) -> dict:
    """One Workday job's own page (the JSON behind it), for what the job list
    leaves out. Shape checked on Chewy's board, 21 Oct 2026: jobPostingInfo has
    location ("Hollywood, FL"), additionalLocations (["Richardson, TX"]),
    timeType ("Full time"), remoteType (often missing; each employer words it:
    "Hybrid", "Remote Position", "In-Office Position") and jobDescription
    (HTML). The description is kept long: the "3+ years" line is usually in
    the qualifications near the end."""
    info = _get(client, url, retries=0, headers={"Accept": "application/json"}).json().get("jobPostingInfo") or {}
    locs = [info.get("location"), *(info.get("additionalLocations") or [])]
    return {"description": strip_html(info.get("jobDescription") or "")[:12000],
            "location_raw": " / ".join(str(x).strip() for x in locs if x and str(x).strip()),
            "employment": info.get("timeType"), "workplace": _workplace(info.get("remoteType"))}


def _workplace(s: str | None) -> str | None:
    """A board's own remote / hybrid / in-office wording -> remote / hybrid /
    onsite (what classify.work_mode() takes), None when it says nothing."""
    t = (s or "").lower()
    if "hybrid" in t:
        return "hybrid"
    if "remote" in t:
        return "remote"
    if re.search(r"\b(in[- ]?office|on[- ]?site|in[- ]person)\b", t):
        return "onsite"
    return None


# --- Workable ------------------------------------------------------------------
# token = the account in apply.workable.com/<token>/
# Public widget endpoint; returns every published job with location fields.

def fetch_workable(client, emp: dict) -> list[RawPosting]:
    """Workable lists a job once per city ("Editorial Coordinator" in Miami and
    again in New York, same shortcode), so rows are merged by shortcode and
    their locations joined. Verified against The Drum's board, 19 Oct 2026."""
    token = emp["ats_token"]
    data = _get(client, f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true").json()
    merged: dict[str, RawPosting] = {}
    for j in data.get("jobs", []):
        where = _join(j.get("city"), j.get("state"), j.get("country"))
        if j.get("telecommuting"):
            where = _join(where, "Remote")
        sc = str(j.get("shortcode") or j.get("id"))
        if sc in merged:
            if where and where not in merged[sc].location_raw:
                merged[sc].location_raw += f" / {where}"
            continue
        merged[sc] = RawPosting(
            source_id=emp["id"], job_id=sc, title=j.get("title", ""),
            company=emp["name"], location_raw=where,
            url=j.get("url") or j.get("application_url") or f"https://apply.workable.com/{token}/j/{sc}/",
            description=strip_html(j.get("description", ""))[:3000],
            posted_at=j.get("published_on") or j.get("created_at"),
            extra={"employment": j.get("employment_type")},     # "Full-time", "Contract"...
        )
    return list(merged.values())


# --- BambooHR ------------------------------------------------------------------
# token = the subdomain in <token>.bamboohr.com/careers

def fetch_bamboohr(client, emp: dict) -> list[RawPosting]:
    token = emp["ats_token"]
    data = _get(client, f"https://{token}.bamboohr.com/careers/list",
                headers={"Accept": "application/json"}).json()
    out = []
    for j in data.get("result", []):
        loc = j.get("location") or {}
        where = _join(loc.get("city"), loc.get("state"))
        if j.get("isRemote") or (j.get("locationType") == "1"):
            where = _join(where, "Remote")
        out.append(RawPosting(
            source_id=emp["id"], job_id=str(j.get("id")), title=j.get("jobOpeningName", ""),
            company=emp["name"], location_raw=where,
            url=f"https://{token}.bamboohr.com/careers/{j.get('id')}",
            extra={"department": j.get("departmentLabel"), "type": j.get("employmentStatusLabel"),
                   "employment": j.get("employmentStatusLabel")},  # "Full-Time", "Part-Time", "Contractor"...
        ))
    return out


# --- Recruitee -----------------------------------------------------------------

def fetch_recruitee(client, emp: dict) -> list[RawPosting]:
    token = emp["ats_token"]
    data = _get(client, f"https://{token}.recruitee.com/api/offers/").json()
    out = []
    for j in data.get("offers", []):
        where = j.get("location") or _join(j.get("city"), j.get("state_code"), j.get("country_code"))
        if j.get("remote"):
            where = _join(where, "Remote")
        out.append(RawPosting(
            source_id=emp["id"], job_id=str(j.get("id")), title=j.get("title", ""),
            company=emp["name"], location_raw=where, url=j.get("careers_url", ""),
            description=strip_html(j.get("description", ""))[:3000],
            posted_at=j.get("published_at"),
            extra={"employment": j.get("employment_type_code")},  # "fulltime_permanent", "fulltime_fixed_term"...
        ))
    return out


# --- Breezy HR -----------------------------------------------------------------

def fetch_breezy(client, emp: dict) -> list[RawPosting]:
    token = emp["ats_token"]
    data = _get(client, f"https://{token}.breezy.hr/json").json()
    out = []
    for j in data if isinstance(data, list) else []:
        loc = j.get("location") or {}
        state = loc.get("state") or {}
        country = loc.get("country") or {}
        where = loc.get("name") or _join(loc.get("city"),
                                         state.get("name") if isinstance(state, dict) else state,
                                         country.get("name") if isinstance(country, dict) else country)
        if loc.get("is_remote"):
            where = _join(where, "Remote")
        typ = j.get("type")                                # {"id": "fullTime", "name": "Full-Time"}
        out.append(RawPosting(
            source_id=emp["id"], job_id=str(j.get("id")), title=j.get("name", ""),
            company=emp["name"], location_raw=where, url=j.get("url", ""),
            posted_at=j.get("published_date"),
            extra={"employment": typ.get("name") if isinstance(typ, dict) else typ},
        ))
    return out


# --- Oracle HCM Cloud ("Candidate Experience") -------------------------------------
# Ported from the IB tracker. JPMorgan Chase (The Infatuation's parent) and
# Hearst post here. These boards hold thousands of jobs, so the server is asked
# to search: `queries` in employers.yaml (e.g. ["infatuation"]). Verified on
# the IB tracker that the keyword filter is applied server-side.
#   token = the host (jpmc.fa.oraclecloud.com), ats_site = the siteNumber (CX_1001)

def fetch_oracle(client, emp: dict) -> list[RawPosting]:
    host, site = emp["ats_token"], emp.get("ats_site", "")
    if not site:
        raise AdapterError("oracle needs ats_site (the siteNumber)")
    api = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    seen: dict[str, RawPosting] = {}
    for kw in (emp.get("queries") or WORKDAY_QUERIES):
        offset = 0
        for _ in range(10):
            finder = (f"findReqs;siteNumber={site},keyword={kw},limit=50,offset={offset},"
                      f"sortBy=POSTING_DATES_DESC")
            r = _get(client, api, params={"onlyData": "true", "expand": "requisitionList",
                                          "finder": finder}, headers={"Accept": "application/json"})
            items = r.json().get("items") or [{}]
            reqs = items[0].get("requisitionList") or []
            for j in reqs:
                jid = str(j.get("Id"))
                if jid in seen:
                    continue
                seen[jid] = RawPosting(
                    source_id=emp["id"], job_id=jid, title=j.get("Title", "") or "",
                    company=emp["name"], location_raw=j.get("PrimaryLocation", "") or "",
                    url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}",
                    description=strip_html(j.get("ShortDescriptionStr", ""))[:3000],
                    posted_at=j.get("PostedDate"),
                    # JobSchedule ("Full time" / "Part time") is in every row
                    # but empty on JPMorgan's and Hearst's boards (21 Oct 2026);
                    # read anyway for a tenant that fills it.
                    extra={"organization": j.get("Organization"), "employment": j.get("JobSchedule")})
            offset += len(reqs)
            if not reqs or offset >= (items[0].get("TotalJobsCount") or 0):
                break
            time.sleep(0.4)
    return list(seen.values())


# --- UKG Pro Recruiting -------------------------------------------------------------
# McClatchy (Miami Herald, el Nuevo Herald) posts here. The job board page is a
# thin client over a JSON POST. token = the host path up to the board id:
#   https://mcclatchy.rec.pro.ukg.net/MCC1008MCLTC/JobBoard/<guid>

def fetch_ukg(client, emp: dict) -> list[RawPosting]:
    board = emp["ats_token"].rstrip("/")
    out, skip = [], 0
    while True:
        r = _post(client, f"{board}/JobBoardView/LoadSearchResults", {
            "opportunitySearch": {"Top": 50, "Skip": skip, "QueryString": "",
                                  "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate",
                                               "Ascending": False}], "Filters": []},
            "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [],
                              "Skills": [], "hasNoLicenses": False, "SkippedSkills": []}})
        data = r.json()
        opps = data.get("opportunities") or []
        for j in opps:
            locs = []
            for l in j.get("Locations") or []:
                a = l.get("Address") or {}
                st = a.get("State") or {}
                locs.append(_join(a.get("City"), st.get("Code") if isinstance(st, dict) else st)
                            or l.get("LocalizedDescription") or "")
            jid = str(j.get("Id"))
            # FullTime is a yes/no; on McClatchy's board every "no" is a
            # part-time or freelance job ("Merchandiser - PT").
            ft = j.get("FullTime")
            out.append(RawPosting(
                source_id=emp["id"], job_id=jid, title=j.get("Title", ""), company=emp["name"],
                location_raw=" / ".join(x for x in locs if x),
                url=f"{board}/OpportunityDetail?opportunityId={jid}",
                description=strip_html(j.get("BriefDescription", ""))[:3000],
                posted_at=j.get("PostedDate"),
                extra={"employment": None if ft is None else ("Full-time" if ft else "Part-time")}))
        skip += len(opps)
        if not opps or skip >= (data.get("totalCount") or 0) or skip > 1000:
            break
    return out


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "workable": fetch_workable,
    "bamboohr": fetch_bamboohr,
    "recruitee": fetch_recruitee,
    "breezy": fetch_breezy,
    "oracle": fetch_oracle,
    "ukg": fetch_ukg,
}

# Platforms whose job list leaves out what classify() needs, and the function
# that reads one job's own page (crawl.py calls it for relevant postings that
# carry extra["detail_url"]).
DETAILS = {"workday": workday_detail}

# Platforms with no adapter. Those employers are listed on the dashboard's
# "Who I’m watching" page with a link (and their careers page is watched for
# changes) instead of silently missing. Every `ats:` in employers.yaml is
# either here or in ADAPTERS (a test checks), so a typo can't hide a row.
MANUAL = {"none", "unknown", "bespoke", "linkedin", "icims", "adp", "paylocity",
          "taleo", "successfactors", "dayforce", "jazzhr", "email", "avature", "teamtailor",
          "jobvite", "eightfold", "phenom", "xanterra", "prismhr", "hrmdirect", "ukg-ready",
          "trinet", "peoplesoft", "teamworkonline"}


def detect_from_url(url: str) -> dict:
    """Read the platform and board id off a careers URL. Used by
    scripts/add_employer.py so adding an employer is pasting a link."""
    u = (url or "").strip()
    pats = [
        (r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", "greenhouse"),
        (r"jobs\.lever\.co/([\w.-]+)", "lever"),
        (r"jobs\.ashbyhq\.com/([\w.-]+)", "ashby"),
        (r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", "smartrecruiters"),
        (r"apply\.workable\.com/([\w-]+)", "workable"),
        (r"([\w-]+)\.bamboohr\.com", "bamboohr"),
        (r"([\w-]+)\.recruitee\.com", "recruitee"),
        (r"([\w-]+)\.breezy\.hr", "breezy"),
        (r"([\w-]+)\.applytojob\.com", "jazzhr"),
        (r"([\w-]+)\.icims\.com", "icims"),
    ]
    for rx, ats in pats:
        m = re.search(rx, u, re.I)
        if m:
            return {"ats": ats, "ats_token": m.group(1)}
    m = re.search(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", u)
    if m:
        return {"ats": "workday", "ats_token": m.group(1), "ats_dc": m.group(2), "ats_site": m.group(3)}
    return {"ats": "none"}
