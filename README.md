# GitLab Pipeline Launcher

Small local web app to start GitLab pipelines, watch their jobs and play manual jobs — plus an optional TYPO3 release watcher that triggers pipelines automatically when a new TYPO3 version is published.

Pure Python standard library, no dependencies. Runs on `127.0.0.1` only.

## Features

- **Project list** — GitLab projects (path + branch) managed in the UI, stored in `config.json`
- **Run pipelines** — normal run or "TYPO3 update" run, each with its own CI variables
- **Job view** — pipeline status and jobs; play manual jobs with one click
- **Auto-play** — listed manual jobs (e.g. `build:main`) are started automatically once they become playable (polled every 15 s, max. 2 h)
- **TYPO3 watcher** — checks `get.typo3.org` for the latest release of a major version every `check_interval_hours`; on a new version it triggers the pipeline of every project with `auto_typo3.enabled` and shows a macOS notification
  - First check only stores a baseline, it does not trigger
  - Failed triggers are retried on the next check
- **Run history & log** — kept in `state.json` (last 100 runs, 200 log entries)

## Requirements

- Python 3 (stdlib only)
- GitLab personal access token with `api` scope
- macOS for notifications and the LaunchAgent (the app itself runs anywhere)

## Quick start

```bash
git clone git@github.com:sivaprasad1988/gitlab-launcher.git
cd gitlab-launcher
cp config.example.json config.json   # optional — projects can also be added in the UI
python3 app.py
```

Open <http://127.0.0.1:8787>, save your GitLab token in the *GitLab token* section, add projects.

Start without the TYPO3 watcher:

```bash
python3 app.py --no-watch
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

Changing a project's TYPO3 major resets its baseline (no immediate trigger).

## GitLab token

- Stored via the UI in `.gitlab-token` in the app folder (file mode `600`)
- Fallback: `~/.gitlab-token`
- Read server-side only, never sent to the browser
- Can be tested and removed in the UI

## Run as macOS LaunchAgent

`de.gingco.gitlab-launcher.plist` starts the app at login and keeps it running. Adjust the paths in the plist (Python binary, `app.py`, working directory, log file) to your setup, then:

```bash
cp de.gingco.gitlab-launcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/de.gingco.gitlab-launcher.plist
```

Stop / remove:

```bash
launchctl unload ~/Library/LaunchAgents/de.gingco.gitlab-launcher.plist
```

Output goes to `launcher.log`.

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
| `de.gingco.gitlab-launcher.plist` | macOS LaunchAgent |
| `config.json` | Your configuration (gitignored) |
| `state.json` | Runs, log, TYPO3 baselines (gitignored) |
| `.gitlab-token` | GitLab token (gitignored) |
| `launcher.log` | LaunchAgent output (gitignored) |
