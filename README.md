# GitLab Pipeline Launcher

Small local web app to start GitLab pipelines, watch their jobs and play manual jobs — plus:

- a **TYPO3 release watcher** that starts the staging pipelines automatically when a new TYPO3 version is published,
- **notifications** in Microsoft Teams (or by email) before and after these automatic runs,
- **TYPO3 status per project** (version, extensions, status checks and their history) read via the t3monitor extension,
- a **Scheduler** page to set when the automatic checks run.

Pure Python standard library, no Python packages. Uses the `openssl` command for t3monitor (built into macOS and the Docker image). Runs on `127.0.0.1` only.

## Features

- **Project list** — GitLab projects (path + branch) managed in the UI, stored in `config.json`
- **Project form pre-fill** — new projects start with defaults (staging/production, `build:main`, TYPO3 13, `TARGET_TASK_NAME=shup01`); branch, production branch and TYPO3 major are select boxes; when GitLab is reachable, project path search plus name, branches, manual jobs (CI lint) and TYPO3 major are read from GitLab
- **Run pipelines** — normal run or "TYPO3 update" run, each with its own CI variables
- **Run all** — start the normal pipeline of every project at once (one confirmation, failures reported per project)
- **Promote to production** — after a successful pipeline on the staging branch, start a pipeline on the production branch (see below)
- **Job view** — pipeline status and jobs; play manual jobs with one click
- **Auto-play** — listed manual jobs (e.g. `build:main`) are started automatically once they become playable (polled every 15 s, max. 2 h)
- **TYPO3 watcher** — checks `get.typo3.org` for the latest release of a major version on the schedule set in *Scheduler*; on a new version it announces the update in Teams, then triggers the pipeline of every project with `auto_typo3.enabled` (plus a macOS notification when not running in Docker)
  - First check only stores a baseline, it does not trigger
  - Failed triggers are retried on the next check
