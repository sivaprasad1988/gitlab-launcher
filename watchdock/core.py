"""Paths, settings, shared state and JSON storage."""
import datetime
import json
import os
import subprocess
import threading
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent  # repo root (app.py)
DATA = Path(os.environ.get("LAUNCHER_DATA", BASE))  # config/state/token location (Docker: /data)
CONFIG = DATA / "config.json"
STATE = DATA / "state.json"
TOKEN_FILE = DATA / ".gitlab-token"              # stored by the app (chmod 600)
TOKEN_FALLBACK = Path.home() / ".gitlab-token"     # optional fallback
SMTP_PASSWORD_FILE = DATA / ".smtp-password"        # stored by the app (chmod 600)
TEAMS_FILE = DATA / ".teams-webhook"                # Teams webhook URL, a secret (chmod 600)
T3M_DIR = DATA / "t3monitor"                        # t3monitor history per project
T3M_REPORTS = "internal,security,installed_extensions"
T3M_KEEP = 300                                      # snapshots (= changes) kept per project
PACKAGIST = "https://repo.packagist.org/p2/"         # extension update check (public package metadata only)
PKG_TTL = 3600                                       # seconds a package's version list is cached
SEVERITY = {-2: "notice", -1: "info", 0: "ok", 1: "warning", 2: "error"}
HOST = os.environ.get("LAUNCHER_HOST", "127.0.0.1")  # Docker: 0.0.0.0, published on 127.0.0.1 only
# extra Host header values, e.g. "local-gitlabrunner.local" (Docker publishes it on port 80)
ALLOWED_HOSTS = {h.strip() for h in os.environ.get("LAUNCHER_ALLOWED_HOSTS", "").split(",") if h.strip()}
# optional HTTPS listener (e.g. mkcert certificate); runs in addition to plain HTTP
TLS_CERT = os.environ.get("LAUNCHER_TLS_CERT")
TLS_KEY = os.environ.get("LAUNCHER_TLS_KEY")
TLS_PORT = int(os.environ.get("LAUNCHER_TLS_PORT", 8443))


TYPO3_API = "https://get.typo3.org/api/v1/major/{major}/release/latest"
FINAL_STATUSES = ("success", "failed", "canceled", "skipped")
MAIL_WATCH_TIMEOUT = 24 * 3600   # stop waiting for a pipeline to finish after 24 h
MAIL_WATCH_POLL = 60

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


def write_secret(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    os.chmod(path, 0o600)
