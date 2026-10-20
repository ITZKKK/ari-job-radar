"""
The crawl: read every source, update state, write the dashboard's data file.

    python src/crawl.py                    full run
    python src/crawl.py --only "vox"       one employer (substring of name or id)
    python src/crawl.py --dry-run          crawl and report, write nothing

State machine per posting (carried over from the IB tracker):

    unseen -> open -> closed

first_seen is OUR clock, not the employer's posting date (most boards omit it
or report it unreliably), and the dashboard labels it "first seen".

A posting is marked closed only when its source was read successfully and the
posting was not in it. Aggregator searches are different: a posting can fall
off a keyword search while still open, so those are only marked closed after
`aggregator_close_days` unseen.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time

import httpx
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import adapters, aggregators, inbox, store, watch  # noqa: E402
from src.classify import Rules, classify, fit, remote_in_head, visible, work_mode  # noqa: E402

ROOT = store.ROOT
AGGREGATOR_CLOSE_DAYS = 7


def load_yaml(name: str) -> dict:
    with open(os.path.join(ROOT, name)) as f:
        return yaml.safe_load(f)


def load_employers() -> list[dict]:
    """employers.yaml (kept by hand) plus data/inbox_employers.yaml (written by
    the crawl from the dashboard's inbox). On the same id the hand row wins."""
    hand = load_yaml("employers.yaml")["employers"]
    ids = {e["id"] for e in hand}
    extra = []
    if os.path.exists(inbox.INBOX_EMPLOYERS):
        with open(inbox.INBOX_EMPLOYERS) as f:
            extra = [e for e in ((yaml.safe_load(f) or {}).get("employers") or []) if e.get("id") not in ids]
    return hand + extra


def pid_for(source_id: str, job_id: str) -> str:
    """Stable id from source + the platform's own job id. Deliberately not the
    URL: URLs churn when a board re-indexes and every job would look new."""
    return hashlib.sha1(f"{source_id}::{job_id}".encode()).hexdigest()[:16]


_CO_NOISE = re.compile(r"\b(the|inc|llc|ltd|co|corp|corporation|company|group|media|holdings|"
                       r"llp|plc|network|networks|digital)\b|[^a-z0-9 ]", re.I)


def norm_company(s: str) -> str:
    return " ".join(_CO_NOISE.sub(" ", (s or "").lower()).split())


def norm_title(s: str) -> str:
    s = re.sub(r"\(.*?\)|-.*$|,.*$|\|.*$", "", (s or "").lower())
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


class Progress:
    """Percentage and time remaining on every line (the owner's standing
    preference for long-running scripts)."""

    def __init__(self, total: int):
        self.total, self.done, self.t0 = max(total, 1), 0, time.time()

    def step(self, msg: str) -> None:
        self.done += 1
        el = time.time() - self.t0
        eta = el / self.done * (self.total - self.done)
        pct = 100 * self.done / self.total
        print(f"[{pct:5.1f}% · ~{int(eta // 60)}m{int(eta % 60):02d}s left] {msg}", flush=True)


def employer_matcher(employers: list[dict]):
    """Map a company name seen on an aggregator to a target employer id, via
    the employer's name and aliases. Lets aggregator finds at Ari's targets
    carry the star, and lets duplicates collapse."""
    table = {}
    for e in employers:
        for n in [e["name"], *(e.get("aliases") or [])]:
            k = norm_company(n)
            if k:
                table[k] = e["id"]

    def match(company: str) -> str | None:
        return table.get(norm_company(company))
    return match


def run(only: str | None = None, dry_run: bool = False, state_path: str = store.STATE_PATH,
        radar_path: str = store.RADAR_PATH, fetchers=None) -> dict:
    cfg = load_yaml("config.yaml")
    employers = load_employers()
    boards = (load_yaml("boards.yaml") or {}).get("boards", []) if os.path.exists(
        os.path.join(ROOT, "boards.yaml")) else []
    rules = Rules(cfg)
    state = store.load(state_path)
    first_ever = not state["postings"]
    ts = store.now_iso()
    t0 = time.time()

    # The dashboard's inbox first, so a company Ari just added is crawled in this
    # very run. Only on Actions (or RADAR_INBOX=1): a laptop run must not eat her
    # notes into a state file that never gets pushed.
    inbox_counts = None
    on_actions = os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("RADAR_INBOX") == "1"
    if not only and not dry_run and on_actions and os.environ.get("RADAR_PASSCODE"):
        hand = load_yaml("employers.yaml")["employers"]
        with httpx.Client(headers={"User-Agent": cfg["crawl"]["user_agent"]},
                          timeout=cfg["crawl"]["timeout_seconds"], follow_redirects=True) as ic:
            inbox_counts = inbox.process(state, ic, cfg, os.environ["RADAR_PASSCODE"], known=hand)
        print(f"inbox: {inbox_counts['received']} notes · {inbox_counts['added']} companies added · "
              f"{inbox_counts['updated']} already listed · {inbox_counts['refresh']} refresh · "
              f"{inbox_counts['rejected']} refused · {inbox_counts['failed']} failed"
              f"{' · ' + inbox_counts['error'] if inbox_counts['error'] else ''}", flush=True)
        if inbox_counts["added"] or inbox_counts["updated"]:
            employers = load_employers()                               # rows were just written

    selected = employers
    if only:
        selected = [e for e in employers if only.lower() in e["name"].lower() or only == e["id"]]

    readable = [e for e in selected
                if e.get("ats") in adapters.ADAPTERS and e.get("verified", False)]
    watched = [e for e in selected if e not in readable and e.get("watch", True)
               and (e.get("watch_url") or e.get("careers_url"))]
    aggs = [] if only else aggregators.enabled(cfg, state)
    fetchers = fetchers or {}

    headers = {"User-Agent": cfg["crawl"]["user_agent"],
               "Accept": "application/json, text/plain, */*"}
    client = httpx.Client(headers=headers, timeout=cfg["crawl"]["timeout_seconds"],
                          follow_redirects=True)
    delay = 1.0 / max(cfg["crawl"]["requests_per_second"], 0.1)
    match_employer = employer_matcher(employers)
    emp_by_id = {e["id"]: e for e in employers}

    total = len(readable) + len(aggs) + (1 if watched else 0)
    prog = Progress(total)
    print(f"Crawl {ts}: {len(readable)} boards, {len(aggs)} aggregators, "
          f"{len(watched)} pages to watch", flush=True)

    counts = {"new_relevant": 0, "closed": 0, "sources_ok": 0, "sources_failed": 0}
    seen_now: dict[str, set] = {}

    def ingest(source_id: str, kind: str, raws: list, emp: dict | None = None) -> tuple[int, int]:
        ids, n_rel, n_new = set(), 0, 0
        for r in raws:
            pid = pid_for(source_id, r.job_id)
            if pid in ids:
                continue
            ids.add(pid)
            emp_id = source_id if kind == "employer" else match_employer(r.company)
            # `home: MIAMI` in employers.yaml: a posting with no location at a
            # Miami-only employer is a Miami job (aggregator finds included).
            home = (emp or emp_by_id.get(emp_id) or {}).get("home")
            v = classify(r.title, r.location_raw, r.description, rules,
                         default_location=home, workplace_hint=r.extra.get("workplace"))
            p = state["postings"].get(pid)
            is_new = p is None
            if is_new:
                p = {"pid": pid, "first_seen": ts, "emailed_at": None}
                state["postings"][pid] = p
            was_closed = p.get("status") == "closed"
            p.update({
                "source_id": source_id, "kind": kind, "employer_id": emp_id,
                "job_id": r.job_id, "title": r.title.strip(), "company": r.company,
                "location_raw": r.location_raw, "url": r.url, "posted_at": r.posted_at,
                "last_seen": ts, "status": "open", "closed_at": None,
                "relevant": v.relevant, "reason": v.reason, "family": v.family,
                "tier": v.tier, "seniority": v.seniority, "location": v.location,
                "years": v.years, "flags": v.flags, "work_mode": v.work_mode, "fit": v.fit,
                "remote_offered": v.remote_offered,
            })
            if was_closed:
                p["reopened_at"] = ts
            if v.relevant:
                n_rel += 1
                if is_new:
                    n_new += 1
        seen_now[source_id] = ids
        return n_rel, n_new

    def record(source_id: str, name: str, kind: str, ats: str, ok: bool, err: str,
               n_total: int, n_rel: int) -> None:
        s = state["sources"].get(source_id, {})
        s.update({"name": name, "kind": kind, "ats": ats, "last_attempt": ts,
                  "ok": ok, "error": err})
        if ok:
            s.update({"last_ok": ts, "count_total": n_total, "count_relevant": n_rel,
                      "consecutive_failures": 0})
            counts["sources_ok"] += 1
        else:
            s["consecutive_failures"] = s.get("consecutive_failures", 0) + 1
            counts["sources_failed"] += 1
        state["sources"][source_id] = s

    # 1. Employer boards ---------------------------------------------------
    for e in readable:
        fn = fetchers.get(e["ats"]) or adapters.ADAPTERS[e["ats"]]
        try:
            raws = fn(client, e)
            if e.get("must_match"):
                # Huge parent boards (JPMorgan for The Infatuation, Michelin for
                # the Guide): keep only postings that mention the brand.
                rx = re.compile(e["must_match"], re.I)
                raws = [r for r in raws if rx.search(" ".join(
                    [r.title, r.description, str(r.extra.get("departments") or ""),
                     str(r.extra.get("organization") or "")]))]
            n_rel, n_new = ingest(e["id"], "employer", raws, e)
            record(e["id"], e["name"], "employer", e["ats"], True, "", len(raws), n_rel)
            counts["new_relevant"] += n_new
            prog.step(f"{e['name']}: {len(raws)} jobs, {n_rel} relevant, {n_new} new")
        except Exception as ex:  # noqa: BLE001  one board never stops the run
            msg = str(ex) if isinstance(ex, adapters.AdapterError) else f"{type(ex).__name__}: {ex}"
            record(e["id"], e["name"], "employer", e["ats"], False, msg[:300], 0, 0)
            prog.step(f"{e['name']}: FAILED {msg[:120]}")
        time.sleep(delay)

    # 2. Aggregators -------------------------------------------------------
    for a in aggs:
        fn = fetchers.get(a["id"]) or a["fetch"]
        try:
            raws = fn(client, a["cfg"])
            n_rel, n_new = ingest(a["id"], "aggregator", raws)
            record(a["id"], a["name"], "aggregator", a["id"], True, "", len(raws), n_rel)
            counts["new_relevant"] += n_new
            prog.step(f"{a['name']}: {len(raws)} jobs, {n_rel} relevant, {n_new} new")
        except Exception as ex:  # noqa: BLE001
            msg = f"{type(ex).__name__}: {ex}"
            record(a["id"], a["name"], "aggregator", a["id"], False, msg[:300], 0, 0)
            prog.step(f"{a['name']}: FAILED {msg[:120]}")

    # 3. Careers pages with no board ------------------------------------------
    if watched:
        wc = watch.check(state, client, watched, ts, delay, log=print)
        prog.step(f"careers pages: {wc['watched']} watched, {wc['changed']} changed, "
                  f"{wc['unwatchable']} could not be read")

    # 4. Closures: only for sources read successfully this run -----------------
    for p in state["postings"].values():
        if p.get("status") != "open":
            continue
        src = p.get("source_id")
        if src not in seen_now:
            continue                       # source failed or not in this run
        if p["pid"] in seen_now[src]:
            continue
        if p.get("kind") == "aggregator" and store.days_since(p.get("last_seen")) < AGGREGATOR_CLOSE_DAYS:
            continue
        p["status"], p["closed_at"] = "closed", ts
        if p.get("relevant"):
            counts["closed"] += 1

    # 5. Duplicates: the same job via an employer board and an aggregator -----
    mark_duplicates(state)

    # 6. Forget old irrelevant / long-closed rows to keep the file small -------
    keep_closed = cfg["crawl"]["keep_closed_days"]
    forget_irrel = cfg["crawl"]["forget_irrelevant_after_days"]
    for pid in list(state["postings"]):
        p = state["postings"][pid]
        if p.get("status") == "closed" and store.days_since(p.get("closed_at")) > max(keep_closed, 60):
            del state["postings"][pid]
        elif not p.get("relevant") and store.days_since(p.get("last_seen")) > forget_irrel:
            del state["postings"][pid]

    run_row = {"started_at": ts, "finished_at": store.now_iso(),
               "duration_s": round(time.time() - t0), "first_run": first_ever, **counts}
    if inbox_counts:
        run_row["inbox"] = inbox_counts
    if not dry_run and not only:
        state["runs"] = (state["runs"] + [run_row])[-120:]
    if first_ever:
        # The first crawl is a baseline: everything open "already existed".
        # notify.py sends one welcome email instead of a hundred alerts.
        for p in state["postings"].values():
            p["baseline"] = True

    if dry_run:
        print("Dry run: nothing written.")
    else:
        store.save(state, state_path)
        export(state, cfg, employers, boards, radar_path)

    print(f"Done in {run_row['duration_s']}s · {counts['new_relevant']} new relevant · "
          f"{counts['closed']} closed · {counts['sources_ok']} sources ok, "
          f"{counts['sources_failed']} failed", flush=True)
    write_summary(state, counts, run_row)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out and not dry_run:
        with open(gh_out, "a") as f:
            f.write(f"new_count={counts['new_relevant']}\n")
    return run_row


def write_summary(state: dict, counts: dict, run_row: dict) -> None:
    """Health report on the GitHub Actions run page (for whoever maintains the
    radar). Ari's emails never carry maintenance noise."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    bad = sorted(((s.get("name"), s.get("consecutive_failures"), s.get("error", ""))
                  for s in state["sources"].values() if not s.get("ok")), key=lambda x: -x[1])
    lines = [f"### Job Radar crawl · {run_row['started_at']}",
             f"{counts['new_relevant']} new relevant · {counts['closed']} closed · "
             f"{counts['sources_ok']} sources read · {counts['sources_failed']} failed · "
             f"{run_row['duration_s']}s", ""]
    if bad:
        lines += ["| Source | Failed runs in a row | Last error |", "|---|---|---|"]
        lines += [f"| {n} | {k} | {str(e)[:140].replace('|', '/')} |" for n, k, e in bad]
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


def export_only() -> None:
    cfg = load_yaml("config.yaml")
    employers = load_employers()
    boards = (load_yaml("boards.yaml") or {}).get("boards", [])
    export(store.load(), cfg, employers, boards, store.RADAR_PATH)
    print("dashboard data re-exported (no crawl)")


def mark_duplicates(state: dict) -> None:
    groups: dict[str, list[dict]] = {}
    for p in state["postings"].values():
        p.pop("dupe_of", None)
        if p.get("status") != "open" or not p.get("relevant"):
            continue
        who = p.get("employer_id") or norm_company(p.get("company", ""))
        key = f"{who}|{norm_title(p.get('title'))}|{p.get('location')}"
        groups.setdefault(key, []).append(p)
    for items in groups.values():
        if len(items) < 2:
            continue
        items.sort(key=lambda p: (p.get("kind") != "employer", p.get("first_seen") or ""))
        primary = items[0]
        for p in items[1:]:
            p["dupe_of"] = primary["pid"]
            # If the duplicate was already emailed, don't email the primary again.
            if p.get("emailed_at") and not primary.get("emailed_at"):
                primary["emailed_at"] = p["emailed_at"]


def export(state: dict, cfg: dict, employers: list[dict], boards: list[dict], path: str) -> None:
    """Write data/radar.json — everything the dashboard page shows, and nothing
    it doesn't (no irrelevant postings, no descriptions, nothing outside
    Miami / fully remote)."""
    keep_closed = cfg["crawl"]["keep_closed_days"]
    rules = Rules(cfg)
    loc = cfg["locations"]
    posts = []
    for p in state["postings"].values():
        if not p.get("relevant") or p.get("dupe_of"):
            continue
        if p.get("status") == "closed" and store.days_since(p.get("closed_at")) > keep_closed:
            continue
        row = {k: p.get(k) for k in (
            "pid", "title", "company", "employer_id", "kind", "source_id", "location",
            "location_raw", "url", "first_seen", "last_seen", "status", "closed_at",
            "family", "tier", "seniority", "years", "flags", "posted_at",
            "work_mode", "fit", "remote_offered")}
        # Rows written before these fields existed (closed ones are never
        # re-read): fill them from what state has. Descriptions aren't kept,
        # so the work mode comes from the head alone.
        row["work_mode"] = row["work_mode"] or work_mode(row["location_raw"], row["title"], "")
        if row["remote_offered"] is None:
            row["remote_offered"] = remote_in_head(row["location_raw"], row["title"])
        row["fit"] = row["fit"] or fit(row["seniority"], row["years"], row["title"], "", rules)
        posts.append(row)
    targets = {e["id"]: e for e in employers if e.get("target")}
    for p in posts:
        e = targets.get(p["employer_id"])
        p["target"] = bool(e) and (not e.get("target_match") or bool(
            re.search(e["target_match"], p["title"] if p["kind"] == "employer"
                      else f"{p['title']} {p['company']}", re.I)))
    posts.sort(key=lambda p: p["first_seen"] or "", reverse=True)

    # What Ari sees: Miami, fully remote, or "<city> or Remote". The rest is
    # counted (so the Stats tab can say how much is elsewhere) but not shown.
    shown = [p for p in posts if visible(p["location"], p["work_mode"], loc, p["remote_offered"])]
    open_posts = [p for p in posts if p["status"] == "open"]
    by_loc: dict[str, int] = {}
    for p in open_posts:
        by_loc[p["location"]] = by_loc.get(p["location"], 0) + 1
    totals = {"open_all": len(open_posts),
              "open_shown": sum(1 for p in shown if p["status"] == "open"),
              "by_location_all": dict(sorted(by_loc.items(), key=lambda kv: -kv[1]))}
    for p in shown:
        if p["location"] not in loc["show"]:
            p["location"] = "REMOTE"         # exported copy only; state keeps the true bucket

    emp_out = []
    for e in employers:
        s = state["sources"].get(e["id"], {})
        w = state["watch"].get(e["id"])
        if e.get("ats") in adapters.ADAPTERS and e.get("verified"):
            mode = "read" if s.get("ok") else ("failing" if s else "pending")
        elif w:
            mode = "watched" if w.get("status") == "ok" else "check"
        else:
            mode = "check"
        emp_out.append({
            "id": e["id"], "name": e["name"], "group": e.get("group"),
            "target": bool(e.get("target")), "city": e.get("city"),
            "careers_url": e.get("careers_url"), "linkedin_url": e.get("linkedin_url"),
            # So the page can tell "In the radar" for a company she added herself.
            "inbox_id": e.get("inbox_id"), "aliases": [str(a) for a in (e.get("aliases") or [])],
            "ats": e.get("ats"), "mode": mode,
            "note": e.get("note"), "open_relevant": sum(
                1 for p in shown if p["employer_id"] == e["id"] and p["status"] == "open"),
            "board_total": s.get("count_total"), "error": s.get("error") if not s.get("ok") else "",
            "failures": s.get("consecutive_failures", 0),
            "page": None if not w else {k: w.get(k) for k in (
                "status", "changed_at", "added", "checked_at", "url", "error")},
        })

    aggs_out = [{"id": sid, **{k: s.get(k) for k in (
        "name", "ok", "error", "last_ok", "count_total", "count_relevant", "consecutive_failures")}}
        for sid, s in state["sources"].items() if s.get("kind") == "aggregator"]
    via = {sid: s.get("name") for sid, s in state["sources"].items()}
    for p in posts:
        if p["kind"] == "aggregator":
            p["via"] = via.get(p["source_id"], p["source_id"])

    labels = {b["id"]: b["label"] for b in loc["buckets"]}
    labels.update({"OTHER_US": "Other US", "UNKNOWN": "Location not listed"})
    store.write_json({
        "generated_at": store.now_iso(),
        "owner": cfg["owner"]["name"],
        "last_run": state["runs"][-1] if state["runs"] else None,
        "runs": state["runs"][-30:],
        "locations": [{"id": b, "label": labels.get(b, b), "email": b in loc["email"]}
                      for b in loc["show"]],
        "work_modes": [{"id": "remote", "label": "Remote"}, {"id": "hybrid", "label": "Hybrid"},
                       {"id": "onsite", "label": "In person"}, {"id": "unknown", "label": "Not stated"}],
        "fits": [{"id": "early", "label": "Early career"},
                 {"id": "open", "label": "Experience not stated"},
                 {"id": "stretch", "label": "Stretch (3–4 years, or a manager role)"}],
        "families": [{"id": f["id"], "label": f["label"], "tier": f["tier"]}
                     for f in cfg["roles"]["families"]],
        "inbox": {"url": inbox.topic_url(cfg), **inbox.public(state)} if inbox.topic_url(cfg) else None,
        "totals": totals,
        "postings": shown,
        "employers": emp_out,
        "aggregators": aggs_out,
        "boards": boards,
    }, path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--export-only", action="store_true",
                    help="rebuild data/radar.json from saved state after editing the YAML files")
    a = ap.parse_args()
    from src.env import load_env
    load_env()
    if a.export_only:
        export_only()
    else:
        run(only=a.only, dry_run=a.dry_run)
