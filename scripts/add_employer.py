"""
Add an employer by pasting its careers / job-board URL.

    python scripts/add_employer.py "Haute Living" "https://hauteliving.com/careers"
    python scripts/add_employer.py "The Drum" "https://apply.workable.com/the-drum/" --target

Reads the platform off the URL (Greenhouse, Lever, Ashby, SmartRecruiters,
Workable, BambooHR, Recruitee, Breezy, Workday). If it recognises one, the row
is added with verified: true, because a URL the employer itself links to is
evidence (slug *guesses* are what the IB tracker learned never to trust). If
not, the page is added for change-watching instead.

Run it from the project folder. Commit and push employers.yaml afterwards (or
edit employers.yaml directly on github.com — same result).
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.adapters import ADAPTERS, detect_from_url  # noqa: E402


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    name, url, target = argv[0], argv[1], "--target" in argv
    path = os.path.join(ROOT, "employers.yaml")
    text = open(path).read()
    eid = slug(name)
    if re.search(rf"^\s*- id: {re.escape(eid)}\s*$", text, re.M):
        sys.exit(f"'{eid}' is already in employers.yaml — edit that row instead.")
    d = detect_from_url(url)
    lines = [f"\n  - id: {eid}", f"    name: {name}"]
    if target:
        lines.append("    target: true")
    lines.append(f"    ats: {d['ats']}")
    for k in ("ats_token", "ats_dc", "ats_site"):
        if k in d:
            lines.append(f"    {k}: {d[k]}")
    if d["ats"] in ADAPTERS:
        lines.append("    verified: true")
    lines += [f"    careers_url: {url}", f"    aliases: [{name}]", ""]
    with open(path, "a") as f:
        f.write("\n".join(lines))
    how = "read automatically" if d["ats"] in ADAPTERS else "careers page watched for changes"
    print(f"Added {name} ({d['ats']}) — {how}. Test it with:\n"
          f"  python src/crawl.py --only \"{eid}\" --dry-run")


if __name__ == "__main__":
    main(sys.argv[1:])
