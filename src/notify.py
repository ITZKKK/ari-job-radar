"""
Email digest of new matching jobs.

    python src/notify.py            send what is new since the last email
    python src/notify.py --test     send a test email now
    python src/notify.py --preview  write data/email_preview.html, send nothing

With `alerts.digest: daily` (Ari asked for one summary a morning) the crawl
still runs three times a day, but only the first run after
`alerts.send_after_local_hour` in her time zone sends, once a day; the other
runs print why they didn't and leave the new jobs for tomorrow's summary.
RADAR_EMAIL_NOW=1 (the "email_now" switch on a manual run) sends anyway.

Two ways to send; the first one configured wins:

1. Resend (recommended). Secrets: RESEND_API_KEY, ALERT_TO, optional RESEND_FROM.
   A Resend key can only send email, so if it ever leaked the worst case is spam,
   not a readable inbox. Without a verified domain Resend's sender
   (onboarding@resend.dev) only delivers to the address that owns the Resend
   account — which is why Ari creates the account with her own email.

2. SMTP fallback. Secrets: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_TO.
   Use a dedicated, throwaway Gmail with an App Password, never a personal one:
   an App Password also unlocks IMAP, i.e. the whole mailbox.

No credentials = no email, and the run still succeeds.
"""

from __future__ import annotations

import datetime as dt
import html
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import httpx
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import store  # noqa: E402
from src.classify import visible  # noqa: E402

ROOT = store.ROOT
# Never email something first seen more than this long ago: if email was off
# for a week and is switched on, Ari gets today's news, not a backlog.
MAX_AGE_DAYS = 3

INK, MUTED, LINE, ACCENT, SUN, PAPER = "#22264A", "#5F6487", "#EDE4DE", "#E2577C", "#D9930F", "#FBF6F2"


def site_url(repo: str | None = None) -> str:
    """The dashboard's address: SITE_URL if set, else the Pages URL of the
    repo (GITHUB_REPOSITORY on Actions, or config's github.repo)."""
    u = os.environ.get("SITE_URL", "").strip()
    if u:
        return u
    repo = os.environ.get("GITHUB_REPOSITORY") or repo or ""
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def emailable(p: dict, cfg: dict) -> bool:
    """Same rule as the dashboard (classify.visible), against the email list:
    Miami / fully remote, and full-time (or not stated). notify.py reads
    state, where `location` is still the true bucket."""
    return visible(p.get("location"), p.get("work_mode") or "unknown",
                   {"show": cfg["locations"]["email"]}, bool(p.get("remote_offered")),
                   employment=p.get("employment"), emp_cfg=cfg.get("employment"))


def local_now(cfg: dict, now_utc: dt.datetime) -> dt.datetime:
    """`now_utc` on Ari's clock (owner.timezone)."""
    return now_utc.astimezone(ZoneInfo(cfg["owner"].get("timezone") or "UTC"))


def digest_due(state: dict, cfg: dict, now_utc: dt.datetime, force: bool | None = None) -> tuple[bool, str]:
    """Should this run send the email? (yes/no, why in words). Pure apart from
    reading RADAR_EMAIL_NOW when `force` isn't given, so tests pass the clock.
    Daily digest: not before send_after_local_hour on her clock, and once per
    local day (state["last_digest_day"])."""
    if force is None:
        force = os.environ.get("RADAR_EMAIL_NOW", "").strip() == "1"
    if force:
        return True, "sending now: asked for with email_now"
    a = cfg["alerts"]
    if a.get("digest", "each_run") != "daily":
        return True, "an email after every crawl that finds something (alerts.digest)"
    local = local_now(cfg, now_utc)
    hour = int(a.get("send_after_local_hour", 6))
    if local.hour < hour:
        return False, (f"too early for the morning summary: {local:%H:%M} in {cfg['owner'].get('timezone')}, "
                       f"it goes out after {hour}:00")
    if state.get("last_digest_day") == local.date().isoformat():
        return False, f"already sent today's morning summary ({local.date().isoformat()})"
    return True, f"morning summary due ({local:%Y-%m-%d %H:%M} local)"


def eff_loc(p: dict, cfg: dict) -> str | None:
    """The bucket the email sorts by: a "New York or Remote" job counts as REMOTE."""
    loc = p.get("location")
    if loc in cfg["locations"]["email"]:
        return loc
    return "REMOTE" if p.get("remote_offered") else loc


