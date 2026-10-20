"""
Add an employer by pasting its careers / job-board URL.

    python scripts/add_employer.py "Haute Living" "https://hauteliving.com/careers"
    python scripts/add_employer.py "The Drum" "https://apply.workable.com/the-drum/" --target
    python scripts/add_employer.py "World Red Eye" --linkedin https://www.linkedin.com/company/world-red-eye/

Reads the platform off the URL (Greenhouse, Lever, Ashby, SmartRecruiters,
Workable, BambooHR, Recruitee, Breezy, Workday). If it recognises one, it calls
the board once: a board that answers is added with verified: true, because an
answer is evidence (slug *guesses* are what the IB tracker learned never to
trust). A board that doesn't answer, or any other URL, is added as a careers
page to watch for changes. `--no-check` skips the call and writes
verified: false; flip it by hand once you've seen the board load.

The same function runs inside the crawl when Ari uses the dashboard's "Add a
company" button (src/inbox.py reads her note), so every rule about what gets
written lives here, once:

  * the row is appended as text (never `yaml.safe_dump`: that would strip the
    hand-written comments) and every string is a JSON-quoted scalar, so a name
    with ":" or "#" cannot break the file;
  * an employer already on the list is not added twice: by id, by normalised
    name / alias, by the board's identity (platform + token: ten rows share
    job-boards.greenhouse.io, so the host alone proves nothing) or by the
    careers URL. The existing row gets any missing linkedin_url / target.

Run it from the project folder. Commit and push employers.yaml afterwards (or
edit employers.yaml directly on github.com — same result). EMPLOYERS_PATH in
the environment points it at another file (tests and dry runs).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.parse import urlsplit

import httpx
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.adapters import ADAPTERS, detect_from_url  # noqa: E402
from src.crawl import norm_company  # noqa: E402

_URL = re.compile(r"^https?://[^\s\"'<>]+$")
_LINKEDIN = re.compile(r"^https?://(www\.)?linkedin\.com/company/[\w%.-]+/?$", re.I)
CHECK_TIMEOUT = 25


def employers_path() -> str:
    return os.environ.get("EMPLOYERS_PATH") or os.path.join(ROOT, "employers.yaml")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")


def _norm_url(u: str | None) -> str:
    s = urlsplit((u or "").strip())
    return f"{s.scheme.lower()}://{s.netloc.lower()}{s.path.rstrip('/')}"


def _q(s) -> str:
    """A YAML double-quoted scalar (JSON strings are valid YAML)."""
    return json.dumps(str(s), ensure_ascii=False)


def _row_span(text: str, eid: str) -> tuple[int, int, int] | None:
    """(start of the `- id:` line, end of that line, end of the row's block)."""
    m = re.search(rf"^  - id: {re.escape(eid)}[ \t]*$", text, re.M)
    if not m:
        return None
    nxt = re.compile(r"^(?:  - id: |\S)", re.M).search(text, m.end() + 1)
    return m.start(), m.end(), nxt.start() if nxt else len(text)


def _mode(row: dict) -> str:
    if row.get("ats") in ADAPTERS and row.get("verified"):
        return "read"
    if row.get("watch", True) and (row.get("careers_url") or row.get("watch_url")):
        return "watch"
    return "manual"


def _find_existing(rows: list[dict], eid: str, name: str, url: str | None, d: dict) -> dict | None:
    by_id = {r["id"]: r for r in rows}
    if eid in by_id:
        return by_id[eid]
    key = norm_company(name)
    for r in rows:
        if key and key in {norm_company(n) for n in [r.get("name", ""), *(r.get("aliases") or [])]}:
            return r
    if url and d["ats"] in ADAPTERS:
        for r in rows:
            if r.get("ats") == d["ats"] and str(r.get("ats_token", "")).lower() == d["ats_token"].lower() \
                    and (d["ats"] != "workday" or str(r.get("ats_site", "")).lower() == d.get("ats_site", "").lower()):
                return r
    elif url:
        nu = _norm_url(url)
        for r in rows:
            if nu in (_norm_url(r.get("careers_url")), _norm_url(r.get("watch_url"))):
                return r
    return None


def _board_answers(row: dict) -> bool:
    """One real call to the board. True = this is a readable board of THIS employer."""
    try:
        with open(os.path.join(ROOT, "config.yaml")) as f:
            ua = yaml.safe_load(f)["crawl"]["user_agent"]
    except Exception:  # noqa: BLE001
        ua = "ari-job-radar/1.0"
    try:
        with httpx.Client(headers={"User-Agent": ua, "Accept": "application/json, text/plain, */*"},
                          timeout=CHECK_TIMEOUT, follow_redirects=True) as client:
            ADAPTERS[row["ats"]](client, row)
        return True
    except Exception:  # noqa: BLE001  any failure = not verified, watch the page instead
        return False


INBOX_HEADER = ("# Companies added from the dashboard. src/inbox.py writes this file and the\n"
                "# crawl reads it together with employers.yaml; edit employers.yaml for the rest.\n"
                "employers:\n")


def add_employer(name: str, url: str | None = None, *, target: bool = False,
                 linkedin_url: str | None = None, note: str | None = None, inbox_id: str | None = None,
                 path: str | None = None, known: list[dict] | None = None, check: bool = True) -> dict:
    """Append (or complete) an employer row in `path`. `known` are rows kept
    elsewhere (employers.yaml, when writing the inbox file): checked for
    duplicates, never written. Returns
    {id, created, mode: read|watch|manual, ats, updated: [field, ...], message}."""
    path = path or employers_path()
    name = " ".join((name or "").split())
    url = (url or "").strip() or None
    linkedin_url = (linkedin_url or "").strip() or None
    if not name or len(name) > 80:
        raise ValueError("The company name must be 1–80 characters.")
    if url and _LINKEDIN.match(url):
        linkedin_url, url = url, None            # a LinkedIn page is not a careers page
    if url and not _URL.match(url):
        raise ValueError(f"That doesn't look like a web address: {url}")
    if linkedin_url and not _LINKEDIN.match(linkedin_url):
        raise ValueError("The LinkedIn link should look like https://www.linkedin.com/company/<name>/")

    text = open(path, encoding="utf-8").read() if os.path.exists(path) else INBOX_HEADER
    rows = (yaml.safe_load(text) or {}).get("employers") or []
    eid = slug(name) or "employer"
    d = detect_from_url(url) if url else {"ats": "none"}

    # --- already on the list: complete the row, never duplicate it ---------------
    ex = _find_existing(rows, eid, name, url, d)
    if not ex and known:
        other = _find_existing(list(known), eid, name, url, d)
        if other:            # kept by hand elsewhere: nothing to write
            return {"id": other["id"], "created": False, "mode": _mode(other), "ats": other.get("ats"),
                    "updated": [], "message": f"{other['name']} is already on the list."}
    if ex:
        span = _row_span(text, ex["id"])
        updated = []
        if span:
            start, id_end, end = span
            block = text[start:end]
            add = []
            if linkedin_url and not ex.get("linkedin_url"):
                add.append(f"    linkedin_url: {_q(linkedin_url)}")
                updated.append("linkedin_url")
            if target and not ex.get("target"):
                if re.search(r"^    target:", block, re.M):
                    block = re.sub(r"^    target:.*$", "    target: true", block, count=1, flags=re.M)
                else:
                    add.append("    target: true")
                updated.append("target")
            if inbox_id and ex.get("inbox_id") != inbox_id:
                # The note's id lets the dashboard show "In the radar" for her own entry.
                if re.search(r"^    inbox_id:", block, re.M):
                    block = re.sub(r"^    inbox_id:.*$", f"    inbox_id: {_q(inbox_id)}", block, count=1, flags=re.M)
                else:
                    add.append(f"    inbox_id: {_q(inbox_id)}")
                updated.append("inbox_id")
            if add:
                block = block[:id_end - start] + "\n" + "\n".join(add) + block[id_end - start:]
            if updated:
                text = text[:start] + block + text[end:]
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
        what = " and ".join(u for u in updated if u != "inbox_id")
        wanted = (linkedin_url and not ex.get("linkedin_url")) or (target and not ex.get("target"))
        if wanted and not span:
            # The row exists but its `- id:` line isn't where the editor expects
            # (quoted, commented, re-indented): say so instead of pretending.
            msg = (f"{ex['name']} is already on the list, but its row couldn't be edited "
                   f"automatically — add the change to employers.yaml by hand.")
        else:
            msg = (f"{ex['name']} is already on the list" + (f" — added {what}." if what else "."))
        return {"id": ex["id"], "created": False, "mode": _mode(ex), "ats": ex.get("ats"),
                "updated": updated, "message": msg}

    # --- new row -------------------------------------------------------------------
    row: dict = {"id": eid, "name": name}       # eid is free: _find_existing checked
    lines = [f"\n  - id: {eid}", f"    name: {_q(name)}"]
    if target:
        lines.append("    target: true")
        row["target"] = True
    lines.append(f"    ats: {d['ats']}")
    row["ats"] = d["ats"]
    for k in ("ats_token", "ats_dc", "ats_site"):
        if k in d:
            lines.append(f"    {k}: {_q(d[k])}")
            row[k] = d[k]
    if url and d["ats"] in ADAPTERS:
        ok = _board_answers(row) if check else False
        lines.append(f"    verified: {'true' if ok else 'false'}")
        row["verified"] = ok
        if check and not ok:
            # watch.py falls back to careers_url, so no watch_url line is needed.
            how = "its job board didn't answer, so its careers page is watched for changes instead"
        elif ok:
            how = "its job board will be read automatically"
        else:
            how = "its job board is listed but unchecked (verified: false); its page is watched meanwhile"
    elif url:
        how = "it has no job board the radar can read, so its careers page is watched for changes"
    else:
        lines.append("    watch: false")
        row["watch"] = False
        note = note or ("No careers page found; follow on LinkedIn" if linkedin_url else "No careers page given")
        how = "no careers page, so there's nothing to check automatically" + (
            "; the LinkedIn link is on the Who I'm watching page" if linkedin_url else "")
    if url:
        lines.append(f"    careers_url: {_q(url)}")
        row["careers_url"] = url
    if linkedin_url:
        lines.append(f"    linkedin_url: {_q(linkedin_url)}")
        row["linkedin_url"] = linkedin_url
    if inbox_id:
        lines.append(f"    inbox_id: {_q(inbox_id)}")
        row["inbox_id"] = inbox_id
    if note:
        lines.append(f"    note: {_q(note)}")
    lines += [f"    aliases: [{_q(name)}]", ""]

    if not text.endswith("\n"):
        text += "\n"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n".join(lines))
    return {"id": eid, "created": True, "mode": _mode(row), "ats": d["ats"], "updated": [],
            "message": f"Added {name} — {how}."}


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("url", nargs="?", help="careers page or job-board URL")
    ap.add_argument("--target", action="store_true", help="one of Ari's own picks (★)")
    ap.add_argument("--linkedin", help="the company's LinkedIn page")
    ap.add_argument("--note")
    ap.add_argument("--no-check", action="store_true", help="don't call the board; write verified: false")
    a = ap.parse_args(argv)
    try:
        r = add_employer(a.name, a.url, target=a.target, linkedin_url=a.linkedin, note=a.note,
                         check=not a.no_check)
    except ValueError as ex:
        sys.exit(str(ex))
    print(r["message"])
    if r["created"]:
        print(f"Test it with:\n  python src/crawl.py --only \"{r['id']}\" --dry-run")


if __name__ == "__main__":
    main(sys.argv[1:])
