"""t3monitor instances: keys, fetching, history, change detection."""
import base64
import hashlib
import html
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .core import DATA, SEVERITY, T3M_DIR, T3M_KEEP, T3M_REPORTS, config, load, lock, log, now, save, write_secret
from .alerts import notify_event
from .extensions import t3m_add_updates


# ---------------------------------------------------------------- t3monitor
# Instances per project: config key, form field prefix, file name part. "production" keeps the original names.
T3M_ENVS = {"production": {"cfg": "t3monitor", "form": "t3m_", "file": "", "label": "Production"},
            "staging": {"cfg": "t3monitor_staging", "form": "t3ms_", "file": "-staging", "label": "Staging"}}


def t3m_env(env):
    if env not in T3M_ENVS:
        raise RuntimeError(f"Unknown t3monitor instance: {env}")
    return T3M_ENVS[env]


def t3m_cfg(project, env="production"):
    return project.get(t3m_env(env)["cfg"]) or {}


def t3m_key_file(pid, env="production"):
    if not re.fullmatch(r"[\w.\-]+", pid):
        raise RuntimeError(f"Invalid project id: {pid}")
    return DATA / f"{pid}.t3monitor{t3m_env(env)['file']}-key"


def t3m_auth_file(pid, env="production"):
    t3m_key_file(pid, env)                               # validates pid + env
    return DATA / f"{pid}.t3monitor{T3M_ENVS[env]['file']}-auth"


def t3m_auth_password(pid, env="production"):
    f = t3m_auth_file(pid, env)
    return f.read_text(encoding="utf-8") if f.exists() else ""


def t3m_key(pid, env="production"):
    f = t3m_key_file(pid, env)
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


def t3m_save_key(pid, key, env="production"):
    key = (key or "").strip()
    if len(key) != 64 or any(c.isspace() for c in key):
        raise RuntimeError(f"t3monitor key ({T3M_ENVS[env]['label']}) must be exactly 64 characters")
    write_secret(t3m_key_file(pid, env), key)


def t3m_history_file(pid, env="production"):
    t3m_key_file(pid, env)                               # validates pid + env
    return (T3M_DIR / f"{pid}.json") if env == "production" else (T3M_DIR / env / f"{pid}.json")


def t3m_decrypt(key, raw):
    """Same derivation as BrainAppeal\\T3monitor\\Helper\\Encryption::decryptOpenSsl."""
    if not raw.startswith("01:"):
        raise RuntimeError(f"Unexpected t3monitor answer: {raw[:200]}")
    k = hashlib.sha256(key[32:48].encode()).hexdigest()
    iv = hashlib.sha256(k[16:].encode()).hexdigest()[:16]
    r = subprocess.run(["openssl", "enc", "-d", "-aes-128-cbc", "-K", k[:16].encode().hex(),
                        "-iv", iv.encode().hex()], input=base64.b64decode(raw[3:]),
                       capture_output=True, timeout=30)
    if r.returncode:
        raise RuntimeError("t3monitor answer could not be decrypted (wrong key?)")
    return r.stdout.decode("utf-8", errors="replace")


def _plain(s, limit=400):
    s = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(s or "")))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def t3m_is_local(composer, version):
    """Project's own extensions: own composer vendor or a dev branch (e.g. dev-main)."""
    vendors = config().get("t3monitor_local_vendors", ["gingco"])
    return composer.split("/")[0] in vendors or version.startswith("dev-")


def t3m_parse(xml):
    """Keep only technical data (no backend users, no paths)."""
    root = ET.fromstring(xml)
    exts = {}
    for e in root.find("installed_extensions") if root.find("installed_extensions") is not None else []:
        if e.findtext("ext"):
            version, composer = (e.findtext("version") or "").strip(), (e.findtext("composer/name") or "").strip()
            exts[e.findtext("ext")] = {"version": version, "title": _plain(e.findtext("title"), 120),
                                       "composer": composer, "local": t3m_is_local(composer, version)}
    status, version = [], None
    for g in root.find("reports") if root.find("reports") is not None else []:
        for i in g:
            iid = f"{g.tag}/{i.get('index', i.tag) if i.tag == 'numIndex' else i.tag}"
            try:
                sev = int(i.findtext("severity") or 0)
            except ValueError:
                sev = 0
            value = _plain(i.findtext("value"), 200)
            if g.tag == "typo3" and i.tag == "Typo3Version":
                version = value
            status.append({"id": iid, "group": g.tag, "title": _plain(i.findtext("title"), 150) or i.tag,
                           "value": value, "severity": sev, "message": _plain(i.findtext("message"))})
    return {"site_name": _plain(root.findtext("site_name"), 200), "typo3": version,
            "extensions": dict(sorted(exts.items())), "status": status}