def pending(state: dict, cfg: dict) -> list[dict]:
    tiers = set(cfg["alerts"]["tiers"])
    out = []
    for p in state["postings"].values():
        if not p.get("relevant") or p.get("status") != "open" or p.get("dupe_of"):
            continue
        if p.get("emailed_at") or p.get("baseline"):
            continue
        if store.days_since(p.get("first_seen")) > MAX_AGE_DAYS:
            continue
        if not emailable(p, cfg) or p.get("tier") not in tiers:
            continue
        out.append(p)
    return out


def page_changes(state: dict, employers: dict) -> list[dict]:
    out = []
    for eid, w in state["watch"].items():
        if w.get("changed_at") and not w.get("emailed_change") == w.get("changed_at"):
            out.append({"id": eid, "name": employers.get(eid, {}).get("name", eid),
                        "url": w.get("url"), "added": w.get("added") or [], "changed_at": w["changed_at"]})
    return out


def failing(state: dict, n: int) -> list[dict]:
    return [{"name": s.get("name"), "error": s.get("error"), "n": s.get("consecutive_failures")}
            for s in state["sources"].values() if s.get("consecutive_failures", 0) >= n
            and s.get("consecutive_failures", 0) % n == 0]   # remind every n failures, not every run


def split_title(p: dict) -> tuple[str, str]:
    """"Billboard: Staff Writer" -> ("Billboard", "Staff Writer"); otherwise the
    employer's short name and the title."""
    t = p.get("title") or ""
    company = re.split(r"\s+[—(]", p.get("company") or "")[0].strip()
    m = re.match(r"^([^:]{2,28}):\s+(.+)$", t)
    if m:
        return m.group(1).strip(), m.group(2)
    m = re.match(r"^(.+?)\s+\|\s+(.{2,30})$", t)
    if m:
        return m.group(2).strip(), m.group(1)
    return company, t


def short_place(p: dict, labels: dict) -> str:
    raw = (p.get("location_raw") or "").lower()
    loc = p.get("location")
    if loc == "MIAMI":
        return "Miami or New York" if "new york" in raw else "Miami"
    if loc == "REMOTE":
        return "Remote"
    if p.get("remote_offered"):
        # A city job whose location says "or Remote": say both.
        if "new york" in raw:
            return "Remote or New York"
        if "miami" in raw:
            return "Remote or Miami"
        return "Remote"
    if loc == "NYC":
        return "New York or Miami" if "miami" in raw else "New York"
    if loc == "UNKNOWN":
        return ""
    return labels.get(loc, "") if loc != "OTHER_US" else (p.get("location_raw") or "").split(";")[0].strip()[:40]


def esc(s) -> str:
    return html.escape(str(s or ""))


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_PLACE = {"MIAMI": "Miami", "NYC": "New York", "REMOTE": "Remote"}


def _place(bucket: str | None, labels: dict) -> str:
    return _PLACE.get(bucket or "") or labels.get(bucket, bucket or "")


