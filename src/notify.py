"""
Email digest of new matching jobs.

    python src/notify.py            send what is new since the last email
    python src/notify.py --test     send a test email now
    python src/notify.py --preview  write data/email_preview.html, send nothing

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

import html
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText

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
    """Same rule as the dashboard (classify.visible), against the email list.
    notify.py reads state, where `location` is still the true bucket."""
    return visible(p.get("location"), p.get("work_mode") or "unknown",
                   {"show": cfg["locations"]["email"]}, bool(p.get("remote_offered")))


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


def build(posts: list[dict], changes: list[dict], broken: list[dict], cfg: dict,
          labels: dict, families: dict) -> tuple[str, str] | None:
    if not posts and not changes:
        return None
    cap = cfg["alerts"]["max_per_email"]
    order = {b: i for i, b in enumerate(cfg["locations"]["email"])}
    fit_rank = {"early": 0, "open": 1, "stretch": 2}
    posts = sorted(posts, key=lambda p: (not p.get("target"), p.get("tier") != "core",
                                         fit_rank.get(p.get("fit"), 1),
                                         order.get(eff_loc(p, cfg), 9), p.get("company") or ""))
    shown, more = posts[:cap], max(0, len(posts) - cap)

    n = len(posts)
    name = cfg["owner"]["name"]
    subject = f"{cfg['alerts']['subject_prefix']} "
    if n:
        places = []
        for p in shown:
            lab = short_place(p, labels)
            if lab and lab not in places:
                places.append(lab)
        subject += f"{n} new job{'s' if n != 1 else ''} for you" + (" — " + ", ".join(places[:3]) if places else "")
    else:
        subject += "a careers page you follow just changed"

    rows = []
    for p in shown:
        brand, title = split_title(p)
        star = f"<span style='color:{SUN}'>★</span> " if p.get("target") else ""
        where = short_place(p, labels)
        bits = [families.get(p.get("family"), "")]
        if p.get("fit") == "early" or p.get("seniority") == "entry":
            bits.append("Early career")
        if p.get("years") and p["years"] >= 3:
            bits.append(f"Asks for {p['years']}+ years")
        rows.append(
            f"<tr><td style='padding:16px 0;border-bottom:1px solid {LINE}'>"
            f"<div style='color:{MUTED};font-size:14px;margin:0 0 3px'>{star}"
            f"<b style='color:{INK}'>{esc(brand)}</b>{' · ' + esc(where) if where else ''}</div>"
            f"<a href='{esc(p['url'])}' style='color:{INK};font-weight:700;font-size:18px;line-height:1.3;"
            f"text-decoration:none'>{esc(title)}</a>"
            f"<div style='color:{MUTED};font-size:14px;margin-top:4px'>{esc(' · '.join(x for x in bits if x))}</div>"
            f"</td></tr>")

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
    intro = (f"{'Here’s' if n == 1 else 'Here are'} {n} new job{'s' if n != 1 else ''} that match what you’re looking for."
             if n else "No new jobs this time, but a page you follow changed.")
    star_note = (f"<span style='color:{SUN}'>★</span> = a company on your list. " if any(p.get("target") for p in shown) else "")
    more_note = f"There are {more} more on your radar." if more else ""
    body = f"""<html><head><meta charset="utf-8"></head><body style="margin:0;padding:28px 20px;background:{PAPER};
font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:16px;line-height:1.5;color:{INK}">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:18px;padding:28px 26px">
<p style="margin:0 0 6px;font-size:22px;font-weight:700">Hi {esc(name)} 👋</p>
<p style="margin:0 0 6px">{intro}</p>
<p style="color:{MUTED};margin:0 0 6px;font-size:14px">{star_note}{more_note}</p>
<table style="border-collapse:collapse;width:100%">{''.join(rows)}</table>
{ch_html}{link}
<p style="color:{MUTED};font-size:13px;margin-top:28px">Good luck! LinkedIn and Indeed aren’t searched automatically —
your radar’s “More places to look” page has one-tap searches for those.</p>
</div></body></html>"""
    return subject, body


def welcome(state: dict, cfg: dict) -> tuple[str, str]:
    open_rel = [p for p in state["postings"].values() if p.get("relevant")
                and p.get("status") == "open" and not p.get("dupe_of") and emailable(p, cfg)]
    url = site_url()
    name = cfg["owner"]["name"]
    subject = f"{cfg['alerts']['subject_prefix']} Hi {name}, your job radar is ready ({len(open_rel)} jobs open now)"
    body = f"""<html><head><meta charset="utf-8"></head><body style="margin:0;padding:28px 20px;background:{PAPER};
font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:16px;line-height:1.55;color:{INK}">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:18px;padding:28px 26px">
<p style="margin:0 0 10px;font-size:22px;font-weight:700">Hi {esc(name)} 👋 your job radar is ready.</p>
<p>It just looked through the job pages of the companies on your list, plus a few big job sites,
and found <b>{len(open_rel)}</b> open jobs that fit what you’re looking for. They’re all waiting on your radar.</p>
<p>From now on it checks three times a day, and you’ll only get an email when something <b>new</b> turns up.</p>
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


def main(argv: list[str]) -> None:
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
    state = store.load()
    labels = {b["id"]: b["label"] for b in cfg["locations"]["buckets"]}
    labels.update({"OTHER_US": "Other US", "UNKNOWN": "Location not listed"})
    families = {f["id"]: f["label"] for f in cfg["roles"]["families"]}
    ts = store.now_iso()

    if not cfg["alerts"].get("enabled", True):
        print("alerts disabled in config.yaml")
        return

    # First run: one welcome email instead of an alert per existing job.
    if not state.get("welcomed") and state["runs"]:
        built = welcome(state, cfg)
        if "--preview" in argv:
            _preview(built)
            return
        if send(*built):
            state["welcomed"] = ts
            store.save(state)
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
    built = build(posts, changes, [], cfg, labels, families)
    if not built:
        print("nothing new to send")
        return
    if "--preview" in argv:
        _preview(built)
        return
    if send(*built):
        for p in posts:
            p["emailed_at"] = ts
        for c in changes:
            state["watch"][c["id"]]["emailed_change"] = c["changed_at"]
        store.save(state)


def _preview(built) -> None:
    path = os.path.join(ROOT, "data", "email_preview.html")
    with open(path, "w") as f:
        f.write(f"<!-- {built[0]} -->\n{built[1]}")
    print(f"subject: {built[0]}\npreview written to {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
