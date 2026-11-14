# Plan: Login, Database Storage, GitLab Token Encryption - Gingco Communication - Version: 01 - 11 2026

Status: analysis only, nothing implemented yet.

## 1. Current state

- No authentication. Protection is limited to: bind on `127.0.0.1`, `Host` header check (DNS rebinding), `Origin` check (`watchdock/server.py` `_allowed()`).
- All data is stored in files in the data folder (`LAUNCHER_DATA`, Docker: `/data`):
  - `config.json` — projects and settings (`load()` / `save()` in `watchdock/core.py`)
  - `state.json` — runs, log, scheduler, TYPO3 baselines, mail watches (~25 load/save call sites in `watcher.py`, `scheduler.py`, `gitlab.py`, `projects.py`, `server.py`, `core.py`)
  - `t3monitor/<pid>.json` — history per project, max. 300 snapshots (`t3monitor.py`)
  - Secret files, mode `600`: `.gitlab-token`, `.teams-webhook`, `.smtp-password`, `*.t3monitor-key`, `*.t3monitor-auth`, `*.t3monitor-staging-key`, `*.t3monitor-staging-auth` (~15 call sites)
- No automated tests in the repository.

## 2. Login

Open decisions:

- User model: one admin account, or several named users (log shows who did what).
- First password: CLI command (`python app.py set-password`, recommended), first-visit setup page, or env variable.

Planned approach (stdlib only):

- Password hash with `hashlib.scrypt`, stored in the database (`users` table).
- Session cookie: random token (`secrets`), `HttpOnly`, `SameSite=Strict`, `Secure` on HTTPS.
- Login rate limit, logout, session expiry.
- All `/api/*` routes and the UI require a valid session.

## 3. Database instead of files (decision: full database)

- Engine: SQLite via stdlib `sqlite3`, so the "no Python packages" rule still holds.
- Tables: `projects`, `settings`, `runs`, `log`, `scheduler`, `t3m_snapshots`, `secrets`, `users`, `sessions`.
- Effort (estimate): 1–2 days. Touches all 8 modules in `watchdock/`, plus a one-time import script for the existing files.
- Technical points:
  - Threads + existing `RLock`: one connection per thread (or `check_same_thread=False`) and WAL mode.
  - Database file mode `600`, gitignored, in the data folder (works with the Docker bind mount).
  - Run history and log no longer need to be rewritten in full on every change; the t3monitor history becomes queryable.
- Risks:
  - No tests: every feature needs a manual check after the change.
  - ASSUMPTION (not verified): `config.json` is edited by hand or copied from `config.example.json` (documented in the README). A database ends hand-editing unless a JSON import/export is kept.

## 4. Encrypting the GitLab token

The token cannot be made impossible to recover. The app must send it to GitLab in plain text on every API call, so it must be able to decrypt it. Whoever has both the database and the key can decrypt it too. A one-way hash only works for passwords that are checked, not for a token that must be sent.

Options, weakest to strongest:

1. Key in an env variable or Docker secret, kept outside the data folder; token encrypted in the database.
   - Protects against: stolen database file, leaked backup, accidental commit.
   - Does not protect against: shell access to the host or container.
2. Key derived from the login password (scrypt), held in memory only while the app runs.
   - Protects against: everything in option 1, plus anyone who has the files but not the password.
   - Cost: after every restart, the TYPO3 watcher and scheduler cannot trigger pipelines until someone logs in once.
3. Make the token less valuable (in addition to 1 or 2):
   - Project or group access token instead of a personal one, limited to the needed projects.
   - Expiry date and rotation.
   - `api` scope is still needed, because the app plays manual jobs.

Technical limit: the Python standard library has no AES.

- Use the existing `openssl` dependency: AES-256-CBC plus an HMAC from stdlib `hmac`, key passed via `-pass fd:` / `env:` so it never shows in `ps`. `openssl enc` does not support GCM.
- Or add the `cryptography` package, which breaks the "stdlib only" rule.

ASSUMPTION: the scheduler must run unattended after a restart. If so, option 2 conflicts with it.

Recommendation: option 1 + option 3. Option 2 only if blocked automatic runs after a restart are acceptable.

## 5. Validation

- [x] All storage goes through `load` / `save` / secret helpers — BELEGT (grep, ~45 call sites)
- [x] SQLite needs no third-party package — BELEGT (`sqlite3` is stdlib)
- [x] No tests in the repository — BELEGT (`git ls-files`)
- [x] Token must be decryptable for GitLab API calls — BELEGT (`gitlab.py` sends it on each request)
- [x] No AES in the Python stdlib — BELEGT
- [x] `openssl` is already a dependency — BELEGT (README, `t3monitor.py`)
- [x] `openssl enc` does not support GCM — BELEGT (documented OpenSSL limitation)
- [ ] Effort estimates — judgement, not measured

## 6. Next steps (when started)

1. Decide the login open points (section 2) and the encryption option (section 4).
2. Database layer + import script for existing files.
3. Login and sessions on top of the database.
4. Encrypted secrets storage.
5. Manual test of all features, README update.

---

Intern · Autor: S. Sisupalan
