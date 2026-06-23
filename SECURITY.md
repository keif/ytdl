# Security Policy

## Intended Use

`ytdl` is a **single-operator, self-hosted tool**. It is built to run on your
own LAN or behind a VPN with one trusted user (you), against the file system
and browser cookie store of the machine that runs it.

It is **not** intended for public hosting. Read [Public Hosting](#public-hosting)
below before exposing this service to anyone outside a trusted boundary.

## Reporting a Vulnerability

If you find a security-impacting bug — credential leak, sandbox escape from
yt-dlp invocation, path traversal via `output_dir`, SQL injection in the
queue layer, or anything else with real blast radius — please **do not file
a public issue**.

Email: `anthropic@baker.is`

Include:

- A description of the issue and its impact
- Steps to reproduce, ideally a minimal failing case
- The git commit SHA you tested against (`git rev-parse HEAD`)
- Whether you've notified anyone else (yt-dlp upstream, dependency authors,
  etc.)

You will get an acknowledgement within a few days. Fixes ship via a normal
PR after a private patch window if the bug is severe enough to warrant one.

## Public Hosting

**Do not host a public instance of this software.** Concretely:

1. **YouTube's Terms of Service prohibits downloading.** Public proxies have
   historically received DMCA takedowns and lost their hosting providers.
   See yt-dlp's own README on running public instances.
2. **Cookies leak the operator's identity.** The cookies auto-detect feature
   reads the *operator's* browser session. Every download a stranger triggers
   goes out attached to your YouTube account. Your account will be banned
   within minutes for ToS violation, and any private/age-gated content you
   have access to gets exfiltrated through your credentials.
3. **No authentication or per-user isolation.** Anyone with the URL can submit
   jobs, view the queue, and download other users' files. There is no rate
   limiting, no quotas, and no URL allowlist.
4. **No abuse handling.** If a stranger downloads infringing content via your
   server, the legal liability traces to you, not them.

If you want to share the tool with a small group of people you trust, the
safe path is:

- Run on your LAN
- Or run on a small VPS and expose it only over Tailscale / WireGuard / SSH
  tunnel + HTTP basic auth

If your goal is "anyone on the internet can use this," this is the wrong
codebase. Build something else with auth, isolation, abuse handling, and a
real legal posture.

## Dependency Security

Dependabot is configured to track `pip`, `npm`, `docker`, and
`github-actions` ecosystems on a weekly cadence. Vulnerability advisories
are surfaced through GitHub's Dependabot alerts in addition to PR
notifications.

When a Dependabot PR lands, run `uv sync` (Python) and
`pnpm install --frozen-lockfile` (frontend) to refresh the lockfiles before
committing.
