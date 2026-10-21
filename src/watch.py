"""
Watch the careers pages that have no readable job board.

Small publications (World Red Eye, Haute Living, Ocean Drive...) often have no
applicant tracking system at all: a "Careers" page, a "we're hiring" line, or
nothing. Writing a scraper per site is what this project avoids. What can be
done generically is notice CHANGE: each crawl fetches the page and keeps the
lines that read like hiring. When those lines change, the dashboard and the
email say which employer and quote what appeared. It never invents a posting.

Ported from the IB tracker's watch.py. A page is only ever compared against a
good previous read: a failed fetch, a bot wall or an empty page (drawn by
JavaScript) is recorded as a status and leaves the baseline alone, so a broken
fetch is never reported as news.
"""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser

HIRING = re.compile(
    r"\b(editor(ial)?|writer|reporter|journalist|producer|content|social media|"
    r"coordinator|assistant|associate|intern(ship)?s?|fellow(ship)?|freelance|"
    r"position|positions|opening|openings|vacanc(y|ies)|hiring|join (our|the) team|"
    r"careers?|jobs?|apply|application|resume|cv|pitch(es)?|contributors?)\b", re.I)

NOISE = [re.compile(p, re.I) for p in (
    r"\b\d+\+?\s*(minutes?|hours?|days?|weeks?|months?)\s+ago\b",
    r"\b(posted|updated)\s+(today|yesterday)\b",
    r"\b(19|20)\d{2}-\d{2}-\d{2}\b",
    r"\b\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"[a-z]*\.?,?\s+(19|20)\d{2}\b",
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}"
    r"(st|nd|rd|th)?,?\s+(19|20)\d{2}\b",
    r"\b\d{1,2}/\d{1,2}/(19|20)?\d{2}\b",
    r"©.*", r"\bcopyright\b.*",
)]

# A change is only worth telling Ari about when a line that APPEARED is about
# a role: a line going away is never something to apply for, and a rotating
# testimonial ("Hiring BoardroomPR for our PR and marketing has been one of
# the best decisions...") said "hiring" without being a job. So a long line
# counts only when it names a role. Anything else quietly becomes the new
# baseline.
WORTHY = re.compile(
    r"\b(editor(ial)?|writer|reporter|journalist|producer|content (creator|producer|writer)|"
    r"social media|coordinator|assistant|intern(ship)?|fellow(ship)?|copywriter|"
    r"(now )?hiring|open (position|role)s?|we'?re looking for|job opening)\b", re.I)
ROLE = re.compile(r"\b(editor(ial)?|writer|reporter|journalist|producer|coordinator|assistant|"
                  r"associate|intern(ship)?|fellow(ship)?|copywriter|publicist|specialist|manager)\b", re.I)
LONG_LINE = 120


def worthy(lines: list[str]) -> list[str]:
    """The added lines that read like a role, not like a sentence about one."""
    return [l for l in lines if WORTHY.search(l) and (len(l) <= LONG_LINE or ROLE.search(l))]

_BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th",
           "section", "article", "br", "a", "button", "option", "dt", "dd"}
_SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe", "nav", "footer"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self._buf, self._skip = [], [], 0

    def _flush(self):
        t = " ".join("".join(self._buf).split())
        if t:
            self.lines.append(t)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCKS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCKS:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)


def hiring_lines(html: str) -> list[str]:
    p = _Text()
    try:
        p.feed(html or "")
        p.close()
    except Exception:  # noqa: BLE001  malformed HTML is normal
        pass
    p._flush()
    out = set()
    for line in p.lines:
        for rx in NOISE:
            line = rx.sub("", line)
        line = " ".join(line.split()).strip(" ·|-–—")
        if 8 <= len(line) <= 220 and HIRING.search(line):
            out.add(line)
    return sorted(out)


def check(state: dict, client, employers: list[dict], ts: str, delay: float, log) -> dict:
    counts = {"watched": 0, "changed": 0, "unwatchable": 0}
    w = state["watch"]
    for e in employers:
        url = e.get("watch_url") or e.get("careers_url")
        if not url or not url.startswith("http"):
            continue
        prev = w.get(e["id"]) or {}
        status, error, lines = "ok", "", None
        try:
            r = client.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
            if r.status_code in (401, 403, 429, 503):
                status, error = "blocked", f"HTTP {r.status_code}"
            elif r.status_code >= 400:
                status, error = "error", f"HTTP {r.status_code}"
            else:
                lines = hiring_lines(r.text)
                if not lines:
                    status = "empty"
        except Exception as ex:  # noqa: BLE001  one page never stops the crawl
            status, error = "error", f"{type(ex).__name__}: {str(ex)[:120]}"

        entry = {**prev, "url": url, "status": status, "error": error, "checked_at": ts}
        if status != "ok":
            counts["unwatchable"] += 1
        else:
            counts["watched"] += 1
            old = prev.get("lines")
            if old is None or prev.get("url") != url:
                entry.update(lines=lines, changed_at=None, added=[], removed=[])
            elif old != lines:
                added = [x for x in lines if x not in old]
                removed = [x for x in old if x not in lines]
                entry["lines"] = lines
                if worthy(added):
                    entry.update(changed_at=ts, added=added[:12], removed=removed[:12])
                    counts["changed"] += 1
                    log(f"  page changed · {e['name']}: {(added or removed)[0][:70]}")
        w[e["id"]] = entry
        time.sleep(delay)
    return counts
