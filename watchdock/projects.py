"""Create / update project entries in config.json."""
from .core import CONFIG, STATE, config, load, lock, log, save
from .t3monitor import T3M_ENVS, _t3m_url, _t3m_user, save_t3m_key


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
            "auto_play": _jobs(b.get("prod_play"))},
        **{e["cfg"]: {"url": _t3m_url(b.get(e["form"] + "url")), "auth_user": _t3m_user(b.get(e["form"] + "auth_user"))}
           for e in T3M_ENVS.values()}}


def update_project(b):
    pid = b["id"]                       # id stays stable (state/runs reference it)
    with lock:
        cfg = config()
        for i, old in enumerate(cfg["projects"]):
            if old["id"] == pid:
                new = build_project(b, pid)
                save_t3m_key(pid, b)
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
        save_t3m_key(pid, b)
        cfg["projects"].append(project)
        save(CONFIG, cfg)
    log(f"Project added: {pid}")
    return project