def build(posts: list[dict], changes: list[dict], broken: list[dict], cfg: dict,
          labels: dict, families: dict, now_utc: dt.datetime | None = None,
          empty_ok: bool = False) -> tuple[str, str] | None:
    """(subject, html), or None when there is nothing to say (and `empty_ok`,
    alerts.send_when_empty, is off). Order: her own companies (★) first, then
    the preferred place before remote, then jobs on her beats, then fit, then
    Spanish roles, then core roles. With alerts.remote_last, remote jobs get
    their own section."""
    if not posts and not changes and not empty_ok:
        return None
    a = cfg["alerts"]
    cap = a["max_per_email"]
    daily = a.get("digest") == "daily"
    pref = cfg["locations"].get("preferred")
    fit_years = int((cfg.get("fit") or {}).get("early_max_years", 2))
    beat_names = {t["id"]: t["label"] for t in (cfg.get("topics") or [])}
    fit_rank = {"early": 0, "open": 1, "stretch": 2}
    posts = sorted(posts, key=lambda p: (not p.get("target"), eff_loc(p, cfg) != pref, not p.get("topics"),
                                         fit_rank.get(p.get("fit"), 1), not p.get("spanish"),
                                         p.get("tier") != "core", p.get("company") or ""))
    shown, more = posts[:cap], max(0, len(posts) - cap)

    # Sections: the preferred place, then each other place in config order.
    order = [pref] + [b for b in cfg["locations"]["email"] if b != pref]

    def place_of(p: dict) -> str:
        b = eff_loc(p, cfg)
        return b if b in order else "REMOTE"            # fully remote is emailable whatever the list says

    def ranked(bs) -> list[str]:
        return sorted(bs, key=lambda b: order.index(b) if b in order else len(order))

    groups: dict[str, list[dict]] = {}
    for p in shown:
        groups.setdefault(place_of(p), []).append(p)
    keys = ranked(groups)
    sectioned = bool(a.get("remote_last")) and pref in groups and len(groups) > 1

    n = len(posts)
    name = cfg["owner"]["name"]
    day = _WEEKDAYS[local_now(cfg, now_utc or dt.datetime.now(dt.timezone.utc)).weekday()]
    subject = f"{a['subject_prefix']} {day}: "
    if n:
        counts: dict[str, int] = {}
        for p in posts:
            counts[place_of(p)] = counts.get(place_of(p), 0) + 1
        parts = [(f"{counts[b]} remote" if b == "REMOTE" else f"{counts[b]} in {_place(b, labels)}")
                 for b in ranked(counts)]
        subject += f"{n} new job{'s' if n != 1 else ''}" + (" — " + ", ".join(parts) if parts else "")
    elif changes:
        subject += "a careers page you follow just changed"
    else:
        subject += "nothing new this morning"

    def row(p: dict) -> str:
        brand, title = split_title(p)
        star = f"<span style='color:{SUN}'>★</span> " if p.get("target") else ""
        where = short_place(p, labels)
        bits = [families.get(p.get("family"), "")]
        if p.get("fit") == "early":
            bits.append("Good fit")
        if p.get("years") is not None and p["years"] > fit_years:
            bits.append(f"Asks for {p['years']}+ years")
        bits += [beat_names[t] for t in (p.get("topics") or []) if t in beat_names]
        if p.get("spanish"):
            bits.append("Spanish")
        if "Fellowship" in (p.get("flags") or []):
            bits.append("Fellowship")
        return (
            f"<tr><td style='padding:16px 0;border-bottom:1px solid {LINE}'>"
            f"<div style='color:{MUTED};font-size:14px;margin:0 0 3px'>{star}"
            f"<b style='color:{INK}'>{esc(brand)}</b>{' · ' + esc(where) if where else ''}</div>"
            f"<a href='{esc(p['url'])}' style='color:{INK};font-weight:700;font-size:18px;line-height:1.3;"
            f"text-decoration:none'>{esc(title)}</a>"
            f"<div style='color:{MUTED};font-size:14px;margin-top:4px'>{esc(' · '.join(x for x in bits if x))}</div>"
            f"</td></tr>")

    def table(items: list[dict]) -> str:
        return f"<table style='border-collapse:collapse;width:100%'>{''.join(row(p) for p in items)}</table>"

    if sectioned:
        jobs_html = ""
        for i, b in enumerate(keys):
            head = f"In {_place(b, labels)}" if b == pref else _place(b, labels)
            sub = (f"<p style='color:{MUTED};margin:0 0 2px;font-size:14px'>You said you’d rather be in "
                   f"{esc(_place(pref, labels))}, so these come after.</p>") if i == 1 else ""
            jobs_html += (f"<h3 style='margin:26px 0 4px;font-size:17px'>{esc(head)}</h3>{sub}"
                          f"{table(groups[b])}")
    else:
        jobs_html = table(shown) if shown else ""

    ch_html = ""
    if changes:
        items = "".join(
            f"<li style='margin:0 0 10px'><a href='{esc(c['url'])}' style='color:{INK}'><b>{esc(c['name'])}</b></a>"
            f"{' — now says: <i>' + esc('; '.join(c['added'][:2])[:200]) + '</i>' if c['added'] else ' updated its careers page.'}</li>"
            for c in changes)
        ch_html = (f"<h3 style='margin:30px 0 6px;font-size:17px'>Careers pages that changed</h3>"
                   f"<p style='color:{MUTED};margin:0 0 10px'>These companies don’t have a proper jobs board, "
                   f"so the radar watches their page for you. Might be worth a look:</p>"
                   f"<ul style='padding-left:18px;margin:0'>{items}</ul>")

    url = site_url()
    link = (f"<p style='margin:28px 0 0'><a href='{url}' style='background:{ACCENT};color:#fff;"
            f"padding:12px 20px;border-radius:999px;text-decoration:none;font-weight:600'>"
            f"Open your radar</a></p>") if url else ""
    since = "since your last summary" if daily else "since your last email"
    if n:
        intro = f"Here’s what turned up {since}."
    elif changes:
        intro = f"No new jobs {since}, but a page you follow changed."
    else:
        intro = f"Nothing new turned up {since}. The radar keeps looking."
    star_note = (f"<span style='color:{SUN}'>★</span> = a company on your list. " if any(p.get("target") for p in shown) else "")
    more_note = f"There are {more} more on your radar." if more else ""
    rhythm = ("The radar checks three times a day and sends you this first thing each morning, "
              "only when something new has come in." if daily else
              "The radar checks three times a day and emails you when something new comes in.")
    body = f"""<html><head><meta charset="utf-8"></head><body style="margin:0;padding:28px 20px;background:{PAPER};
font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:16px;line-height:1.5;color:{INK}">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:18px;padding:28px 26px">
<p style="margin:0 0 6px;font-size:22px;font-weight:700">Hi {esc(name)} 👋</p>
<p style="margin:0 0 6px">{intro}</p>
<p style="color:{MUTED};margin:0 0 6px;font-size:14px">{star_note}{more_note}</p>
{jobs_html}
{ch_html}{link}
<p style="color:{MUTED};font-size:13px;margin-top:28px">{rhythm}</p>
<p style="color:{MUTED};font-size:13px;margin-top:8px">Good luck! LinkedIn and Indeed aren’t searched automatically —
your radar’s “More places to look” page has one-tap searches for those.</p>
</div></body></html>"""
    return subject, body


