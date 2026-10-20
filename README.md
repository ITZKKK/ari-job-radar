# Ari's Job Radar

Watches job boards for writer / editor / reporter / editorial-coordinator /
content-creator roles (plus social media, PR, marketing and copywriting) in
Miami or fully remote (a New York job that says "or Remote" counts). It emails
Ari when something new appears and keeps a dashboard she can open from any
phone or laptop.

It's built like Itz's IB tracker (same adapters-not-scrapers design, same
"closed only when a board was read successfully" rule, same page watcher),
**but it runs entirely in the cloud.** GitHub Actions crawls three times a day,
GitHub Pages hosts the dashboard and Resend sends the email. Nothing runs on
Ari's or Itz's Mac, and it costs nothing.

```
GitHub Actions (7:20am, 1:20pm, 7:20pm New York time)
  ├─ src/crawl.py    reads 22 employer job boards + job-search APIs,
  │                  watches 11 careers pages, keeps history in data/state.json
  ├─ src/notify.py   emails Ari the NEW matching jobs (via Resend)
  ├─ commits data/   (git history = audit log of every crawl)
  └─ publishes site/ + data/radar.json to GitHub Pages  →  https://<user>.github.io/ari-job-radar/
```

---

## Setup (Itz) — about 30 minutes, once

Everything below happens in a browser on github.com, plus one block of commands
in **Terminal on your Mac**.

### 1. Create the GitHub repository

1. github.com → **+** (top right) → **New repository**.
2. Name: `ari-job-radar`. Owner: your account (ITZKKK).
3. Visibility:
   - **Public** works on every GitHub plan.
   - **Private** works only if your account has GitHub Pro. Pro is free with the
     GitHub Student Developer Pack (education.github.com/pack), which a
     Columbia email qualifies for.
   - Either way, **the dashboard itself is a public web page.** Anyone with the
     link can open it. It isn't listed in search engines (it carries `noindex`)
     and shows only public job postings; Ari's saves and notes never leave her
     browser.
4. Do **not** tick "Add a README" (the folder already has one). → **Create repository**.

### 2. Push the code (Terminal on your Mac)

```bash
cd ~/Desktop/"Ari's tracker"/ari-job-radar
git init -b main
git add .
git commit -m "Ari's job radar"
git remote add origin https://github.com/ITZKKK/ari-job-radar.git
git push -u origin main
```

(The quotes around `"Ari's tracker"` matter because of the apostrophe and the space.)
Unlike your IB tracker, this folder can stay on the Desktop: nothing runs
locally, so macOS's Desktop restrictions don't apply.

### 3. Turn on the dashboard hosting

Repo → **Settings** → **Pages** → *Build and deployment* → **Source: GitHub Actions**.

### 4. Add the secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
Names must match exactly.

| Secret | Value | Needed? |
|---|---|---|
| `RESEND_API_KEY` | the key Ari sends you (starts `re_`) | yes, for email |
| `ALERT_TO` | **the exact email Ari signed up to Resend with** | yes, for email |
| `ADZUNA_APP_ID` | from developer.adzuna.com (step 5) | optional, paid |
| `ADZUNA_APP_KEY` | same place | optional, paid |
| `JSEARCH_RAPIDAPI_KEY` | from rapidapi.com (step 6) | recommended (free) |

With no email secrets the radar still crawls and the dashboard still updates;
it just doesn't email.

### 5. Adzuna key (optional, paid): catches small Miami employers

**Adzuna is no longer free** (checked 19 Oct 2026). A new account gets 25 test
calls; after that it costs £8/month for 5,000 calls/month. The radar would use
about 540 a month. **Don't add the key unless you pay.** A key that's out of
test calls just makes that source fail on every run.

Worth paying for only if, after a week or two, the radar is clearly missing
small Miami employers (agencies, restaurant groups, local magazines). If you do:
pay on developer.adzuna.com, then add the Application ID and Key as
`ADZUNA_APP_ID` and `ADZUNA_APP_KEY`. Without them the radar simply skips Adzuna.

### 6. JSearch key (optional, 5 minutes): LinkedIn and Indeed jobs, indirectly

LinkedIn and Indeed forbid automated reading, so the radar never touches them.
JSearch resells Google for Jobs results, which include LinkedIn and Indeed
postings. That puts the legal exposure on the vendor, not on you. The free plan
gives 200 calls/month, and the radar uses 3 a day (about 90 a month).

1. rapidapi.com → sign up → search **JSearch** (by OpenWeb Ninja / letscrape) → **Pricing** → **Basic (free)** → Subscribe.
2. Copy the **X-RapidAPI-Key** shown on the endpoint page → secret `JSEARCH_RAPIDAPI_KEY`.

### 7. First run

The push in step 2 already started one. Open the repo's **Actions** tab:

- If GitHub asks, click **"I understand my workflows, go ahead and enable them"**.
- The **radar** workflow takes about 3–5 minutes. When it's green, the dashboard is at
  **`https://itzkkk.github.io/ari-job-radar/`** (also shown on the run page under *deploy*).
- To crawl again right now: **Actions** → **radar** → **Run workflow**.

If the first run happened before the secrets existed, just click **Run workflow**
once more. The first run that has email credentials sends Ari a single
"your radar is live" email. Later emails list only jobs that are new since then.

### 8. Send Ari the link

On iPhone, she can open it in Safari → **Share** → **Add to Home Screen**, and it behaves like an app.

### 9. The dashboard's inbox: Add a company / Check for new jobs now, with no GitHub for anyone

On the page Ari can **add a job she found** (Saved / Applied tabs) and **add
a company** (Who I'm watching), with website, jobs page, LinkedIn page, notes
and a ★. Those live on her device like her saves. To make the radar *act*
(watch the company's page, or crawl right now) the page drops a signed note
into the radar's **inbox**: a topic on [ntfy.sh](https://ntfy.sh), a free
public message relay that needs no account. Every crawl reads the inbox
first (`src/inbox.py`), and `.github/workflows/inbox.yml` looks every 15
minutes and starts a crawl early when a note is waiting. A company she adds
is in `employers.yaml` and being checked within about 15 minutes, and her
page shows **In the radar** next to it.

Because the topic is public, every note is signed (HMAC-SHA256) with a
passcode she types once on the page; the crawl checks the signature against
the repo secret `RADAR_PASSCODE`. The passcode never leaves her device, and
knowing the topic name is not enough to add anything. Already set up:

- `config.yaml` → `inbox.ntfy_topic` is the topic; the secret
  `RADAR_PASSCODE` holds the passcode. To rotate either, change it and give
  Ari the new passcode (the page asks again).
- No passcode on the page → she can still add companies; they show
  **Not in radar** so she can send you the names and you add them with
  `python scripts/add_employer.py "Name" "<url>"`.
- A note the radar hasn't picked up within an hour is re-sent the next time
  she opens the page; one it still hasn't picked up after a day shows
  **Not in radar — tell Itz**, so nothing fails silently.

---

## What Ari gets

- **Only Miami or fully remote.** Jobs elsewhere in the US are still collected (the Stats tab says how many) but not shown or emailed. A job in another city whose listing itself says "or Remote" is shown as Remote; a remote job that turns out to be hybrid is not. To widen it again, edit `config.yaml` → `locations.show` / `locations.email`.
- **Email**: sent only after a crawl finds something new that matches, so a few times a week rather than daily. Each email lists the new jobs with links, her own employers (★) first, then early-career roles, then Miami, then remote. It also flags when a watched careers page changes (e.g. Haute Living adds an editor role).
- **Dashboard** (written to talk to Ari, not like a data tool):
  - **For you** is the home page: a greeting, new roles at her own companies (★), then everything else new this week, early-career first, then Miami, then remote.
  - **All jobs** has search, plus filters tucked behind a button: where, kind of role, how you'd work (remote / hybrid / in person), early-career only. Every job is labelled **Early career** (says entry level, or asks for up to 2 years), nothing, or **Stretch** (asks for 3–4 years, or a manager title).
  - **Saved** and **Applied** are her own tracking, with a notes box on each job. On an applied job she picks where it stands (Applied, Heard back, Phone screen, Interview, Offer, Rejected…), and the Applied tab groups by that.
  - **Stats** counts what she sees: open jobs, new this week, how many are elsewhere in the US, a 30-day chart, her application pipeline, and whether the radar itself is healthy.
  - **Who I'm watching** explains which companies are checked automatically, which pages are watched and which to check herself, with an **Add a company** button.
  - **More places to look** has one-tap LinkedIn and Indeed searches, plus the alerts and newsletters to sign up for.
  - **Check for new jobs now** (top bar) starts a crawl; the dashboard updates in about 5 minutes.
- Saves, applications and notes are stored in her browser. **Back up my saves** downloads a file and **Restore a backup** reads it back, for moving to a new phone or laptop.

## What it covers, and what it doesn't

**Read automatically (22 employers, verified 19 Oct 2026):** The Drum, The
Infatuation (JPMorgan's board, filtered), Penske Media (Eater, Thrillist,
Billboard, Robb Report, WWD, Variety…), Axios (incl. Axios Miami), Miami Herald
/ el Nuevo Herald, NBCUniversal (Telemundo, NBC 6), TelevisaUnivision, Fever
(Secret Miami), Vox Media, Hearst, People Inc., Bustle Digital Group, Business
Insider, Morning Brew, Forbes, Semafor, The New York Times, Dow Jones, Red
Ventures (The Points Guy), Ad Age (Crain), Michelin Guide, OpenTable.

**Careers page watched for changes:** Haute Living, Miami New Times and Voice
Media Group, Modern Luxury, Time Out, WLRN, WPLG Local 10, Atlas Obscura,
Digiday, Puck, theSkimm.

**Job-search sources:** JSearch (Google for Jobs, free key), Adzuna (optional,
paid), Remotive (remote writing and marketing), We Work Remotely
(marketing), the NAHJ career centre feed.

**Not read automatically, by design:**
- **LinkedIn, Indeed, JournalismJobs, Mediabistro.** Their terms forbid
  automated reading. They're on the *More places to look* page as one-click searches,
  and Ari should subscribe to their own email alerts.
- **No public board:** World Red Eye, Ocean Drive, Cultured, Static Media. Ari
  should follow them on LinkedIn; the job-search sources catch them when they
  post elsewhere.
- **Board unreadable:** VICE (no public board found), BuzzFeed (Greenhouse board
  removed), Condé Nast (Workday board redirected to an error page on
  19 Oct 2026), CBS News Miami (SuccessFactors), Bloomberg (Avature), Resy (Amex
  Eightfold). Each has a link on the dashboard.

**Ownership changes found while building this:** Vox Media sold Eater and
Thrillist to Penske Media in June 2026, and NYMag/Vox.com went to Lupa Systems
in May. That's why Eater appears under Penske. The Infatuation is owned by
JPMorgan Chase, so its jobs are on JPMorgan's careers site.

---

## Maintaining it

All everyday changes are YAML edits. You can make them on github.com: open the
file → pencil icon → **Commit changes**. The dashboard republishes within a
couple of minutes.

| To… | Edit |
|---|---|
| add an employer | the dashboard's **Add a company** (lands in `data/inbox_employers.yaml`), or edit `employers.yaml`, or run `python scripts/add_employer.py "Name" "<careers URL>"` |
| change locations (Miami / remote only), role types, seniority, early-career rules, years cap | `config.yaml` |
| change the inbox topic or passcode | `config.yaml` → `inbox.ntfy_topic`; secret `RADAR_PASSCODE` |
| change who gets email / when | secrets (`ALERT_TO`) and `config.yaml` → `alerts` |
| change the crawl times | `.github/workflows/radar.yml` → `cron` (UTC) |
| add a "More places to look" link | `boards.yaml` |

**Health:** every run page on GitHub (**Actions** → a run → *Summary*) shows how
many sources were read and a table of any that failed. Failures are also listed
on the dashboard's **Stats** tab ("Is the radar healthy?"). Ari's emails never
include maintenance messages. If the *email* step fails, the run turns red and
GitHub emails you.

**Running locally (optional, for testing a change):**

```bash
cd ~/Desktop/"Ari's tracker"/ari-job-radar
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest -q                                   # 103 tests, no network needed
python src/crawl.py --only "the-drum" --dry-run       # one employer, writes nothing
cp .env.example .env    # fill in to test email locally
python src/notify.py --test
```

The crawl prints a percentage and the time remaining on every line.

**Known limits and caveats**
- GitHub pauses scheduled workflows in a repo with no activity for 60 days. The
  radar commits on every run that changes something, which counts as activity,
  but if the job market goes quiet for two months, check the Actions tab.
- "First seen" is the radar's clock, not the employer's posting date.
- An aggregator job (Adzuna and the like) is marked closed only after 7 days
  unseen, because keyword searches can drop a job that's still open.
- The role filter reads **titles**. Anything ambiguous is kept and flagged
  rather than dropped, e.g. *video / photo editing* or *4+ yrs asked*.
- Resend's free sender only delivers to the address that owns the Resend
  account. To email two people (say Ari and you), verify a domain in Resend and
  set the `RESEND_FROM` secret to an address on it; `ALERT_TO` accepts a
  comma-separated list.

## Files

```
config.yaml          what counts as relevant (locations, roles, seniority), sources, alerts
employers.yaml       the employer list (single source of truth)
boards.yaml          the "More places to look" links
src/adapters.py      one adapter per job-board platform (Greenhouse, Lever, Workday, Oracle, UKG, Workable…)
src/aggregators.py   Adzuna, JSearch, Remotive, RSS feeds
src/classify.py      role family, seniority, location bucket, work mode, early-career fit, relevant or not
src/crawl.py         the crawl and its state machine; writes data/state.json + data/radar.json
src/watch.py         careers-page change watcher
src/notify.py        the email
scripts/add_employer.py       add a row to employers.yaml from a URL (CLI and the function behind the button)
site/index.html      the dashboard (one static page)
.github/workflows/radar.yml      schedule + publish
.github/workflows/inbox.yml   every 15 min: a note in the inbox? start a crawl
src/inbox.py                  reads the dashboard's inbox (ntfy.sh) and adds the companies
data/inbox_employers.yaml     companies Ari added from the dashboard (written by the crawl)
tests/test_tracker.py         103 tests, incl. real job titles from 19 Oct 2026
ARI_SETUP.md         Ari's part: Resend in 5 minutes, alerts to subscribe to, questions
```