def _t3m_public(d):
    """Extensions without the project's own ones (rule applied now, also to old snapshots)."""
    return {k: v for k, v in d.get("extensions", {}).items()
            if not t3m_is_local(v.get("composer", ""), v.get("version", ""))}


def t3m_changes(old, new):
    if old is None:
        return ["First check"]
    ch = []
    if old.get("typo3") != new.get("typo3"):
        ch.append(f"TYPO3 {old.get('typo3') or '?'} → {new.get('typo3') or '?'}")
    oe, ne = _t3m_public(old), _t3m_public(new)
    for k in sorted(set(oe) | set(ne)):
        if k not in oe:
            ch.append(f"Extension added: {k} {ne[k]['version']}")
        elif k not in ne:
            ch.append(f"Extension removed: {k} {oe[k]['version']}")
        elif oe[k]["version"] != ne[k]["version"]:
            ch.append(f"Extension {k}: {oe[k]['version']} → {ne[k]['version']}")
    os_ = {x["id"]: x for x in old.get("status", [])}
    for x in new.get("status", []):
        o = os_.get(x["id"])
        if o and o["severity"] != x["severity"]:
            ch.append(f"Status '{x['title']}': {SEVERITY.get(o['severity'], o['severity'])} → "
                      f"{SEVERITY.get(x['severity'], x['severity'])}")
        elif not o and x["severity"] >= 1:
            ch.append(f"New {SEVERITY[x['severity']]}: {x['title']}")
    return ch


def _t3m_signature(d):
    """Fields that define a change (status values like times are ignored)."""
    return json.dumps([d.get("typo3"), {k: v["version"] for k, v in _t3m_public(d).items()},
                       sorted((x["id"], x["severity"]) for x in d.get("status", []))])


