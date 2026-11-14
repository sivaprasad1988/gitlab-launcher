"""TYPO3 release watcher and pipeline result mails."""
import datetime
import json
import threading
import time
import urllib.request

from .core import FINAL_STATUSES, MAIL_WATCH_POLL, MAIL_WATCH_TIMEOUT, STATE, TYPO3_API, config, load, lock, log, notify, now, save, stop_event
from .alerts import notify_event
from .gitlab import find_project, gitlab, run_pipeline


def _mail_watch_done(pipeline_id):
    with lock:
        st = load(STATE, {})
        st["mail_watch"] = [w for w in st.get("mail_watch", []) if w["pipeline"] != pipeline_id]
        save(STATE, st)


def watch_pipeline_mail(w):
    """Wait until an auto-triggered pipeline has finished, then send the result mail."""
    deadline = datetime.datetime.fromisoformat(w["t"]).timestamp() + MAIL_WATCH_TIMEOUT
    pl, errors = None, 0
    while not stop_event.is_set():
        try:
            project = find_project(w["project"])
        except RuntimeError:                       # project removed meanwhile
            return _mail_watch_done(w["pipeline"])
        try:
            pl = gitlab(project, "GET", f"/pipelines/{w['pipeline']}")
            if pl["status"] in FINAL_STATUSES:
                break
        except Exception as e:
            errors += 1
            if errors in (1, 10):
                log(f"Mail watch error for pipeline #{w['pipeline']}: {e}")
        if time.time() > deadline:
            break
        stop_event.wait(MAIL_WATCH_POLL)
    if stop_event.is_set():
        return                                     # resumed on next start (kept in state.json)
    status = pl["status"] if pl else "unknown"
    final = status in FINAL_STATUSES
    facts = [("Project", f"{project['name']} ({project['path']})"),
             ("Branch", project["ref"]),
             ("Trigger", w["trigger"]),
             ("Pipeline", f"#{w['pipeline']}"),
             ("Status", status + ("" if final else " (not finished within 24 h)")),
             ("Started", w["t"]),
             ("Finished", (pl or {}).get("finished_at") or "-")]
    title = f"TYPO3 update {project['name']}: {status.upper() if final else 'NOT FINISHED (' + status + ')'}"
    notify_event(title, "Automatic TYPO3 update pipeline on staging.", facts,
                 (pl or {}).get("web_url") or w.get("web_url"))
    _mail_watch_done(w["pipeline"])


def start_mail_watch(project, pl, trigger):
    w = {"project": project["id"], "pipeline": pl["id"], "web_url": pl.get("web_url"),
         "trigger": trigger, "t": now()}
    with lock:
        st = load(STATE, {})
        st.setdefault("mail_watch", []).append(w)
        save(STATE, st)
    threading.Thread(target=watch_pipeline_mail, args=(w,), daemon=True).start()


def resume_mail_watches():
    for w in load(STATE, {}).get("mail_watch", []):
        threading.Thread(target=watch_pipeline_mail, args=(w,), daemon=True).start()


# ---------------------------------------------------------------- TYPO3 watcher
def latest_typo3(major):
    with urllib.request.urlopen(TYPO3_API.format(major=int(major)), timeout=30) as r:
        return json.load(r)["version"]


def announce_update(due):
    """Before the staging pipelines start: one message per new version (once per project and version)."""
    with lock:
        st = load(STATE, {})
        done = st.setdefault("typo3_announced", {})
        todo = [(p, prev, v) for p, prev, v in due if done.get(p["id"]) != v]
        for p, _, v in todo:
            done[p["id"]] = v
        save(STATE, st)
    for v in sorted({v for _, _, v in todo}):
        items = [(p, prev) for p, prev, vv in todo if vv == v]
        major = v.split(".")[0]
        notify_event(f"New TYPO3 {v} released: starting update on staging",
                     f"TYPO3 {v} has been published. The update pipeline is now started on staging for "
                     f"{len(items)} project(s). Result messages follow when the pipelines have finished.",
                     [(p["name"], f"{p['ref']} · {prev} → {v}") for p, prev in items],
                     f"https://get.typo3.org/version/{major}", "TYPO3 release info")
        log(f"Announced TYPO3 {v} update for {len(items)} project(s)")


def check_typo3():
    """Returns (projects with auto-update, list of errors). Errors make the scheduler run count as failed."""
    projects = [p for p in config()["projects"] if p.get("auto_typo3", {}).get("enabled")]
    latest, errors = {}, []
    for major in sorted({int(p["auto_typo3"]["major"]) for p in projects}):
        try:
            latest[major] = latest_typo3(major)
        except Exception as e:
            log(f"TYPO3 {major} check failed: {e}")
            errors.append(f"TYPO3 {major} check failed: {e}")

    due = []                                   # (project, previous version, new version)
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
            due.append((p, prev, v))

    announce_update(due)
    for p, prev, v in due:
        trigger = f"TYPO3 {prev} -> {v}"
        try:
            pl = run_pipeline(p, p["auto_typo3"].get("variables", {}), trigger,
                              p["auto_typo3"].get("auto_play", []))
            with lock:
                st = load(STATE, {})
                st["typo3_seen"][p["id"]] = v
                save(STATE, st)
            notify("TYPO3 update started", f"{p['name']}: {v} (pipeline #{pl['id']})")
            start_mail_watch(p, pl, trigger)
        except Exception as e:
            # Not marked as seen -> retried on next check.
            log(f"Auto-trigger failed for {p['id']} (TYPO3 {v}): {e}")
            errors.append(f"Auto-trigger failed for {p['name']} (TYPO3 {v}): {e}")
            notify("TYPO3 auto-update FAILED", f"{p['name']}: {e}")
            notify_event(f"TYPO3 update {p['name']}: NOT STARTED",
                         "The automatic TYPO3 update pipeline could not be started. "
                         "It is retried on the next check.",
                         [("Project", f"{p['name']} ({p['path']})"), ("Branch", p["ref"]),
                          ("Trigger", trigger), ("Error", e)])

    with lock:
        st = load(STATE, {})
        st["last_check"] = now()
        # keep the last known version of a major whose check failed (used for the update hint in the UI)
        st["typo3_latest"] = {**st.get("typo3_latest", {}), **{str(k): v for k, v in latest.items()}}
        save(STATE, st)
    return projects, errors
