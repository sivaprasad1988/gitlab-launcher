#!/usr/bin/env python3
"""T3 Watchdock — local web app (Python stdlib only).

- Lists GitLab projects from config.json, runs pipelines, shows jobs, plays manual jobs.
- Background check of the TYPO3 release API; on a new release it triggers the
  pipelines of all projects with "auto_typo3.enabled" (intended: staging only).
- Token is read server-side from ~/.gitlab-token and never sent to the browser.

Code lives in the watchdock/ package, the web UI in static/.
"""
from watchdock.server import main

if __name__ == "__main__":
    main()
