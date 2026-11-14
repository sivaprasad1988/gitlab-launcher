"""HTTP server and request handler."""
import json
import ssl
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import ALLOWED_HOSTS, BASE, CONFIG, HOST, STATE, TLS_CERT, TLS_KEY, TLS_PORT, TOKEN_FILE, config, load, lock, log, now, save, stop_event
from .alerts import email_cfg, save_email, save_teams, send_mail, send_teams, smtp_password, teams_cfg, teams_url
from .extensions import ext_changelog
from .gitlab import find_project, gitlab, project_info, promote, run_pipeline, save_token, search_projects, test_token, token_source
from .t3monitor import T3M_ENVS, t3m_auth_file, t3m_auth_password, t3m_env, t3m_fetch, t3m_key, t3m_key_file, t3m_summary, t3m_view
from .watcher import resume_mail_watches
from .projects import add_project, update_project
from .scheduler import TASKS, save_sched, sched_run, sched_view, scheduler


tls_on = False  # set in main() once the HTTPS listener runs
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),        # fixed allowlist, no path traversal
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}


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

    def _to_https(self):
        """Plain HTTP on an extra hostname -> redirect to HTTPS (127.0.0.1/localhost stay plain)."""
        host = self.headers.get("Host", "")
        if not tls_on or isinstance(self.connection, ssl.SSLSocket) or host not in ALLOWED_HOSTS:
            return False
        self.send_response(301 if self.command == "GET" else 308)
        self.send_header("Location", f"https://{host.split(':')[0]}{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()
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
        if self._to_https():
            return
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in STATIC:
                name, ctype = STATIC[u.path]
                return self._send(200, (BASE / "static" / name).read_bytes(), ctype)
            if u.path == "/api/state":
                cfg, st = config(), load(STATE, {})
                return self._send(200, {
                    "projects": cfg["projects"],
                    "scheduler": sched_view(),
                    "token_source": token_source()[0],
                    "email": email_cfg(),
                    "email_password_set": bool(smtp_password()),
                    "teams": teams_cfg(),
                    "teams_url_set": bool(teams_url()),
                    "t3monitor_keys": [p["id"] for p in cfg["projects"] if t3m_key(p["id"])],
                    "t3monitor_auth": [p["id"] for p in cfg["projects"] if t3m_auth_password(p["id"])],
                    "t3monitor_staging_keys": [p["id"] for p in cfg["projects"] if t3m_key(p["id"], "staging")],
                    "t3monitor_staging_auth": [p["id"] for p in cfg["projects"] if t3m_auth_password(p["id"], "staging")],
                    "last_check": st.get("last_check"),
                    "typo3_latest": st.get("typo3_latest", {}),
                    "typo3_seen": st.get("typo3_seen", {}),
                    "runs": st.get("runs", [])[:30],
                    "log": st.get("log", [])[:50]})
            if u.path == "/api/project-info":
                return self._send(200, project_info(q["gitlab_url"][0], q["path"][0], q.get("ref", [""])[0]))
            if u.path == "/api/ext/changelog":
                return self._send(200, ext_changelog(q["composer"][0], q["version"][0], q.get("installed", [""])[0]))
            if u.path == "/api/t3monitor/summary":
                return self._send(200, t3m_summary())
            if u.path == "/api/t3monitor":
                return self._send(200, t3m_view(find_project(q["project"][0])["id"], q.get("env", ["production"])[0]))
            if u.path == "/api/search":
                return self._send(200, search_projects(q["gitlab_url"][0], q["q"][0]))
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
        if self._to_https():
            return
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
            if path == "/api/t3monitor/refresh":
                p, env = find_project(body["project"]), body.get("env") or "production"
                t3m_env(env)
                try:
                    t3m_fetch(p, env)
                except RuntimeError:
                    pass                                   # shown via last_error
                return self._send(200, t3m_view(p["id"], env))
            if path == "/api/teams":
                return self._send(200, save_teams(body))
            if path == "/api/teams/test":
                code = send_teams("T3 Watchdock: test message", "Teams notifications of T3 Watchdock work.",
                                  [("Sent", now().replace("T", " "))])
                msg = (f"Teams test message accepted by the webhook (HTTP {code}). If it does not appear in the "
                       "channel, check the run history of the Workflow in Teams (Workflows → the flow → Run history).")
                log(msg)
                return self._send(200, {"msg": msg})
            if path == "/api/email":
                return self._send(200, save_email(body))
            if path == "/api/email/test":
                e = email_cfg()
                send_mail("[T3 Watchdock] Test email", "Email notifications of T3 Watchdock work.\n", e)
                log(f"Test email sent to {', '.join(e['to'])}")
                return self._send(200, {"msg": f"Test email sent to {', '.join(e['to'])}"})
            if path == "/api/check-now":
                ok, result = sched_run("typo3_release")
                return self._send(200 if ok else 500, {"ok": True} if ok else {"error": result})
            if path == "/api/scheduler":
                return self._send(200, save_sched(body))
            if path == "/api/scheduler/run":
                if body.get("id") not in TASKS:
                    raise RuntimeError("Unknown task")
                ok, result = sched_run(body["id"])
                return self._send(200, {"ok": ok, "result": result, "scheduler": sched_view()})
            if path == "/api/projects":
                return self._send(200, add_project(body))
            if path == "/api/projects/update":
                return self._send(200, update_project(body))
            if path == "/api/projects/delete":
                with lock:
                    cfg = config()
                    cfg["projects"] = [p for p in cfg["projects"] if p["id"] != body["id"]]
                    save(CONFIG, cfg)
                for env in T3M_ENVS:
                    t3m_key_file(body["id"], env).unlink(missing_ok=True)
                    t3m_auth_file(body["id"], env).unlink(missing_ok=True)
                log(f"Project removed: {body['id']}")
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def main():
    port = int(config().get("port", 8787))
    srv = ThreadingHTTPServer((HOST, port), Handler)
    resume_mail_watches()
    threading.Thread(target=scheduler, daemon=True).start()
    print(f"T3 Watchdock: http://{HOST}:{port}", flush=True)
    if TLS_CERT and TLS_KEY and not (Path(TLS_CERT).exists() and Path(TLS_KEY).exists()):
        print(f"HTTPS disabled: certificate not found ({TLS_CERT})", flush=True)
    elif TLS_CERT and TLS_KEY:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(TLS_CERT, TLS_KEY)
        tls = ThreadingHTTPServer((HOST, TLS_PORT), Handler)
        tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
        threading.Thread(target=tls.serve_forever, daemon=True).start()
        global tls_on
        tls_on = True
        print(f"T3 Watchdock: https://{HOST}:{TLS_PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        stop_event.set()
