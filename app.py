#!/usr/bin/env python3
"""GitLab Pipeline Launcher — local web app (Python stdlib only).

- Lists GitLab projects from config.json, runs pipelines, shows jobs, plays manual jobs.
- Background check of the TYPO3 release API; on a new release it triggers the
  pipelines of all projects with "auto_typo3.enabled" (intended: staging only).
- Token is read server-side from ~/.gitlab-token and never sent to the browser.
"""
import datetime
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("LAUNCHER_DATA", BASE))  # config/state/token location (Docker: /data)
CONFIG = DATA / "config.json"
STATE = DATA / "state.json"
TOKEN_FILE = DATA / ".gitlab-token"              # stored by the app (chmod 600)
TOKEN_FALLBACK = Path.home() / ".gitlab-token"     # optional fallback
HOST = os.environ.get("LAUNCHER_HOST", "127.0.0.1")  # Docker: 0.0.0.0, published on 127.0.0.1 only
# extra Host header values, e.g. "local-gitlabrunner.local" (Docker publishes it on port 80)
ALLOWED_HOSTS = {h.strip() for h in os.environ.get("LAUNCHER_ALLOWED_HOSTS", "").split(",") if h.strip()}
# optional HTTPS listener (e.g. mkcert certificate); runs in addition to plain HTTP
TLS_CERT = os.environ.get("LAUNCHER_TLS_CERT")
TLS_KEY = os.environ.get("LAUNCHER_TLS_KEY")
TLS_PORT = int(os.environ.get("LAUNCHER_TLS_PORT", 8443))
TYPO3_API = "https://get.typo3.org/api/v1/major/{major}/release/latest"

lock = threading.RLock()
stop_event = threading.Event()


# ---------------------------------------------------------------- storage
def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def config():
    return load(CONFIG, {"port": 8787, "check_interval_hours": 6, "projects": []})


def log(msg):
    print(f"[{now()}] {msg}", flush=True)
    with lock:
        st = load(STATE, {})
        st.setdefault("log", []).insert(0, {"t": now(), "msg": msg})
        del st["log"][200:]
        save(STATE, st)


def notify(title, msg):
    """macOS notification; text passed as argv (no script injection)."""
    try:
        subprocess.run(
            ["osascript", "-e", "on run argv", "-e",
             "display notification (item 1 of argv) with title (item 2 of argv)",
             "-e", "end run", msg, title],
            check=False, timeout=10, capture_output=True)
    except Exception:
        pass


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


# ---------------------------------------------------------------- TYPO3 watcher
def latest_typo3(major):
    with urllib.request.urlopen(TYPO3_API.format(major=int(major)), timeout=30) as r:
        return json.load(r)["version"]


def check_typo3():
    projects = [p for p in config()["projects"] if p.get("auto_typo3", {}).get("enabled")]
    latest = {}
    for major in sorted({int(p["auto_typo3"]["major"]) for p in projects}):
        try:
            latest[major] = latest_typo3(major)
        except Exception as e:
            log(f"TYPO3 {major} check failed: {e}")

    for p in projects:
        major = int(p["auto_typo3"]["major"])
        if major not in latest:
            continue
        v = latest[major]
        with lock:
            st = load(STATE, {})
            prev = st.setdefault("typo3_seen", {}).get(p["id"])
        if prev is None:
            # First run: store baseline only, do not trigger.
            with lock:
                st = load(STATE, {})
                st.setdefault("typo3_seen", {})[p["id"]] = v
                save(STATE, st)
            log(f"Baseline TYPO3 {v} stored for {p['id']} (no trigger)")
        elif prev != v:
            try:
                pl = run_pipeline(p, p["auto_typo3"].get("variables", {}), f"TYPO3 {prev} -> {v}",
                                  p["auto_typo3"].get("auto_play", []))
                with lock:
                    st = load(STATE, {})
                    st["typo3_seen"][p["id"]] = v
                    save(STATE, st)
                notify("TYPO3 update started", f"{p['name']}: {v} (pipeline #{pl['id']})")
            except Exception as e:
                # Not marked as seen -> retried on next check.
                log(f"Auto-trigger failed for {p['id']} (TYPO3 {v}): {e}")
                notify("TYPO3 auto-update FAILED", f"{p['name']}: {e}")

    with lock:
        st = load(STATE, {})
        st["last_check"] = now()
        st["typo3_latest"] = {str(k): v for k, v in latest.items()}
        save(STATE, st)


