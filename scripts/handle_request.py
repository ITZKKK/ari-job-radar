"""
Act on a request made from the dashboard: an issue opened on the repo.

Ari's two buttons ("Add a company", "Check for new jobs now") open GitHub issue
forms; .github/workflows/requests.yml runs this on every new issue and passes
the issue in through the environment (never on the command line, so a title
can't become shell):

    ISSUE_NUMBER  ISSUE_TITLE  ISSUE_BODY  ISSUE_USER  ISSUE_LABELS  COMMENT_FILE

It decides what the issue is (the TITLE decides, "Add company: …" or "Refresh
now"; the label is a hint), checks the author against config.yaml →
github.allowed_users (anyone can open an issue on a public repo), does the
work, then writes

    $GITHUB_OUTPUT   kind=add-company|refresh|declined|ignore  changed=  dispatch=  close_reason=
    COMMENT_FILE     the reply to post on the issue, as markdown

and the workflow commits, starts the crawl, comments and closes accordingly.

The issue form's field LABELS are what the parser keys on (GitHub renders a
form as "### Label\\n\\nvalue"), so LABELS below and the labels in
.github/ISSUE_TEMPLATE/add-company.yml must say the same thing.

Dry run locally:

    ISSUE_TITLE="Refresh now" ISSUE_USER=ITZKKK COMMENT_FILE=/tmp/c.md python scripts/handle_request.py
"""

from __future__ import annotations

import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from add_employer import add_employer, employers_path  # noqa: E402
from src.crawl import request_links  # noqa: E402
from src.notify import site_url  # noqa: E402

# Field labels in .github/ISSUE_TEMPLATE/add-company.yml. Keep both in step.
LABELS = {
    "company": "Company name",
    "url": "Website or careers page",
    "linkedin": "LinkedIn page",
    "target": "Your list",
}
TARGET_OPTION = "This is one of my picks (give it a ★)"

DECLINED = ("Only people on the radar's allow-list can change it here — "
            "ask Itz to add your GitHub username.")


def parse_issue_form(body: str | None) -> dict:
    """GitHub renders an issue form as "### Label\\n\\nvalue" sections. Returns
    {label.lower(): value}; "_No response_" -> ""; a checkbox list -> bool."""
    out: dict = {}
    for m in re.finditer(r"^### (.+?)[ \t]*\n+([\s\S]*?)(?=^### |\Z)", body or "", re.M):
        label, value = m.group(1).strip().lower(), m.group(2).strip()
        if value == "_No response_":
            value = ""
        if re.match(r"^- \[[ xX]\]", value):
            value = bool(re.search(r"- \[[xX]\]", value))
        out[label] = value
    return out


def detect_kind(title: str | None, labels: str | None) -> str:
    t = (title or "").strip().lower()
    if re.match(r"^add company", t):
        return "add-company"
    if re.match(r"^refresh", t):
        return "refresh"
    labs = {x.strip().lower() for x in (labels or "").split(",") if x.strip()}
    if "add-company" in labs:
        return "add-company"
    if "refresh" in labs:
        return "refresh"
    return "ignore"


def allowed(user: str | None, cfg: dict) -> bool:
    users = {str(u).lower() for u in (cfg.get("github") or {}).get("allowed_users") or []}
    return bool(user) and user.lower() in users


def handle(issue: dict, cfg: dict, *, path: str | None = None, check: bool = True) -> dict:
    """issue = {title, body, user, labels}. Returns
    {kind, changed, dispatch, close_reason, comment}. Pure apart from the
    employers.yaml write (and, with check=True, one call to a new board)."""
    kind = detect_kind(issue.get("title"), issue.get("labels"))
    res = {"kind": kind, "changed": False, "dispatch": False, "close_reason": "completed", "comment": ""}
    if kind == "ignore":
        return res
    if not allowed(issue.get("user"), cfg):
        res.update(kind="declined", close_reason="not planned", comment=DECLINED)
        return res

    # Same derivations as the email link and the dashboard buttons.
    site = site_url((cfg.get("github") or {}).get("repo"))
    acts = (request_links(cfg) or {}).get("actions_url", "")
    later = (f"Give it about 5 minutes, then open your radar: {site}" if site else
             "Give it about 5 minutes, then open your radar.")
    progress = f"\n\nProgress: {acts}" if acts else ""

    if kind == "refresh":
        res.update(dispatch=True, comment=f"On it — checking now. {later}{progress}")
        return res

    # add-company
    form = parse_issue_form(issue.get("body"))
    title = issue.get("title") or ""
    name = form.get(LABELS["company"].lower()) or re.sub(r"^add company:?\s*", "", title, flags=re.I)
    target = form.get(LABELS["target"].lower())
    # The dashboard's prefilled link can't tick the checkbox, so it puts a ★ in the title.
    pick = (bool(target) if isinstance(target, bool) else False) or "★" in title
    try:
        r = add_employer(str(name).replace("★", "").strip(), form.get(LABELS["url"].lower()) or None,
                         target=pick,
                         linkedin_url=form.get(LABELS["linkedin"].lower()) or None,
                         path=path, check=check)
    except ValueError as ex:
        res.update(close_reason="not planned",
                   comment=f"Couldn't add that: {ex}\n\nOpen a new \"Add a company\" and try again.")
        return res
    changed = r["created"] or bool(r["updated"])
    res["changed"] = changed
    if r["created"] and r["mode"] in ("read", "watch"):
        res["dispatch"] = True
        res["comment"] = f"{r['message']} The radar is checking it now. {later}{progress}"
    else:
        res["comment"] = r["message"] + ("" if r["created"] else " Nothing else to do.")
        if r["created"]:
            res["comment"] += " It's on your Who I'm watching page."
    return res


def main() -> None:
    with open(os.path.join(ROOT, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    issue = {"title": os.environ.get("ISSUE_TITLE"), "body": os.environ.get("ISSUE_BODY"),
             "user": os.environ.get("ISSUE_USER"), "labels": os.environ.get("ISSUE_LABELS")}
    res = handle(issue, cfg, path=employers_path(),
                 check=os.environ.get("REQUEST_NO_CHECK", "") != "1")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    lines = [f"kind={res['kind']}", f"changed={'true' if res['changed'] else 'false'}",
             f"dispatch={'true' if res['dispatch'] else 'false'}", f"close_reason={res['close_reason']}"]
    if gh_out:
        with open(gh_out, "a") as f:
            f.write("\n".join(lines) + "\n")
    comment_file = os.environ.get("COMMENT_FILE")
    if comment_file:
        with open(comment_file, "w", encoding="utf-8") as f:
            f.write(res["comment"] + "\n")
    print("\n".join(lines))
    if res["comment"]:
        print(f"comment: {res['comment']}")


if __name__ == "__main__":
    main()
