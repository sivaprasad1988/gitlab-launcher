"""E-mail and Microsoft Teams notifications."""
import json
import re
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

from .core import CONFIG, SMTP_PASSWORD_FILE, TEAMS_FILE, config, lock, log, save, write_secret


# ---------------------------------------------------------------- email
def email_cfg():
    e = config().get("email") or {}
    return {"enabled": bool(e.get("enabled")), "host": e.get("host", ""), "port": int(e.get("port") or 587),
            "security": e.get("security", "starttls"), "user": e.get("user", ""),
            "from": e.get("from", ""), "to": e.get("to", [])}


def smtp_password():
    if SMTP_PASSWORD_FILE.exists():
        return SMTP_PASSWORD_FILE.read_text(encoding="utf-8").strip()
    return ""


def save_email(b):
    to = [a.strip() for a in re.split(r"[,;\s]+", b.get("to") or "") if a.strip()]
    sec = b.get("security", "starttls")
    if sec not in ("starttls", "ssl", "none"):
        raise RuntimeError("Security must be starttls, ssl or none")
    e = {"enabled": bool(b.get("enabled")), "host": (b.get("host") or "").strip(),
         "port": int(b.get("port") or (465 if sec == "ssl" else 587)), "security": sec,
         "user": (b.get("user") or "").strip(), "from": (b.get("from") or "").strip(), "to": to}
    if e["enabled"] and not (e["host"] and e["from"] and to):
        raise RuntimeError("SMTP host, sender and at least one recipient are required")
    with lock:
        cfg = config()
        cfg["email"] = e
        save(CONFIG, cfg)
    pw = b.get("password")
    if b.get("clear_password"):
        SMTP_PASSWORD_FILE.unlink(missing_ok=True)
    elif pw:                                   # empty field = keep stored password
        write_secret(SMTP_PASSWORD_FILE, pw)
    log("Email settings saved")
    return e


def send_mail(subject, text, cfg=None):
    e = cfg or email_cfg()
    if not (e["host"] and e["from"] and e["to"]):
        raise RuntimeError("Email not configured (SMTP host, sender, recipients)")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, e["from"], ", ".join(e["to"])
    msg.set_content(text)
    ctx = ssl.create_default_context()
    if e["security"] == "ssl":
        s = smtplib.SMTP_SSL(e["host"], e["port"], timeout=30, context=ctx)
    else:
        s = smtplib.SMTP(e["host"], e["port"], timeout=30)
    with s:
        if e["security"] == "starttls":
            s.starttls(context=ctx)
        if e["user"]:
            s.login(e["user"], smtp_password())
        s.send_message(msg)


# ---------------------------------------------------------------- Microsoft Teams
def teams_url():
    return TEAMS_FILE.read_text(encoding="utf-8").strip() if TEAMS_FILE.exists() else ""


def teams_cfg():
    return {"enabled": bool((config().get("teams") or {}).get("enabled"))}


def save_teams(b):
    url = (b.get("url") or "").strip()
    if b.get("clear_url"):
        TEAMS_FILE.unlink(missing_ok=True)
    elif url:                                  # empty field = keep stored URL
        if not url.startswith("https://") or any(c.isspace() for c in url):
            raise RuntimeError("Webhook URL must start with https:// and contain no spaces")
        write_secret(TEAMS_FILE, url)
    enabled = bool(b.get("enabled"))
    if enabled and not teams_url():
        raise RuntimeError("Webhook URL is required")
    with lock:
        cfg = config()
        cfg["teams"] = {"enabled": enabled}
        save(CONFIG, cfg)
    log("Teams settings saved")
    return teams_cfg()


def send_teams(title, intro, facts, link=None, link_title="Open pipeline"):
    """Post an Adaptive Card (format accepted by Teams Workflows and legacy incoming webhooks)."""
    url = teams_url()
    if not url:
        raise RuntimeError("No Teams webhook URL stored")
    card = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4", "body": [
                {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
                {"type": "TextBlock", "text": intro, "wrap": True}]}
    if facts:                                  # an empty FactSet can make Teams reject the whole card
        card["body"].append({"type": "FactSet", "facts": [{"title": str(k), "value": str(v)} for k, v in facts]})
    if link:
        card["actions"] = [{"type": "Action.OpenUrl", "title": link_title, "url": link}]
    body = json.dumps({"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Teams HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Teams not reachable: {e.reason}") from None


def notify_event(title, intro, facts, link=None, link_title="Open pipeline"):
    """Send to every enabled channel (Teams, email); errors only go to the log."""
    if teams_cfg()["enabled"]:
        try:
            send_teams(title, intro, facts, link, link_title)
            log(f"Teams message sent: {title}")
        except Exception as e:
            log(f"Teams message failed ({title}): {e}")
    if email_cfg()["enabled"]:
        text = intro + "\n\n" + "\n".join(f"{k + ':':<10}{v}" for k, v in facts) + \
            (f"\n{'Link:':<10}{link}" if link else "") + "\n"
        try:
            send_mail("[T3 Watchdock] " + title, text)
            log(f"Email sent: {title}")
        except Exception as e:
            log(f"Email failed ({title}): {e}")
