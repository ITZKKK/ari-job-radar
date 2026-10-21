# Ari's Job Radar

Watches job boards for full-time writer / editor / reporter / content-creator /
PR / marketing roles (plus social media) in Miami, or fully remote (a New York
job that says "or Remote" counts), with Miami first. It emails Ari one summary
first thing each morning when something new has come in, and keeps a dashboard
she can open from any phone or laptop.

It's built like Itz's IB tracker (same adapters-not-scrapers design, same
"closed only when a board was read successfully" rule, same page watcher),
**but it runs entirely in the cloud.** GitHub Actions crawls three times a day,
GitHub Pages hosts the dashboard and Resend sends the email. Nothing runs on
Ari's or Itz's Mac, and it costs nothing.

```
GitHub Actions (three times a day: about 7am, 1pm and 7pm New York time in summer, an hour earlier in winter)
  ├─ src/crawl.py    reads 53 employer job boards + job-search APIs,
  │                  watches 27 careers pages, keeps history in data/state.json
  ├─ src/notify.py   the morning run emails Ari a summary of the NEW matching jobs (via Resend)
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
| `RESEND_API_KEY` | Ariella's Resend API key (starts `re_`, **Sending access** only) | yes, for email |
| `ALERT_TO` | **the exact email Ariella's Resend account was created with** (the free sender delivers only there) | yes, for email |
| `ADZUNA_APP_ID` | from developer.adzuna.com (step 5) | optional, paid |
| `ADZUNA_APP_KEY` | same place | optional, paid |
| `JSEARCH_RAPIDAPI_KEY` | from rapidapi.com (step 6) | recommended (free), but **not subscribed yet**, see step 6 |

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
gives 200 calls/month, and the radar uses 5 a day (about 150 a month), all Miami
searches on her role types, Spanish roles and her beats.

1. rapidapi.com → sign up → search **JSearch** (by OpenWeb Ninja / letscrape) → **Pricing** → **Basic (free)** → Subscribe.
2. Copy the **X-RapidAPI-Key** shown on the endpoint page → secret `JSEARCH_RAPIDAPI_KEY`.

**Status, 21 Oct 2026: JSearch is switched off.** The key in the secret isn't
subscribed to JSearch (every call answers 403 "You are not subscribed to this
API"), so `config.yaml` → `aggregators.jsearch.enabled` is `false`. To fix: do
step 1 with the same RapidAPI account the key belongs to, then set `enabled:
true`. The searches are already written; it works from the next run.

### 7. First run

The push in step 2 already started one. Open the repo's **Actions** tab:

- If GitHub asks, click **"I understand my workflows, go ahead and enable them"**.
- The **radar** workflow takes about 3–5 minutes. When it's green, the dashboard is at
  **`https://itzkkk.github.io/ari-job-radar/`** (also shown on the run page under *deploy*).
- To crawl again right now: **Actions** → **radar** → **Run workflow**.

If the first run happened before the secrets existed, just click **Run workflow**
once more. The first run that has email credentials sends Ari a single
"your radar is live" email. After that she gets **one summary first thing each
morning** (the first run after 6am New York time: around 7am in summer, 6am in
winter) listing what's new since the last one; a morning with nothing new sends
nothing, and jobs found by the afternoon and evening runs wait for the next
morning. To send one right away: **Actions** → **radar** → **Run workflow** →
tick **email_now**.

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

Built on what Ariella told us on 21 Oct 2026 (recorded in `config.yaml` → `owner`):
3 years of work, 2 in newsrooms, graduated July 2025; Miami, in person or hybrid
preferred, remote OK but last; full-time only; writer, editor, reporter, content
creator, PR, marketing; beats food, hospitality, travel, fashion, lifestyle,
culture; native Spanish; one summary email each morning.

