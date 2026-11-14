"""GitLab API: token, pipelines, manual jobs, project lookup."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .core import STATE, TOKEN_FALLBACK, TOKEN_FILE, config, load, lock, log, now, save, stop_event


# ---------------------------------------------------------------- GitLab
def token_source():
    for name, f in (("app", TOKEN_FILE), ("home", TOKEN_FALLBACK)):
        if f.exists() and f.read_text(encoding="utf-8").strip():
            return name, f
    return None, None


def token():
    _, f = token_source()
    if not f:
        raise RuntimeError("No GitLab token stored. Add it in the app (GitLab token section).")
    return f.read_text(encoding="utf-8").strip()


def save_token(value):
    value = (value or "").strip()
    if not value or any(c.isspace() for c in value):
        raise RuntimeError("Token is empty or contains whitespace")
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    os.chmod(TOKEN_FILE, 0o600)
    log("GitLab token saved in app folder")


def test_token():
    """Check token against GitLab of the first project (or gitlab.gingco.de)."""
    projects = config()["projects"]
    url = (projects[0]["gitlab_url"] if projects else "https://gitlab.gingco.de").rstrip("/")
    req = urllib.request.Request(url + "/api/v4/user", headers={"PRIVATE-TOKEN": token()})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return f"Token valid for user '{json.load(r).get('username')}' on {url}"
    except urllib.error.HTTPError as e:
        return f"Token rejected by {url} (HTTP {e.code})"
    except urllib.error.URLError as e:
        return f"GitLab not reachable ({e.reason}); token saved but not verified"


def gitlab(project, method, path, body=None):
    url = (project["gitlab_url"].rstrip("/") + "/api/v4/projects/"
           + urllib.parse.quote(project["path"], safe="") + path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "PRIVATE-TOKEN": token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if detail.lstrip().startswith("<"):          # HTML error page (proxy/auth) -> status only
            detail = e.reason
        raise RuntimeError(f"GitLab HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitLab not reachable: {e.reason}") from None


def find_project(pid):
    for p in config()["projects"]:
        if p["id"] == pid:
            return p
    raise RuntimeError(f"Unknown project: {pid}")


def auto_play(project, pipeline_id, job_names, timeout=7200):
    """Poll pipeline; play listed manual jobs once they become playable."""
    names = set(job_names)
    deadline = time.time() + timeout
    while names and time.time() < deadline and not stop_event.is_set():
        try:
            pl = gitlab(project, "GET", f"/pipelines/{pipeline_id}")
            for j in gitlab(project, "GET", f"/pipelines/{pipeline_id}/jobs?per_page=100"):
                if j["name"] in names and j["status"] == "manual":
                    gitlab(project, "POST", f"/jobs/{j['id']}/play")
                    names.discard(j["name"])
                    log(f"Auto-play: '{j['name']}' started in pipeline #{pipeline_id} ({project['id']})")
            if pl["status"] in ("success", "failed", "canceled", "skipped"):
                break
        except Exception as e:
            log(f"Auto-play error in pipeline #{pipeline_id}: {e}")
        stop_event.wait(15)
    if names:
        log(f"Auto-play: not started in pipeline #{pipeline_id}: {', '.join(sorted(names))}")


def run_pipeline(project, variables, trigger, play_jobs=(), ref=None):
    ref = ref or project["ref"]
    body = {"ref": ref,
            "variables": [{"key": k, "value": str(v)} for k, v in variables.items()]}
    p = gitlab(project, "POST", "/pipeline", body)
    with lock:
        st = load(STATE, {})
        st.setdefault("runs", []).insert(0, {
            "t": now(), "project": project["id"], "pipeline": p["id"],
            "web_url": p.get("web_url"), "trigger": trigger, "ref": ref})
        del st["runs"][100:]
        save(STATE, st)
    log(f"Pipeline #{p['id']} started: {project['path']} @ {ref} ({trigger})")
    if play_jobs:
        threading.Thread(target=auto_play, args=(project, p["id"], list(play_jobs)), daemon=True).start()
    return p


def known_host(gitlab_url):
    """Only known GitLab hosts -- the token must not be sent anywhere else."""
    url = (gitlab_url or "").strip().rstrip("/")
    known = {p["gitlab_url"].rstrip("/") for p in config()["projects"]} | {"https://gitlab.gingco.de"}
    if url not in known:
        raise RuntimeError(f"Unknown GitLab host: {url}")
    return url


def gitlab_get(url, path):
    req = urllib.request.Request(url + "/api/v4" + path, headers={"PRIVATE-TOKEN": token()})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def search_projects(gitlab_url, q):
    url = known_host(gitlab_url)
    res = gitlab_get(url, "/projects?membership=true&simple=true&per_page=20&order_by=last_activity_at&search="
                     + urllib.parse.quote(q))
    return [{"path": x["path_with_namespace"], "name": x["name"]} for x in res]


def manual_jobs(project, ref):
    """Manual jobs of the CI config on ref (GitLab CI lint dry run)."""
    try:
        r = gitlab(project, "GET", "/ci/lint?dry_run=true&include_jobs=true&ref=" + urllib.parse.quote(ref, safe=""))
        return [j["name"] for j in r.get("jobs", []) if j.get("when") == "manual"]
    except Exception:
        return []


def typo3_major(project, ref):
    """Most frequent major of "typo3/cms-core" constraints in the repo (blob search), or None."""
    try:
        hits = gitlab(project, "GET", "/search?scope=blobs&search=" + urllib.parse.quote("typo3/cms-core")
                      + "&ref=" + urllib.parse.quote(ref, safe=""))
    except Exception:
        return None
    majors = [int(m) for h in hits for m in re.findall(r'"typo3/cms-core"\s*:\s*"[^0-9"]*(\d+)', h.get("data", ""))]
    return max(set(majors), key=majors.count) if majors else None


def project_info(gitlab_url, path, ref=""):
    """Everything the project form can pre-fill, read from GitLab."""
    p = {"gitlab_url": known_host(gitlab_url), "path": path.strip().strip("/")}
    info = gitlab(p, "GET", "")
    branches = [b["name"] for b in gitlab(p, "GET", "/repository/branches?per_page=100")]
    default = info.get("default_branch")
    ref = ref.strip() or ("staging" if "staging" in branches else default)
    prod = "production" if "production" in branches else (default if default != ref else "")
    return {"name": info.get("name"), "default": default, "branches": branches, "ref": ref,
            "auto_play": manual_jobs(p, ref), "prod_ref": prod,
            "prod_play": manual_jobs(p, prod) if prod else [], "major": typo3_major(p, ref)}


def promote(project, source_id):
    """Run the production pipeline, only after a successful staging pipeline."""
    prod = project.get("production") or {}
    if not prod.get("ref"):
        raise RuntimeError("No production branch configured for this project")
    src = gitlab(project, "GET", f"/pipelines/{source_id}")
    if src.get("ref") != project["ref"] or src.get("status") != "success":
        raise RuntimeError(f"Pipeline #{source_id} is not a successful '{project['ref']}' pipeline "
                           f"(ref '{src.get('ref')}', status '{src.get('status')}')")
    return run_pipeline(project, prod.get("variables", {}), f"promote #{source_id} -> {prod['ref']}",
                        prod.get("auto_play", []), ref=prod["ref"])
