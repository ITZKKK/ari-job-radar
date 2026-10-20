/**
 * ari-job-radar relay: a Cloudflare Worker (free tier) so Ari can add a
 * company or start a crawl from the dashboard WITHOUT a GitHub account.
 *
 * The page POSTs {passcode, kind, company, url, linkedin, target}. This checks
 * the passcode and opens the same GitHub issue the dashboard's GitHub forms
 * would ("Add company: …" or "Refresh now"), which
 * .github/workflows/requests.yml then handles exactly as before. Nothing
 * else: no reads, no other repos, no storage.
 *
 * Settings (Worker → Settings → Variables and secrets):
 *   GITHUB_TOKEN   secret   fine-grained token, THIS repo only, Issues: read and write
 *   PASSCODE       secret   the word Ari types once on the page
 *   REPO           text     ITZKKK/ari-job-radar
 *   ALLOW_ORIGIN   text     https://itzkkk.github.io   (where the dashboard lives)
 * Then put the worker's URL in config.yaml → github.relay_url (README, step 9b).
 */

// Must match .github/ISSUE_TEMPLATE/add-company.yml and scripts/handle_request.py.
const LABELS = { company: "Company name", url: "Website or careers page", linkedin: "LinkedIn page", target: "Your list" };
const TARGET_OPTION = "This is one of my picks (give it a ★)";
const MAX = { company: 80, url: 500, linkedin: 200 };

export default {
  async fetch(req, env) {
    const cors = {
      "Access-Control-Allow-Origin": env.ALLOW_ORIGIN || "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "content-type",
      "Vary": "Origin",
    };
    const json = (obj, status = 200) =>
      new Response(JSON.stringify(obj), { status, headers: { ...cors, "content-type": "application/json" } });

    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (req.method !== "POST") return json({ ok: false, error: "POST only" }, 405);
    if (!env.GITHUB_TOKEN || !env.REPO || !env.PASSCODE) {
      return json({ ok: false, error: "the relay isn’t set up yet (GITHUB_TOKEN, REPO and PASSCODE are needed)" }, 500);
    }

    let body;
    try { body = await req.json(); } catch { body = null; }
    if (!body || typeof body !== "object") return json({ ok: false, error: "bad request" }, 400);
    if (!same(String(body.passcode || ""), env.PASSCODE)) {
      await new Promise(r => setTimeout(r, 1500));   // slow down guessing
      return json({ ok: false, error: "wrong passcode" }, 401);
    }

    const kind = body.kind === "refresh" ? "refresh" : body.kind === "add-company" ? "add-company" : null;
    if (!kind) return json({ ok: false, error: "unknown request" }, 400);

    let title, text, labels;
    if (kind === "refresh") {
      title = "Refresh now"; labels = ["refresh"]; text = "Sent from the dashboard.";
    } else {
      const s = k => String(body[k] || "").replace(/\s+/g, " ").trim().slice(0, MAX[k]);
      const company = s("company"), url = s("url"), linkedin = s("linkedin");
      if (!company) return json({ ok: false, error: "the company needs a name" }, 400);
      if (url && !/^https?:\/\/[^\s"'<>]+$/.test(url)) return json({ ok: false, error: "that website address doesn’t look right" }, 400);
      if (linkedin && !/^https?:\/\/(www\.)?linkedin\.com\/company\/[\w%.-]+\/?$/i.test(linkedin)) {
        return json({ ok: false, error: "that LinkedIn link doesn’t look right" }, 400);
      }
      title = `Add company: ${company}`; labels = ["add-company"];
      // The same markdown GitHub renders for the issue form, so requests.yml parses it unchanged.
      // (Text before the first "###" is ignored by the parser.)
      const field = (label, value) => `### ${label}\n\n${value || "_No response_"}`;
      text = ["Sent from the dashboard.", field(LABELS.company, company), field(LABELS.url, url), field(LABELS.linkedin, linkedin),
              `### ${LABELS.target}\n\n- [${body.target ? "x" : " "}] ${TARGET_OPTION}`].join("\n\n");
    }

    const r = await fetch(`https://api.github.com/repos/${env.REPO}/issues`, {
      method: "POST",
      headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ari-job-radar-relay", "content-type": "application/json" },
      body: JSON.stringify({ title, body: text, labels }),
    });
    if (!r.ok) return json({ ok: false, error: `GitHub answered ${r.status}` }, 502);
    const issue = await r.json();
    return json({ ok: true, kind, issue_url: issue.html_url });
  },
};

// Constant-time compare, so the passcode can't be guessed one letter at a time.
function same(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}
