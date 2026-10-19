"""
State, kept as one JSON file in the repo: data/state.json.

Why JSON and not SQLite (which the IB tracker uses): this tracker has no
server. GitHub Actions writes the state and commits it; the dashboard is a
static page. A JSON file diffs readably in git (every crawl's commit shows what
changed) and the export step can turn it into what the page reads without a
database driver anywhere.

The load-bearing rules are the IB tracker's, unchanged:
  * History, not snapshots: every posting has first_seen and last_seen.
  * A run log per source: a posting is only marked closed when it vanished from
    a board that was read SUCCESSFULLY. A broken board never reads as closures.
"""

from __future__ import annotations

import datetime as dt
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "data", "state.json")
RADAR_PATH = os.path.join(ROOT, "data", "radar.json")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def days_since(ts: str | None) -> float:
    d = parse(ts)
    if not d:
        return 1e9
    return (dt.datetime.now(dt.timezone.utc) - d).total_seconds() / 86400


def empty() -> dict:
    return {"version": 1, "postings": {}, "sources": {}, "runs": [], "watch": {}}


def load(path: str = STATE_PATH) -> dict:
    if not os.path.exists(path):
        return empty()
    with open(path) as f:
        s = json.load(f)
    for k, v in empty().items():
        s.setdefault(k, v)
    return s


def save(state: dict, path: str = STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def write_json(obj, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
