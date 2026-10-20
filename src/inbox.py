"""
The dashboard's inbox: what Ari sends from the page, with no GitHub account.

A static page can't write to this repo and nobody wanted a GitHub login in
the way, so the page drops a note into a topic on ntfy.sh (a free, public
message relay: no sign-up, anonymous publish, and anyone who knows a topic's
name can read it) and every crawl reads the topic first. The poller in
.github/workflows/inbox.yml looks every 15 minutes and starts a crawl early
when a note is waiting.

Because the topic is public, every note is signed: HMAC-SHA256 over its
fields with a passcode only Ari and this repo know (secret RADAR_PASSCODE).
Someone reading the topic sees the notes and their signatures but can't
forge a new one, and the passcode itself never travels. A replayed note is
harmless: adding a company twice is a no-op and "refresh" only asks for the
crawl that is already running. Nothing private goes in a note (her notes
about a company stay on her device): the topic and this repo are public.

Companies she adds land in data/inbox_employers.yaml, a file only the crawl
writes, so they never conflict with hand edits to employers.yaml. The crawl
reads both. The ids of notes it acted on, and of notes it refused (and why),
are exported to the dashboard, which is how her page can say "In the radar",
"the passcode was probably mistyped" or "tell Itz" truthfully.

Note format (JSON, the ntfy message body):

    {"v": 1, "kind": "add-company" | "refresh", "id": "<random>", "at": "<ISO>",
     "company": "", "website": "", "careers": "", "linkedin": "", "target": false,
     "sig": "<hex>"}

The signature covers v, kind, id, at, company, website, careers, linkedin and
target ("1"/"0"), joined with newlines, whitespace collapsed. site/index.html
signs the same way; test_inbox_signature pins the format.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

KINDS = ("add-company", "refresh")
FIELDS = ("company", "website", "careers", "linkedin")
MAX_AGE_S = 36 * 3600        # ntfy keeps 12 h anyway; older notes are stale
KEEP = 300                   # note ids remembered (done / rejected), newest kept
INBOX_EMPLOYERS = os.path.join(ROOT, "data", "inbox_employers.yaml")


def topic_url(cfg: dict) -> str | None:
    c = cfg.get("inbox") or {}
    topic = str(c.get("ntfy_topic") or "").strip()
    if not topic:
        return None
    return f"{str(c.get('server') or 'https://ntfy.sh').rstrip('/')}/{topic}"


def canonical(m: dict) -> str:
    def s(k):
        return " ".join(str(m.get(k) or "").split())
    return "\n".join(["1", s("kind"), s("id"), s("at"), *(s(k) for k in FIELDS), "1" if m.get("target") else "0"])


def sign(m: dict, passcode: str) -> str:
    return hmac.new(passcode.encode(), canonical(m).encode(), hashlib.sha256).hexdigest()


def verify(m: dict, passcode: str) -> bool:
    return bool(passcode) and hmac.compare_digest(sign(m, passcode), str(m.get("sig") or "").lower())


def fetch(client, url: str, since: str) -> list[dict]:
    """Notes on the topic since `since` (a unix time, or '12h'), oldest first,
    each with ntfy's own id and time attached as _id / _time."""
    r = client.get(f"{url}/json", params={"poll": "1", "since": since or "12h"})
    r.raise_for_status()
    out = []
    for line in r.text.splitlines():
        if not line.strip():
            continue
        try:
            env = json.loads(line)
            if env.get("event") != "message":
                continue
            m = json.loads(env.get("message") or "")
        except (ValueError, TypeError):
            continue
        if isinstance(m, dict):
            m["_id"], m["_time"] = env.get("id"), env.get("time")
            out.append(m)
    return out


def _trim(d: dict, n: int = KEEP) -> None:
    for k in sorted(d, key=lambda k: d[k]["t"] if isinstance(d[k], dict) else d[k])[:-n]:
        del d[k]


def public(state: dict) -> dict:
    """What the dashboard gets: which of her notes were acted on or refused."""
    box = state.get("inbox") or {}
    return {"done": sorted(box.get("done") or {}, key=(box.get("done") or {}).get)[-KEEP:],
            "rejected": {k: v["why"] for k, v in (box.get("rejected") or {}).items()}}


def process(state: dict, client, cfg: dict, passcode: str, *, path: str = INBOX_EMPLOYERS,
            known: list[dict] | None = None, check: bool = True, log=print) -> dict:
    """Read the inbox and act on it. Never raises: the inbox must not stop a
    crawl. `known` are the hand-kept employers (never written here, only
    checked so a company is not added twice). Returns counts for the run log."""
    counts = {"received": 0, "added": 0, "updated": 0, "refresh": 0, "rejected": 0, "failed": 0, "error": ""}
    url = topic_url(cfg)
    if not url or not passcode:
        return counts
    box = state.setdefault("inbox", {})
    box.setdefault("since", ""), box.setdefault("done", {}), box.setdefault("rejected", {})
    try:
        notes = fetch(client, url, box["since"])
    except Exception as ex:  # noqa: BLE001
        counts["error"] = f"{type(ex).__name__}: {str(ex)[:120]}"
        log(f"inbox: couldn't be read ({counts['error']})")
        return counts

    from add_employer import add_employer  # noqa: E402  scripts/ is on sys.path
    now = int(time.time())
    for m in notes:
        counts["received"] += 1
        nid = str(m.get("id") or "")[:64]
        if not nid or nid in box["done"]:
            continue
        if m.get("_time") and now - float(m["_time"]) > MAX_AGE_S:
            continue
        if m.get("v") != 1 or m.get("kind") not in KINDS or not verify(m, passcode):
            # Not marked done (the id is whatever the sender chose); remembered as
            # refused so her page can say "passcode probably mistyped".
            counts["rejected"] += 1
            box["rejected"][nid] = {"t": now, "why": "signature"}
            log(f"inbox: ignored a note ({str(m.get('kind'))[:20]!r}: bad signature or shape)")
            continue
        if m["kind"] == "refresh":
            counts["refresh"] += 1
        else:
            try:
                r = add_employer(str(m.get("company") or ""), str(m.get("careers") or m.get("website") or "") or None,
                                 target=bool(m.get("target")), linkedin_url=str(m.get("linkedin") or "") or None,
                                 inbox_id=nid, path=path, known=known, check=check)
                counts["added" if r["created"] else "updated"] += 1
                log(f"inbox: {r['message']}")
            except ValueError as ex:                 # her input: say why, don't retry
                counts["rejected"] += 1
                box["rejected"][nid] = {"t": now, "why": str(ex)[:120]}
                log(f"inbox: couldn't add {str(m.get('company'))[:40]!r}: {ex}")
                continue
            except Exception as ex:  # noqa: BLE001  our problem: leave the note for the next crawl
                counts["failed"] += 1
                log(f"inbox: failed on {str(m.get('company'))[:40]!r}: {type(ex).__name__}: {str(ex)[:120]}")
                continue
        box["done"][nid] = now
        box["rejected"].pop(nid, None)             # a note refused earlier and now fine
    if notes and notes[-1].get("_time"):
        box["since"] = str(int(notes[-1]["_time"]))    # a time, not an id: an expired id would read as "nothing"
    _trim(box["done"]), _trim(box["rejected"])
    return counts
