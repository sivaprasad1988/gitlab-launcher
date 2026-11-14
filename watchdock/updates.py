"""Ready-to-run update commands for extension updates (sent via Teams / e-mail, run locally by a developer)."""
import re
import shlex

from .alerts import notify_event
from .core import config, log
from .t3monitor import T3M_ENVS, t3m_cfg, t3m_key, t3m_view

# composer package name / plain version; values come from the remote site, so they must match before
# they go into a shell command
PKG_RE = re.compile(r"[a-z0-9]([_.-]?[a-z0-9]+)*/[a-z0-9](([_.]|-{1,2})?[a-z0-9]+)*")
VER_RE = re.compile(r"v?\d+(\.\d+){0,3}")


def update_command(project, exts):
    """One copy-paste command: update minor/patch packages on develop, commit, push, merge into staging, push.
    exts: {ext_key: extension dict with composer/version/latest}. Returns (command, skipped_names)."""
    dev = project.get("dev_ref") or "develop"
    stg = project["ref"]
    pkgs, msg, skipped = [], [], []
    for k, e in sorted(exts.items()):
        name, old, new = e.get("composer", "").lower(), e.get("version", ""), e.get("latest", "")  # names are case-insensitive
        if not (PKG_RE.fullmatch(name) and VER_RE.fullmatch(old) and VER_RE.fullmatch(new)):
            skipped.append(k)
            continue
        pkgs.append(name)
        msg.append(f"{name} {old} -> {new}")
    if not pkgs:
        return None, skipped
    q = shlex.quote
    commit = "Update extensions: " + ", ".join(msg)
    cmd = " && ".join([
        f"git switch {q(dev)}", "git pull",
        "composer update " + " ".join(pkgs) + " --with-dependencies",
        "git add composer.lock", f"git commit -m {q(commit)}", "git push",
        f"git switch {q(stg)}", "git pull", f"git merge --no-edit {q(dev)}", "git push"])
    return cmd, skipped


def send_update_commands(results):
    """results: [(project, t3monitor result)] of one scheduler run. Sends one message per project with a NEW
    update; staging data is preferred (closest to develop), production otherwise."""
    by_project = {}
    for p, env, r in results:
        if env == "staging" or p["id"] not in by_project:
            by_project[p["id"]] = (p, r)
    port, sent = int(config().get("port", 8787)), []
    for p, r in by_project.values():
        if not r["new_upd"]:
            continue
        upd = {k: e for k, e in r["data"]["extensions"].items() if e.get("update")}
        minor = {k: e for k, e in upd.items() if not e.get("major")}
        major = {k: e for k, e in upd.items() if e.get("major")}
        cmd, skipped = update_command(p, minor)
        minor = {k: e for k, e in minor.items() if k not in skipped}
        if not cmd and not major and not skipped:
            continue
        facts = [(f"Update: {e['composer'] or k}", f"{e['version']} → {e['latest']}") for k, e in sorted(minor.items())]
        facts += [(f"MAJOR (manual): {e['composer'] or k}", f"{e['version']} → {e['latest']}")
                  for k, e in sorted(major.items())]
        if skipped:
            facts.append(("Skipped (unexpected name/version, update manually)", ", ".join(skipped)))
        intro = (f"Run in your local clone of {p['path']}. Updates {p.get('dev_ref') or 'develop'}, "
                 f"then merges into {p['ref']} (the push starts the {p['ref']} pipeline).") if cmd else \
            "No update can be automated: check the updates below and update manually."
        notify_event(f"Extension updates: {p['name']}", intro, facts,
                     f"http://127.0.0.1:{port}/", "Open launcher", code=cmd)
        log(f"Update commands sent: {p['name']} ({len(minor)} minor/patch, {len(major)} major)")
        sent.append(p["name"])
    return sent


def send_now(project):
    """Button "Send update commands": all pending updates from the last stored check, new or not."""
    results = []
    for env in T3M_ENVS:
        if not (t3m_cfg(project, env).get("url") and t3m_key(project["id"], env)):
            continue
        data = t3m_view(project["id"], env)["current"]
        if data:
            results.append((project, env, {"data": data, "new_upd": [k for k, e in data["extensions"].items()
                                                                      if e.get("update")]}))
    if not results:
        return "No t3monitor data yet: run a check first"
    return "Update commands sent" if send_update_commands(results) else "No pending extension updates"