def watcher():
    while not stop_event.is_set():
        try:
            check_typo3()
        except Exception as e:
            log(f"Watcher error: {e}")
        hours = float(config().get("check_interval_hours", 6))
        stop_event.wait(hours * 3600)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _allowed(self):
        port = self.server.server_address[1]
        ok = {f"127.0.0.1:{port}", f"localhost:{port}"} | ALLOWED_HOSTS
        if self.headers.get("Host") not in ok:          # DNS rebinding
            return False
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] not in ok:  # cross-site requests
            return False
        return True

    def _send(self, code, data, ctype="application/json"):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/":
                return self._send(200, (BASE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                cfg, st = config(), load(STATE, {})
                return self._send(200, {
                    "projects": cfg["projects"],
                    "interval": cfg.get("check_interval_hours", 6),
                    "token_source": token_source()[0],
                    "last_check": st.get("last_check"),
                    "typo3_latest": st.get("typo3_latest", {}),
                    "typo3_seen": st.get("typo3_seen", {}),
                    "runs": st.get("runs", [])[:30],
                    "log": st.get("log", [])[:50]})
            if u.path == "/api/branches":
                return self._send(200, branches(q["gitlab_url"][0], q["path"][0]))
            if u.path == "/api/pipeline":
                p = find_project(q["project"][0])
                pid = int(q["id"][0])
                return self._send(200, {
                    "pipeline": gitlab(p, "GET", f"/pipelines/{pid}"),
                    "jobs": gitlab(p, "GET", f"/pipelines/{pid}/jobs?per_page=100")})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        if not self._allowed() or not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(403, {"error": "forbidden"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            path = self.path
            if path == "/api/run":
                p = find_project(body["project"])
                if body.get("mode") == "promote":
                    return self._send(200, promote(p, int(body["from"])))
                src = p.get("auto_typo3", {}) if body.get("mode") == "update" else p
                trig = "manual TYPO3 update" if body.get("mode") == "update" else "manual"
                return self._send(200, run_pipeline(p, src.get("variables", {}), trig,
                                                    src.get("auto_play", [])))
            if path == "/api/play":
                p = find_project(body["project"])
                r = gitlab(p, "POST", f"/jobs/{int(body['job'])}/play")
                log(f"Manual job '{r.get('name')}' started in {p['id']}")
                return self._send(200, r)
            if path == "/api/token":
                save_token(body.get("token"))
                return self._send(200, {"msg": test_token()})
            if path == "/api/token/test":
                return self._send(200, {"msg": test_token()})
            if path == "/api/token/delete":
                TOKEN_FILE.unlink(missing_ok=True)
                log("GitLab token removed from app folder")
                return self._send(200, {"ok": True})
            if path == "/api/check-now":
                check_typo3()
                return self._send(200, {"ok": True})
            if path == "/api/projects":
                return self._send(200, add_project(body))
            if path == "/api/projects/update":
                return self._send(200, update_project(body))
            if path == "/api/projects/delete":
                with lock:
                    cfg = config()
                    cfg["projects"] = [p for p in cfg["projects"] if p["id"] != body["id"]]
                    save(CONFIG, cfg)
                log(f"Project removed: {body['id']}")
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def branches(gitlab_url, path):
    """Branch names + default branch. Only known GitLab hosts (token must not go elsewhere)."""
    url = gitlab_url.strip().rstrip("/")
    known = {p["gitlab_url"].rstrip("/") for p in config()["projects"]} | {"https://gitlab.gingco.de"}
    if url not in known:
        raise RuntimeError(f"Unknown GitLab host: {url}")
    p = {"gitlab_url": url, "path": path.strip().strip("/")}
    return {"default": gitlab(p, "GET", "")["default_branch"],
            "branches": [b["name"] for b in gitlab(p, "GET", "/repository/branches?per_page=100")]}


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


def _jobs(v):
    if isinstance(v, list):
        return [s.strip() for s in v if str(s).strip()]
    return [s.strip() for s in (v or "").split(",") if s.strip()]


def build_project(b, pid=None):
    path = (b.get("path") or "").strip().strip("/")
    ref = (b.get("ref") or "").strip()
    if not path or not ref:
        raise RuntimeError("Path and branch are required")
    return {
        "id": pid or (path + "-" + ref).replace("/", "-"),
        "name": (b.get("name") or "").strip() or path,
        "gitlab_url": (b.get("gitlab_url") or "https://gitlab.gingco.de").strip().rstrip("/"),
        "path": path,
        "ref": ref,
        "variables": b.get("variables") or {},
        "auto_play": _jobs(b.get("auto_play")),
        "auto_typo3": {
            "enabled": bool(b.get("auto_enabled")),
            "major": int(b.get("major") or 13),
            "variables": b.get("auto_variables") or {},
            "auto_play": _jobs(b.get("auto_typo3_play"))},
        "production": {
            "ref": (b.get("prod_ref") or "").strip(),
            "variables": b.get("prod_variables") or {},
            "auto_play": _jobs(b.get("prod_play"))}}


def update_project(b):
    pid = b["id"]                       # id stays stable (state/runs reference it)
    with lock:
        cfg = config()
        for i, old in enumerate(cfg["projects"]):
            if old["id"] == pid:
                new = build_project(b, pid)
                cfg["projects"][i] = new
                save(CONFIG, cfg)
                break
        else:
            raise RuntimeError(f"Unknown project: {pid}")
        if old.get("auto_typo3", {}).get("major") != new["auto_typo3"]["major"]:
            st = load(STATE, {})        # new major -> fresh baseline, no instant trigger
            st.get("typo3_seen", {}).pop(pid, None)
            save(STATE, st)
    log(f"Project updated: {pid}")
    return new


def add_project(b):
    project = build_project(b)
    pid = project["id"]
    with lock:
        cfg = config()
        if any(p["id"] == pid for p in cfg["projects"]):
            raise RuntimeError(f"Project already exists: {pid}")
        cfg["projects"].append(project)
        save(CONFIG, cfg)
    log(f"Project added: {pid}")
    return project


def main():
    port = int(config().get("port", 8787))
    srv = ThreadingHTTPServer((HOST, port), Handler)
    if "--no-watch" not in sys.argv:
        threading.Thread(target=watcher, daemon=True).start()
    print(f"GitLab Launcher: http://{HOST}:{port}", flush=True)
    if TLS_CERT and TLS_KEY and not (Path(TLS_CERT).exists() and Path(TLS_KEY).exists()):
        print(f"HTTPS disabled: certificate not found ({TLS_CERT})", flush=True)
    elif TLS_CERT and TLS_KEY:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(TLS_CERT, TLS_KEY)
        tls = ThreadingHTTPServer((HOST, TLS_PORT), Handler)
        tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
        threading.Thread(target=tls.serve_forever, daemon=True).start()
        print(f"GitLab Launcher: https://{HOST}:{TLS_PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        stop_event.set()


if __name__ == "__main__":
    main()