def open_now(state: dict, cfg: dict, dupes: bool = False) -> list[dict]:
    """Open jobs Ari would see in an email (the welcome counts these)."""
    return [p for p in state["postings"].values() if p.get("relevant") and p.get("status") == "open"
            and (dupes or not p.get("dupe_of")) and emailable(p, cfg)]


def welcome(state: dict, cfg: dict) -> tuple[str, str]:
    open_rel = open_now(state, cfg)
    url = site_url()
    name = cfg["owner"]["name"]
    subject = f"{cfg['alerts']['subject_prefix']} Hi {name}, your job radar is ready ({len(open_rel)} jobs open now)"
    body = f"""<html><head><meta charset="utf-8"></head><body style="margin:0;padding:28px 20px;background:{PAPER};
font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:16px;line-height:1.55;color:{INK}">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:18px;padding:28px 26px">
<p style="margin:0 0 10px;font-size:22px;font-weight:700">Hi {esc(name)} 👋 your job radar is ready.</p>
<p>It just looked through the job pages of the companies on your list, plus a few big job sites,
and found <b>{len(open_rel)}</b> open jobs that fit what you’re looking for. They’re all waiting on your radar.</p>
<p>{"From now on it checks three times a day and emails you one summary first thing each morning when something <b>new</b> has come in."
   if cfg["alerts"].get("digest") == "daily" else
   "From now on it checks three times a day, and you’ll only get an email when something <b>new</b> turns up."}</p>
<p style="color:{MUTED};font-size:15px">Tip: on your phone, open the radar in Safari, tap Share, then “Add to Home Screen”. It’ll sit there like an app.</p>
{f"<p style='margin:24px 0 0'><a href='{url}' style='background:{ACCENT};color:#fff;padding:12px 20px;border-radius:999px;text-decoration:none;font-weight:600'>Open your radar</a></p>" if url else ''}
</div></body></html>"""
    return subject, body