def t3m_fetch(project, env="production", alert=True):
    """Reads one site. alert=True (manual refresh): immediate message on failure / new errors / new extension
    updates. alert=False (scheduled run): no message here, the run sends one summary (task_t3monitor)."""
    pid, url = project["id"], (t3m_cfg(project, env).get("url") or "").strip()
    key = t3m_key(pid, env)
    hf = t3m_history_file(pid, env)
    tag = pid if env == "production" else f"{pid} ({env})"
    site = f"{project.get('name') or pid} ({T3M_ENVS[env]['label']})"
    try:
        if not url or not key:
            raise RuntimeError("t3monitor URL or key missing")
        q = urllib.parse.urlencode({"t3monitor": 1, "secret": key[:32], "reports": T3M_REPORTS})
        headers = {"User-Agent": "t3-watchdock"}
        auth_user = t3m_cfg(project, env).get("auth_user") or ""
        if auth_user:
            cred = f"{auth_user}:{t3m_auth_password(pid, env)}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(cred).decode()
        req = urllib.request.Request(url.rstrip("/") + "/?" + q, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode("utf-8", errors="replace").strip()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError("HTTP 401: Basic auth required or wrong (set user/password in the project's "
                                   "t3monitor settings)") from None
            raise RuntimeError(f"HTTP {e.code}: {_plain(e.read().decode(errors='replace'), 200)}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"Site not reachable: {e.reason}") from None
        data = t3m_parse(t3m_decrypt(key, raw))
        t3m_add_updates(data)
    except Exception as e:
        msg = str(e)
        with lock:
            h = load(hf, {"snapshots": []})
            first_fail = h.get("last_error") != msg
            if first_fail:
                log(f"t3monitor {tag}: {msg}")
            h.update(last_check=now(), last_error=msg)
            hf.parent.mkdir(parents=True, exist_ok=True)
            save(hf, h)
        if first_fail and alert:                          # once per new error text, not on every run
            notify_event(f"t3monitor check FAILED: {site}", "The TYPO3 status of this site could not be read.",
                         [("Site", url or "–"), ("Error", msg)], t3m_launcher_link(pid), "Open in launcher")
        raise RuntimeError(msg) from None
    with lock:
        h = load(hf, {"snapshots": []})
        snaps = h["snapshots"]
        prev = snaps[0]["data"] if snaps else None
        if prev is not None and _t3m_signature(prev) == _t3m_signature(data):
            snaps[0].update(last=now(), data=data)        # unchanged: refresh values + last seen
        else:
            changes = t3m_changes(prev, data)
            snaps.insert(0, {"t": now(), "last": now(), "changes": changes, "data": data})
            del snaps[T3M_KEEP:]
            if prev is not None:
                log(f"t3monitor {tag}: {len(changes)} change(s)")
        # extension updates not reported yet (each extension version once; fixed ones are forgotten)
        reported = h.get("reported_updates", {})
        upd = {k: e for k, e in data["extensions"].items() if e.get("update")}
        new_upd = [k for k, e in upd.items() if reported.get(k) != e["latest"]]
        h["reported_updates"] = {k: e["latest"] for k, e in upd.items()}
        h.update(last_check=now(), last_error=None)
        hf.parent.mkdir(parents=True, exist_ok=True)
        save(hf, h)
    new_err = t3m_new_errors(prev, data)
    if new_err:
        log(f"t3monitor {tag}: {len(new_err)} new error(s)")
    if new_upd:
        log(f"t3monitor {tag}: new extension update(s): " + ", ".join(f"{k} {upd[k]['latest']}" for k in new_upd))
    if alert and (new_err or new_upd):
        facts = [(f"Error: {x['title']}", _plain(f"{x['value']} – {x['message']}".strip(" –"), 300)) for x in new_err[:10]]
        facts += [(f"Update: {k}", _upd_text(upd[k])) for k in new_upd]
        parts = ([f"{len(new_err)} new error{'s' if len(new_err) > 1 else ''}"] if new_err else []) \
            + ([f"{len(new_upd)} new extension update{'s' if len(new_upd) > 1 else ''}"] if new_upd else [])
        notify_event(f"t3monitor: {' and '.join(parts)} on {site}", f"TYPO3 status of {url}.", facts,
                     t3m_launcher_link(pid), "Open in launcher")
    return {"site": site, "data": data, "new_err": new_err, "new_upd": new_upd}


def _n(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _upd_text(e):
    return f"{e['version']} → {e['latest']}" + (" (MAJOR: may contain breaking changes)" if e.get("major") else "")


def t3m_new_errors(prev, data):
    """Status checks that are 'error' now but were not before. First check = baseline (no message)."""
    if prev is None:
        return []
    was = {x["id"] for x in prev.get("status", []) if x.get("severity") == 2}
    return [x for x in data.get("status", []) if x.get("severity") == 2 and x["id"] not in was]


def t3m_launcher_link(pid):
    """Link to the project page (localhost only; reachable from the machine running the launcher)."""
    return f"http://127.0.0.1:{int(config().get('port', 8787))}/#/p/{urllib.parse.quote(pid)}"


def t3m_view(pid, env="production"):
    h = load(t3m_history_file(pid, env), {"snapshots": []})
    snaps = h.get("snapshots", [])
    return {"env": env, "key_set": bool(t3m_key(pid, env)), "last_check": h.get("last_check"), "last_error": h.get("last_error"),
            "current": snaps[0]["data"] if snaps else None,
            "since": snaps[0]["t"] if snaps else None,
            "history": [{"t": x["t"], "last": x["last"], "changes": x["changes"],
                         "typo3": x["data"].get("typo3")} for x in snaps]}


def t3m_summary():
    """Compact status of every configured instance, for the overview table on the home page."""
    out = {}
    for p in config()["projects"]:
        for env in T3M_ENVS:
            if not (t3m_cfg(p, env).get("url") and t3m_key(p["id"], env)):
                continue
            h = load(t3m_history_file(p["id"], env), {"snapshots": []})
            c = h["snapshots"][0]["data"] if h.get("snapshots") else {}
            sev = [x.get("severity") for x in c.get("status", [])]
            out.setdefault(p["id"], {})[env] = {
                "typo3": c.get("typo3"), "errors": sev.count(2), "warnings": sev.count(1), "has_data": bool(c),
                "last_check": h.get("last_check"), "last_error": h.get("last_error")}
    return out


def _t3m_user(v):
    v = (v or "").strip()
    if ":" in v or any(c.isspace() for c in v):
        raise RuntimeError("Basic auth user must not contain ':' or spaces")
    return v


def _t3m_url(v):
    v = (v or "").strip()
    if v and not re.match(r"https?://[^\s/]+", v):
        raise RuntimeError("t3monitor URL must start with https://")
    return v


def save_t3m_key(pid, b):
    for env, e in T3M_ENVS.items():                          # validate all keys before writing any file
        k = (b.get(e["form"] + "key") or "").strip()
        if k and not b.get(e["form"] + "key_clear") and (len(k) != 64 or any(c.isspace() for c in k)):
            raise RuntimeError(f"t3monitor key ({e['label']}) must be exactly 64 characters")
    for env, e in T3M_ENVS.items():
        f = lambda k: b.get(e["form"] + k)
        if f("key_clear"):
            t3m_key_file(pid, env).unlink(missing_ok=True)
        elif (f("key") or "").strip():
            t3m_save_key(pid, f("key"), env)
        if f("auth_clear") or not (f("auth_user") or "").strip():
            t3m_auth_file(pid, env).unlink(missing_ok=True)   # no user = no basic auth
        elif f("auth_password"):                              # empty field = keep stored password
            write_secret(t3m_auth_file(pid, env), f("auth_password"))