- **Miami first, remote last.** Only Miami & South Florida or fully remote jobs are shown or emailed. Jobs elsewhere in the US are still collected (the Stats tab says how many). A job in another city whose listing itself says "or Remote" is shown as Remote; a remote job that turns out to be hybrid is not. Miami jobs come before remote ones everywhere: home page, All jobs, and the email, where remote jobs have their own section. To widen it again, edit `config.yaml` → `locations.show` / `locations.email`; the order comes from `locations.preferred`.
- **Full-time only.** Each job is read as full-time, part-time, freelance, contract / temporary, internship or not stated: from the board's own field where it has one (Lever, Ashby, Workable, SmartRecruiters, UKG, Recruitee, Breezy, BambooHR, Workday, Adzuna, Remotive, We Work Remotely), else the title ("Freelance Writer", "Part-time…", "PT", "On Call", "(Contract)"), else clear phrases in the description ("This is a part-time position"). Only full-time and not-stated jobs are shown or emailed; the rest are counted on Stats as hidden. Fellowships are full-time jobs with an end date: kept, and labelled **Fellowship**.
- **Her beats.** Jobs on food, hospitality, travel, fashion, lifestyle or culture get a badge (e.g. **Food & dining**) and rank higher. The title decides, plus the company name a job-search source gives ("Eater"), plus `topics:` on the rows in `employers.yaml` whose every job is on a beat (cruise lines, hotels, restaurant groups, museums, record labels…). The employer's `group` text is not read.
- **Spanish.** A job that mentions Spanish, bilingual, español, Hispanic, Latino / Latina, Latin music or Latin America, Telemundo or Univision, or has a Spanish title (redactora, periodista, coordinador de mercadeo, comunicaciones…) gets a **Spanish** badge and ranks a little higher; so does every job at a row marked `spanish: true` (TelevisaUnivision). Spanish titles count like their English ones.
- **Good fit.** Every job is labelled **Good fit** (says entry level, or asks for up to 3 years), nothing (experience not stated), or **Stretch** (asks for 4–5 years, or a manager title). Jobs asking for 6+ years are hidden.
- **PR agencies.** At employers marked `industry: pr` in `employers.yaml`, "Account Coordinator / Account Executive" counts as a PR job (it is the entry-level PR title there); everywhere else an Account Executive is a sales job and stays out.
- **Email**: one summary first thing each morning (around 7am in summer, 6am in winter), listing everything new since the last one; skipped on a morning with nothing new. Miami first, then remote in its own section; within each section, companies on her list (★) first, then jobs on her beats, then good fits. It also flags when a watched careers page changes (e.g. Haute Living adds an editor role). The one-off "your radar is ready" email doesn't replace that morning's summary, and the jobs it counted aren't sent again as new.
- **Dashboard** (written to talk to Ari, not like a data tool):
  - **For you** is the home page: a greeting, new roles at her own companies (★), then new Miami jobs this week, then new remote jobs, then what's still open in Miami.
  - **All jobs** has search, plus filters tucked behind a button: where, kind of role, how you'd work (remote / hybrid / in person), good fit only, my beats only, Spanish roles only.
  - **Saved** and **Applied** are her own tracking, with a notes box on each job. On an applied job she picks where it stands (Applied, Heard back, Phone screen, Interview, Offer, Rejected…), and the Applied tab groups by that.
  - **Stats** counts what she sees: open jobs, new this week, how many are elsewhere in the US, a 30-day chart, her application pipeline, and whether the radar itself is healthy.
  - **Who I'm watching** explains which companies are checked automatically, which pages are watched and which to check herself, with an **Add a company** button.
  - **More places to look** has one-tap LinkedIn and Indeed searches, plus the alerts and newsletters to sign up for.
  - **Check for new jobs now** (top bar) starts a crawl; the dashboard updates in about 5 minutes.
- Saves, applications and notes are stored in her browser. **Back up my saves** downloads a file and **Restore a backup** reads it back, for moving to a new phone or laptop.

## What it covers, and what it doesn't