def send(subject: str, body: str) -> bool:
    to = os.environ.get("ALERT_TO", "").strip()
    if not to:
        print("ALERT_TO not set; skipping email.", file=sys.stderr)
        return False
    recipients = [x.strip() for x in to.split(",") if x.strip()]

    key = os.environ.get("RESEND_API_KEY", "").strip()
    if key:
        sender = os.environ.get("RESEND_FROM", "").strip() or "Job Radar <onboarding@resend.dev>"
        r = httpx.post("https://api.resend.com/emails",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"from": sender, "to": recipients, "subject": subject, "html": body},
                       timeout=20)
        if r.status_code >= 400:
            hint = ""
            if r.status_code == 403 and "resend.dev" in sender:
                hint = (" The free sender only delivers to the email that owns the Resend "
                        "account. Make ALERT_TO exactly that address.")
            elif r.status_code == 401:
                hint = " The API key was rejected: create a new one and update the secret."
            print(f"Resend error {r.status_code}.{hint}\n{r.text[:300]}", file=sys.stderr)
            sys.exit(1)
        print(f"sent via Resend -> {', '.join(recipients)}")
        return True

    host = os.environ.get("SMTP_HOST", "").strip()
    if host:
        user, pw = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
        msg = MIMEText(body, "html", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, f"Job Radar <{user}>", ", ".join(recipients)
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(user, recipients, msg.as_string())
        print(f"sent via SMTP -> {', '.join(recipients)}")
        return True

    print("No RESEND_API_KEY or SMTP_HOST; skipping email.", file=sys.stderr)
    return False


def main(argv: list[str], state_path: str = store.STATE_PATH, now_utc: dt.datetime | None = None) -> None:
    """`state_path` and `now_utc` are for tests (a temp file, a fixed clock)."""
    from src.env import load_env
    load_env()
    with open(os.path.join(ROOT, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    if "--test" in argv:
        ok = send(f"{cfg['alerts']['subject_prefix']} Test",
                  f"<p style='font-family:sans-serif'>Delivery works. Real alerts list new jobs.</p>"
                  f"<p style='font-family:sans-serif'>{esc(site_url())}</p>")
        sys.exit(0 if ok else 1)

    with open(os.path.join(ROOT, "employers.yaml")) as f:
        employers = {e["id"]: e for e in yaml.safe_load(f)["employers"]}
    state = store.load(state_path)
    labels = {b["id"]: b["label"] for b in cfg["locations"]["buckets"]}
    labels.update({"OTHER_US": "Other US", "UNKNOWN": "Location not listed"})
    families = {f["id"]: f["label"] for f in cfg["roles"]["families"]}
    ts = store.now_iso()
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    today = local_now(cfg, now).date().isoformat()
    preview = "--preview" in argv
    daily = cfg["alerts"].get("digest") == "daily"

    if not cfg["alerts"].get("enabled", True):
        print("alerts disabled in config.yaml")
        return

    # First run: one welcome email instead of an alert per existing job.
    if not state.get("welcomed") and state["runs"]:
        built = welcome(state, cfg)
        if preview:
            _preview(built)
            return
        if send(*built):
            state["welcomed"] = ts
            # The jobs it counted are "waiting on your radar", not news: the
            # next summary lists only what turns up after the welcome. Like an
            # email_now send, it doesn't use up the morning summary
            # (last_digest_day stays as it was).
            for p in open_now(state, cfg, dupes=True):
                p["emailed_at"] = p.get("emailed_at") or ts
            store.save(state, state_path)
        return

    # Daily digest: is this the run that sends? (--preview always renders.)
    # Only the scheduled summary moves last_digest_day: an email_now send is
    # an extra, so a 2am "send now" can't swallow that morning's summary.
    due, why = digest_due(state, cfg, now)                       # honours RADAR_EMAIL_NOW
    on_schedule = digest_due(state, cfg, now, force=False)[0]
    if not preview:
        print(why)
        if not due:
            return

    posts = pending(state, cfg)
    for p in posts:
        e = employers.get(p.get("employer_id") or "", {})
        p["target"] = bool(e.get("target")) and (not e.get("target_match") or bool(
            re.search(e["target_match"], p.get("title", "") if p.get("kind") == "employer"
                      else f"{p.get('title', '')} {p.get('company', '')}", re.I)))
    changes = page_changes(state, employers) if cfg["alerts"].get("include_page_changes") else []
    # Broken boards are reported on the GitHub run page for the maintainer,
    # not in Ari's inbox: she can't fix them, and noise trains people to stop reading.
    built = build(posts, changes, [], cfg, labels, families, now_utc=now,
                  empty_ok=daily and bool(cfg["alerts"].get("send_when_empty")))
    if preview:
        if built:
            _preview(built)
        else:
            print("nothing new to send")
        return
    if not built:
        if daily and on_schedule:
            state["last_digest_day"] = today        # today is done; tomorrow lists what comes in later
            store.save(state, state_path)
            print("nothing new this morning; no email today")
        else:
            print("nothing new to send")
        return
    if send(*built):
        for p in posts:
            p["emailed_at"] = ts
        for c in changes:
            state["watch"][c["id"]]["emailed_change"] = c["changed_at"]
        if daily and on_schedule:
            state["last_digest_day"] = today
        store.save(state, state_path)


def _preview(built) -> None:
    path = os.path.join(ROOT, "data", "email_preview.html")
    with open(path, "w") as f:
        f.write(f"<!-- {built[0]} -->\n{built[1]}")
    print(f"subject: {built[0]}\npreview written to {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
