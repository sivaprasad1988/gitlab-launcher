# GitLab Pipeline Launcher

Small local web app to start GitLab pipelines, watch their jobs and play manual jobs — plus an optional TYPO3 release watcher that triggers pipelines automatically when a new TYPO3 version is published.

Pure Python standard library, no dependencies. Runs on `127.0.0.1` only.

## Features

- **Project list** — GitLab projects (path + branch) managed in the UI, stored in `config.json`
- **Run pipelines** — normal run or "TYPO3 update" run, each with its own CI variables
- **Run all** — start the normal pipeline of every project at once (one confirmation, failures reported per project)
- **Promote to production** — after a successful pipeline on the staging branch, start a pipeline on the production branch (see below)
- **Job view** — pipeline status and jobs; play manual jobs with one click
- **Auto-play** — listed manual jobs (e.g. `build:main`) are started automatically once they become playable (polled every 15 s, max. 2 h)
- **TYPO3 watcher** — checks `get.typo3.org` for the latest release of a major version every `check_interval_hours`; on a new version it triggers the pipeline of every project with `auto_typo3.enabled` and shows a macOS notification
  - First check only stores a baseline, it does not trigger
  - Failed triggers are retried on the next check
- **Run history & log** — kept in `state.json` (last 100 runs, 200 log entries)

## Requirements

- Python 3 (stdlib only)
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
python3 app.py --no-watch    # without TYPO3 watcher
```

## Configuration

`config.json` (gitignored, see `config.example.json`):

| Key | Description |
|---|---|
| `port` | HTTP port (default `8787`) |
| `check_interval_hours` | TYPO3 check interval (default `6`) |
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

Changing a project's TYPO3 major resets its baseline (no immediate trigger).

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

Open <http://127.0.0.1:8787>. `config.json`, `state.json` and `.gitlab-token` are read from and written to this folder (mounted as `/data`), so they survive rebuilds.

| Task | Command |
|---|---|
| Update after code changes | `docker compose up -d --build` |
| Logs | `docker compose logs -f` |
| Restart | `docker compose restart` |
| Stop | `docker compose down` |

Notes:
- Port is published on `127.0.0.1` only. Host port must match `port` in `config.json` (default `8787`), otherwise the Host header check rejects requests.
- Stop any copy of `app.py` running in a terminal first (same port).
- macOS notifications (TYPO3 auto-trigger) do not work inside the container; see the Activity log instead.
- Environment: `LAUNCHER_DATA` (data folder, default: app folder), `LAUNCHER_HOST` (bind address, default `127.0.0.1`).

## Security

- Binds to `127.0.0.1` only
- `Host` header check against DNS rebinding
- `Origin` check and `application/json` requirement on POST against cross-site requests
- Token, config, state and logs are gitignored

## Files

| File | Purpose |
|---|---|
| `app.py` | Server, GitLab API client, TYPO3 watcher |
| `index.html` | Web UI |
| `config.example.json` | Example configuration |
| `Dockerfile`, `compose.yaml` | Docker setup |
| `config.json` | Your configuration (gitignored) |
| `state.json` | Runs, log, TYPO3 baselines (gitignored) |
| `.gitlab-token` | GitLab token (gitignored) |