**Read automatically (53 employers; 22 checked 19 Oct 2026, 31 added 21 Oct 2026):** The Drum, The Infatuation (JPMorgan's board, filtered), Penske Media (Eater, Thrillist, Billboard, Robb Report, WWD, Variety…), Axios (incl. Axios Miami), Miami Herald / el Nuevo Herald, NBCUniversal (Telemundo, NBC 6), TelevisaUnivision, Fever (Secret Miami), Vox Media, Hearst, People Inc., Bustle Digital Group, Business Insider, Morning Brew, Forbes, Semafor, The New York Times, Dow Jones, Red Ventures (The Points Guy), Ad Age (Crain), Michelin Guide, OpenTable. New on Ari's beats: travel and hospitality — Norwegian Cruise Line Holdings, Carnival Corporation, Virgin Voyages, Four Seasons (its South Florida hotels), Soho House, SH Hotels (1 Hotels), Loews Hotels, Accor & Ennismore in Miami (Delano, SLS, Mondrian), Seminole Hard Rock; food and drink — Restaurant Brands International (Burger King, Popeyes), Bacardi, Pollo Tropical; fashion and lifestyle — Perry Ellis, Celsius, Chewy, The Real Deal; music and entertainment — Live Nation (Miami), Warner Music (incl. Warner Music Latina), Sony Music (Latin), Universal Music, the Recording Academy and Latin Recording Academy, iHeartMedia, Sony Pictures, the Miami HEAT; Spanish-language — Warner Bros. Discovery (CNN en Español); PR and marketing agencies — Edelman, Ogilvy, 5WPR, Max Borges Agency, Zubi; and the University of Miami.

**Careers page watched for changes:** Haute Living, Miami New Times and Voice Media Group, Modern Luxury, Time Out, WLRN, WPLG Local 10, Atlas Obscura, Digiday, Puck, theSkimm, plus the Greater Miami Convention & Visitors Bureau, Subway, The Bass, the Arsht Center, Miami City Ballet, Frost Science, Art Basel, Spanish Broadcasting System, WSVN 7News, 6AM City (MIA Today), and the PR agencies rbb Communications, Durée & Company, BoardroomPR, J Public Relations, Nike Communications and Zimmerman.

**Board unreadable (link on the dashboard), new:** Royal Caribbean Group, MSC Cruises, Azamara, Windstar, Fontainebleau, Faena, Acqualina, Groot Hospitality, Discover The Palm Beaches, PAMM, Gannett / Palm Beach Post, FIU, Miami Dade College, Publicis, Inter Miami, and Hard Rock Stadium (Dolphins, F1 Miami Grand Prix, Miami Open). The existing "no public board" and "board unreadable" rows (VICE, BuzzFeed, Condé Nast, CBS News Miami, Bloomberg, Resy, World Red Eye, Ocean Drive, Cultured, Static Media) stay as they were.

**Job-search sources:** JSearch (Google for Jobs, free key; off until the key
is subscribed, see setup step 6), Adzuna (optional, paid), Remotive (remote writing and marketing), We Work Remotely
(marketing), the NAHJ and NABJ career centre feeds.

**Not read automatically, by design:**
- **LinkedIn, Indeed, JournalismJobs, Mediabistro.** Their terms forbid
  automated reading. They're on the *More places to look* page as one-click searches,
  and Ari should subscribe to their own email alerts.
- **No public board:** World Red Eye, Ocean Drive, Cultured, Static Media. Ari
  should follow them on LinkedIn; the job-search sources catch them when they
  post elsewhere.

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
| change locations (Miami / remote only), role types, seniority, years cap | `config.yaml` → `locations`, `roles`, `seniority` |
| change which place comes first (Miami) | `config.yaml` → `locations.preferred` |
| change "Good fit" (up to 3 years) | `config.yaml` → `fit.early_max_years` (labels follow the number) |
| show part-time / freelance / contract jobs too | `config.yaml` → `employment.show` (e.g. add `part_time`) |
| change her beats or what counts as one | `config.yaml` → `topics`, `topics_description_min_words` |
| give an employer whose every job is on a beat its beats (a cruise line, a museum) | `employers.yaml` → `topics: [travel, hospitality]` on its row |
| mark a Spanish-language employer (every job gets the Spanish badge) | `employers.yaml` → `spanish: true` on its row |
| make an employer's own place names count as Miami ("Four Seasons Surf Club") | `employers.yaml` → `miami_match: 'surf club'` on its row |
| mark a PR / marketing agency (Account Coordinator = PR job) | `employers.yaml` → `industry: pr`; the titles are in `config.yaml` → `roles.agency_titles` |
| change what counts as a Spanish job | `config.yaml` → `languages.spanish_markers` |
| change the inbox topic or passcode | `config.yaml` → `inbox.ntfy_topic`; secret `RADAR_PASSCODE` |
| change who gets email | secret `ALERT_TO` |
| change when the email goes: daily or after every crawl, the hour, empty mornings, remote section | `config.yaml` → `alerts.digest`, `send_after_local_hour`, `send_when_empty`, `remote_last` |
| send an email right now | **Actions** → **radar** → **Run workflow** → tick **email_now** |
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
python -m pytest -q                                   # 248 tests, no network needed
python src/crawl.py --only "the-drum" --dry-run       # one employer, writes nothing
python src/notify.py --preview                        # writes data/email_preview.html, sends nothing
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
- **Full-time** is only as good as the listing. Greenhouse boards don't say,
  so there it comes from the title and the description; a part-time job that
  never says so anywhere is shown (as "not stated"), never the other way round.
- **Workday** job lists give no description and sometimes just "3 Locations",
  so the crawl opens the job's own page for each relevant one (full-time or
  not, every location, the years asked for), at most 25 per employer per run;
  the rest are read on the next runs. After a rule change in `config.yaml`
  those pages are read again.
- **Beats** come from the title (and a job-search source's company name), plus
  `topics:` on the employer's row. An employer with every kind of job (Penske
  Media, Hearst) gets no row beats: its Eater jobs are food jobs because the
  title says Eater.
- **The morning email** follows GitHub's schedule: the 11:21 UTC run, around
  7am New York time in summer and 6am in winter (cron doesn't move for
  daylight saving), and GitHub sometimes starts scheduled runs late at busy
  times. A crawl started from the dashboard before 6am doesn't email.
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
src/classify.py      role family, seniority, location bucket, work mode, fit, full-time or not, beats, Spanish, relevant or not
src/crawl.py         the crawl and its state machine; writes data/state.json + data/radar.json
src/watch.py         careers-page change watcher
src/notify.py        the morning summary email
scripts/add_employer.py       add a row to employers.yaml from a URL (CLI and the function behind the button)
site/index.html      the dashboard (one static page)
.github/workflows/radar.yml      schedule + publish
.github/workflows/inbox.yml   every 15 min: a note in the inbox? start a crawl
src/inbox.py                  reads the dashboard's inbox (ntfy.sh) and adds the companies
data/inbox_employers.yaml     companies Ari added from the dashboard (written by the crawl)
tests/test_tracker.py         248 tests, incl. real job titles from 19 Oct 2026
ARI_SETUP.md         Ari's part: Resend (done), alerts to subscribe to, what she told us
```