- **Notifications (Microsoft Teams, email)** — when a pipeline started by the TYPO3 watcher has finished (success, failed, canceled, skipped), a Teams message and/or email with status and link is sent; also when the auto-trigger could not start the pipeline. The t3monitor check sends a summary per scheduled run and alerts on new findings (see below). Set up in *Settings*
- **TYPO3 status (t3monitor)** — reads TYPO3 version, extensions and status checks from sites with the [t3monitor](https://github.com/BrainAppeal/t3monitor) extension, for the production and (optionally) the staging instance of a project; shown on the project page with a change history (see below)
- **Extension updates & changelogs** — newest compatible release per extension from Packagist (major updates marked separately); click an extension row to read the changelog of that release
- **TYPO3 overview on the home page** — table with TYPO3 version (↑ = update available) and errors/warnings of production and staging per project, a *versions differ* marker and the oldest last check; data from the last t3monitor check
- **Scheduler** — page to set when the TYPO3 release check and the t3monitor check run: every N hours or daily at a fixed time on chosen weekdays; shows last run, result and next run, with *Run now*
- **Run history & log** — kept in `state.json` (last 100 runs, 200 log entries)
- **Save & close** — Scheduler and Settings forms have *Save* (stay on the page) and *Save & close* (back to Projects)
- **Flash messages** — results and errors appear top right (green ✓ / red ⚠; 6 s, errors 12 s; click to close)
- **Dark / light theme** — switch in the header: *◐ Auto* (follows the system setting) → *☀ Light* → *☾ Dark*; the choice is saved in the browser (`localStorage`) and applied before the page is drawn

## Requirements

- Python 3 (stdlib only)
- `openssl` command (for t3monitor; present on macOS and in the Docker image)
- GitLab personal access token with `api` scope
- Docker (recommended) — or run directly with Python
- macOS for notifications (only when not running in Docker)

## Quick start

```bash
git clone git@github.com:sivaprasad1988/gitlab-launcher.git
cd gitlab-launcher
cp config.example.json config.json   # optional — projects can also be added in the UI
docker compose up -d --build         # runs permanently, see "Run with Docker"
```

Open <http://127.0.0.1:8787>, save your GitLab token in *Settings → GitLab token*, add projects.

Without Docker (runs only while the terminal is open):

```bash
python3 app.py               # with TYPO3 watcher
python3 app.py --no-watch    # TYPO3 release check paused (Scheduler)
```

## Configuration

`config.json` (gitignored, see `config.example.json`):

| Key | Description |
|---|---|
| `port` | HTTP port (default `8787`) |
| `scheduler.<task>` | Schedule per task (`typo3_release`, `t3monitor`): `enabled`, `mode` (`interval`/`daily`), `hours`, `time` (`HH:MM`), `days` (0 = Mon … 6 = Sun); set in the *Scheduler* page |
| `check_interval_hours` | Old setting: default interval of the TYPO3 release check until a schedule is saved |
| `projects[].id` | Stable ID, generated from `path` + `ref` |
| `projects[].name` | Display name |
| `projects[].gitlab_url` | GitLab base URL |
| `projects[].path` | Project path, e.g. `group/project` |
| `projects[].ref` | Branch to run the pipeline on |
| `projects[].variables` | CI variables for a normal run |
| `projects[].auto_play` | Manual jobs to auto-play after a normal run |
| `projects[].auto_typo3.enabled` | Enable TYPO3 auto-trigger for this project |
| `projects[].auto_typo3.major` | TYPO3 major version to watch, e.g. `13` |
| `projects[].auto_typo3.variables` | CI variables for a TYPO3 update run |
| `projects[].auto_typo3.auto_play` | Manual jobs to auto-play after a TYPO3 update run |
| `projects[].production.ref` | Production branch; empty = no promote button |
| `projects[].production.variables` | CI variables for the production run |
| `projects[].production.auto_play` | Manual jobs to auto-play after the production run |
| `projects[].t3monitor.url` | Site URL with the t3monitor extension; key is stored in `<project-id>.t3monitor-key` |
| `projects[].t3monitor.auth_user` | HTTP Basic auth user for password-protected sites (empty = none); password in `<project-id>.t3monitor-auth` |
| `projects[].t3monitor_staging.url` / `.auth_user` | Same for the staging instance; key in `<project-id>.t3monitor-staging-key`, password in `<project-id>.t3monitor-staging-auth` |
| `teams.enabled` | Send Teams messages for TYPO3 auto-trigger runs; webhook URL is stored in `.teams-webhook` |
| `t3monitor_interval_hours` | Old setting: default interval of the t3monitor check until a schedule is saved |
| `t3monitor_local_vendors` | Composer vendors of own extensions, hidden and not tracked in history (default `["gingco"]`; `dev-*` versions also count as local) |
| `email.enabled` | Send email notifications for TYPO3 auto-trigger runs |
| `email.host` / `email.port` / `email.security` | SMTP server; security `starttls`, `ssl` or `none` |
| `email.user` | SMTP login (empty = no login); password is stored in `.smtp-password` |
| `email.from` / `email.to` | Sender and list of recipients |

Changing a project's TYPO3 major resets its baseline (no immediate trigger).

## Scheduler

Page *Scheduler* (top menu) lists the automatic tasks:

| Task | Does |
|---|---|
| TYPO3 release check | Checks `get.typo3.org`; on a new release: Teams announcement, then the update pipelines on staging |
| t3monitor check (TYPO3 & extensions) | Reads TYPO3 version, extensions and status checks of every instance (production, staging) with t3monitor URL and key |

- Per task: on/off, *Every N hours* (0.25–168) or *Daily at a fixed time* on chosen weekdays; shows last run, result and next run; **▶ Run now**.
- Tasks run only while the launcher runs (checked every 30 s). A run missed while it was off starts right after the next start.
- Default until a schedule is saved: every 6 h (or the old `check_interval_hours` / `t3monitor_interval_hours`).
- `python3 app.py --no-watch` pauses the TYPO3 release check (shown as *paused*).

## TYPO3 status (t3monitor)

1. In the TYPO3 site: *Admin Tools → Settings → Extension Configuration → t3monitor → encryption_key* (64 characters).
2. In the launcher: project → **Edit** → *t3monitor – Production* and/or *t3monitor – Staging*: site URL and key → **Save changes**. Each instance has its own key (the staging site has its own `encryption_key`).
3. The project page shows one collapsible section per instance (*TYPO3 status – Production*, *TYPO3 status – Staging*). Closed, its header shows TYPO3 version, ↑ core update, errors, warnings, extension updates and the last check. Open: status boxes, then the collapsible lists *Extensions*, *Problems* (errors and warnings), *All status checks* and *History*. **↻ Refresh now** fetches immediately; otherwise on the schedule of the *t3monitor check* task in *Scheduler*.
4. The home page shows all projects in one table (production and staging side by side).

- Request: `?t3monitor=1&secret=<first 32 chars of the key>`; the answer is AES-encrypted with the last 32 chars (never sent), decrypted with `openssl`.
- Only technical data is stored (version, extensions, status checks) — no backend users or paths.
- **Extension updates:** for every public extension the newest stable release on [Packagist](https://packagist.org) whose `typo3/cms-core` requirement fits the site's TYPO3 version is looked up (column *Latest*). Rows with an update are tinted; major updates (e.g. 7.x → 8.0, may contain breaking changes) get an amber tint and a *major* chip. Counts appear in the section header and the *Extensions* box. Local extensions are skipped; packages not on Packagist show *not on Packagist*. Package lists are cached for 1 h; if Packagist is unreachable the t3monitor check still succeeds (*unknown*).
- **Changelog:** click an extension row to show the changelog of the latest version (when an update is available) or of the installed version. Sources in this order: GitHub/GitLab release notes of the tag, then the changelog file at the tag (`CHANGELOG.md`, `ChangeLog`, … — the section of that version, or the per-version file it links to). Links to the source, the full compare view (installed → latest) and the repository. Only packages hosted on github.com or gitlab.com; results are cached until restart.
- History in `t3monitor/<project-id>.json` (production) and `t3monitor/staging/<project-id>.json` (staging): a new entry only when something changes (TYPO3 version, extension added/removed/updated, status severity); otherwise "seen until" is updated. Last 300 changes are kept.
- Key file: mode `600`, never sent to the browser, removed with the project.
- Password-protected site (HTTP Basic auth): set *Basic auth user* and *password* in the project's t3monitor settings. The user is stored in `config.json`, the password in `<project-id>.t3monitor-auth` (mode `600`, never sent to the browser). Empty user = no basic auth.

## Notifications (Microsoft Teams, email)

Microsoft Teams (no mail server needed):

1. In Teams, open the channel → **⋯ → Workflows** → template **"Send webhook alerts to a channel"** (older name: "Post to a channel when a webhook request is received") → select team and channel → copy the webhook URL shown at the end.
2. *Settings → Microsoft Teams notifications*: paste the URL, tick *Send Teams messages*, **Save & send test message**.
3. To get a Teams alert for each message: channel → **⋯ → Channel notifications → Custom → All new posts: Banner and feed**.

Anyone who has the webhook URL can post into the channel — treat it like a password (never paste it into chats or tickets).

Email (optional, needs SMTP access): *Settings → Email notifications*: SMTP host, port, security, optional user/password, sender, recipients, tick *Send email notifications*, **Save & send test email**.

- Before the staging pipelines of a new TYPO3 release start, one message "New TYPO3 x released: starting update on staging" lists all affected projects (sent once per project and version, not again on retries).
- Only pipelines started automatically by the TYPO3 watcher are watched (not manual runs, Run all or promote).
- **t3monitor check, scheduled run** (also *Run now* in Scheduler): **one summary message per run** with every site (production and staging): TYPO3 version, errors, warnings, extension updates (`NEW` = first time reported, `MAJOR` = may contain breaking changes), new errors, and sites whose check failed.
- **t3monitor, ↻ Refresh now** on a project: a message only when something is new for that site — the check failed (once per new error text), new errors appeared (the first check of a site is the baseline), or a new extension update was found. Each extension version is reported as new only once.
- Teams Workflow webhooks answer *accepted* (HTTP 202) before the message is posted. If the test message is accepted but does not appear, open the run history of the Workflow in Teams (**Workflows → the flow → Run history**) to see why it failed.
- The pipeline status is polled every 60 s; the message is sent once it has finished. After 24 h without a final status a "NOT FINISHED" message is sent.
- If the pipeline could not be started, a "NOT STARTED" message is sent (on every failed retry).
- Pending pipelines are kept in `state.json` and watched again after a restart.
- Webhook URL (`.teams-webhook`) and SMTP password (`.smtp-password`) are stored with file mode `600` and never sent to the browser; leave the field empty to keep the stored value.
- Send errors are written to the Activity log.

## Promote to production

1. Run the staging pipeline (**▶ Run pipeline** or **▶ Run all**) and wait for `success`.
2. Merge `staging` into the production branch **in GitLab** — the launcher does not merge.
3. Open the successful staging pipeline in the launcher and click **🚀 Promote to production**.
4. The server re-checks via GitLab that the source pipeline is a successful staging pipeline, then starts a pipeline on the production branch with the production variables and auto-play jobs.

In the project form, the production branch is pre-filled from GitLab (`production` if it exists, otherwise the default branch) and the production auto-play jobs mirror the staging ones until edited. Run all and the TYPO3 auto-trigger never touch production.

## GitLab token

- Stored via the UI in `.gitlab-token` in the app folder (file mode `600`)
- Fallback: `~/.gitlab-token`
- Read server-side only, never sent to the browser
- Can be tested and removed in the UI

## Run with Docker (always on)

The container restarts automatically (`restart: unless-stopped`), also after a reboot as long as Docker Desktop starts at login (*Docker Desktop → Settings → General → Start Docker Desktop when you sign in*).

```bash
docker compose up -d --build
```

Open <http://127.0.0.1:8787>. All data — `config.json`, `state.json`, `t3monitor/` and the secret files (`.gitlab-token`, `.teams-webhook`, `.smtp-password`, `*.t3monitor-key`, `*.t3monitor-auth`, `*.t3monitor-staging-key`, `*.t3monitor-staging-auth`) — is read from and written to this folder (mounted as `/data`), so it survives rebuilds.

| Task | Command |
|---|---|
| Update after code changes (`app.py` or `index.html`) | `docker compose up -d --build` |
| Logs | `docker compose logs -f` |
| Restart | `docker compose restart` |
| Stop | `docker compose down` |

### Local hostname with HTTPS (optional)

Serve the app at <https://local-gitlabrunner.local> with a trusted [mkcert](https://github.com/FiloSottile/mkcert) certificate:

```bash
brew install mkcert && mkcert -install          # once: local CA (asks for password)
mkdir -p certs
mkcert -cert-file certs/local-gitlabrunner.local.pem \
       -key-file certs/local-gitlabrunner.local-key.pem local-gitlabrunner.local
echo "127.0.0.1 local-gitlabrunner.local" | sudo tee -a /etc/hosts
echo "::1 local-gitlabrunner.local" | sudo tee -a /etc/hosts   # avoids 5 s .local lookup delay
docker compose up -d --build
```

- http://local-gitlabrunner.local redirects to https (http://127.0.0.1:8787 stays plain HTTP as fallback)
- `certs/` is gitignored; the certificate is valid for ~2 years, re-run the `mkcert` command to renew
- Without the certificate files HTTPS is skipped and the app runs on http://127.0.0.1:8787 only
- Firefox may need `mkcert -install` again (own certificate store)

Notes:
- Ports are published on `127.0.0.1` only. Host port must match `port` in `config.json` (default `8787`), otherwise the Host header check rejects requests.
- Stop any copy of `app.py` running in a terminal first (same port).
- macOS notifications (TYPO3 auto-trigger) do not work inside the container; use Teams notifications or the Activity log instead.
- Environment: `LAUNCHER_DATA` (data folder, default: app folder), `LAUNCHER_HOST` (bind address, default `127.0.0.1`), `LAUNCHER_ALLOWED_HOSTS` (extra accepted hostnames, comma-separated), `LAUNCHER_TLS_CERT` / `LAUNCHER_TLS_KEY` / `LAUNCHER_TLS_PORT` (HTTPS listener, default port `8443`).

## Security

- Binds to `127.0.0.1` only (Docker: published on `127.0.0.1` only)
- Optional HTTPS with a local mkcert certificate
- `Host` header check against DNS rebinding
- `Origin` check and `application/json` requirement on POST against cross-site requests
- Secrets (GitLab token, Teams webhook URL, SMTP password, t3monitor keys and Basic auth passwords) are stored in separate files with mode `600`, read server-side only and never sent to the browser
- Token, secrets, config, state, t3monitor history and logs are gitignored
- Outgoing connections: GitLab (only to the GitLab URLs of configured projects), `get.typo3.org`, the Teams webhook / SMTP server if enabled, the t3monitor site URLs, `repo.packagist.org` (only public Composer package names are sent), and — when a changelog is opened — `api.github.com`, `raw.githubusercontent.com` or `gitlab.com` (public repository paths and tags only)

## Files

| File | Purpose |
|---|---|
| `app.py` | Server, GitLab API client, TYPO3 watcher, scheduler, notifications, t3monitor client |
| `index.html` | Web UI |
| `config.example.json` | Example configuration |
| `Dockerfile`, `compose.yaml` | Docker setup |
| `certs/` | mkcert certificate + key (gitignored) |
| `config.json` | Your configuration (gitignored) |
| `state.json` | Runs, log, TYPO3 baselines, scheduler results, pending notifications (gitignored) |
| `.gitlab-token` | GitLab token (gitignored) |
| `.smtp-password` | SMTP password (gitignored) |
| `.teams-webhook` | Teams webhook URL (gitignored) |
| `*.t3monitor-key` | t3monitor key per project (gitignored) |
| `*.t3monitor-auth` | Basic auth password per project for t3monitor (gitignored) |
| `*.t3monitor-staging-key`, `*.t3monitor-staging-auth` | Same for the staging instance (gitignored) |
| `t3monitor/` | t3monitor history per project (gitignored) |
