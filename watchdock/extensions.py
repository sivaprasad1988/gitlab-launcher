"""Extension update check (Packagist) and changelogs (GitHub / GitLab)."""
import json
import posixpath
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .core import PACKAGIST, PKG_TTL


# ---------------------------------------------------------------- extension updates (Packagist)
_pkg_cache = {}                                      # name -> (fetched_at, [version dicts] or None)


def _pkg_versions(name):
    """All versions of a Composer package from Packagist (expanded), None if unknown there."""
    hit = _pkg_cache.get(name)
    if hit and time.time() - hit[0] < PKG_TTL:
        return hit[1]
    req = urllib.request.Request(PACKAGIST + urllib.parse.quote(name) + ".json", headers={"User-Agent": "t3-watchdock"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = json.loads(r.read()).get("packages", {}).get(name, [])
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise RuntimeError(f"Packagist HTTP {e.code}") from None
        raw = None
    versions, cur = None, None
    if raw is not None:                              # p2 metadata is minified: each entry only lists changes
        versions = []
        for v in raw:
            cur = dict(v) if cur is None else {**cur, **v}
            cur = {k: x for k, x in cur.items() if x != "__unset"}
            versions.append(cur)
    _pkg_cache[name] = (time.time(), versions)
    return versions


def _vparse(s):
    """'v13.4.2' -> ((13, 4, 2, 0), 3); None if not numeric."""
    m = re.match(r"v?(\d+(?:\.\d+){0,3})", (s or "").strip())
    if not m:
        return None
    p = [int(x) for x in m.group(1).split(".")]
    return tuple(p + [0] * (4 - len(p))), len(p)


def _bump(t, i):
    return tuple(list(t[:i]) + [t[i] + 1] + [0] * (3 - i))


def _part_ok(v, part):
    """One Composer constraint part, e.g. '^13.4', '>=12.4', '13.*', '~1.2'."""
    if part in ("*", "x"):
        return True
    m = re.fullmatch(r"(\^|~|>=|<=|!=|==|>|<|=)?(.+)", part)
    op, ver = m.group(1) or "", m.group(2)
    wild = re.fullmatch(r"v?(\d+(?:\.\d+)*)\.[*x]", ver)
    if wild:
        lo, n = _vparse(wild.group(1))
        return lo <= v < _bump(lo, n - 1)
    pv = _vparse(ver)
    if not pv or not re.fullmatch(r"v?[\d.]+", ver):
        return False                                 # dev-main etc.
    t, n = pv
    if op == "^":
        i = next((k for k in range(n) if t[k]), n - 1)
        return t <= v < _bump(t, i)
    if op == "~":
        return t <= v < _bump(t, max(n - 2, 0))
    return {">=": v >= t, "<=": v <= t, ">": v > t, "<": v < t, "!=": v != t}.get(op, v == t)


def constraint_ok(version, constraint):
    """True if version (e.g. '13.4.39') satisfies a Composer constraint (e.g. '^12 || ^13 || ^14.3')."""
    pv = _vparse(version)
    if not pv:
        return False
    v = pv[0]
    c = re.sub(r"@\w+", "", constraint or "")
    c = re.sub(r"(>=|<=|!=|==|>|<|=|\^|~)\s+", r"\1", c)
    for alt in re.split(r"\s*\|\|?\s*", c.strip()):
        hy = re.fullmatch(r"(\S+)\s+-\s+(\S+)", alt)
        if hy:
            lo, hi = _vparse(hy.group(1)), _vparse(hy.group(2))
            if lo and hi and v >= lo[0] and (v < _bump(hi[0], hi[1] - 1) if hi[1] < 3 else v <= hi[0]):
                return True
            continue
        parts = [x for x in re.split(r"\s*,\s*|\s+", alt) if x]
        if parts and all(_part_ok(v, x) for x in parts):
            return True
    return False


def _typo3_req(pkg):
    req = pkg.get("require") or {}
    if "typo3/cms-core" in req:
        return req["typo3/cms-core"]
    return next((c for k, c in req.items() if k.startswith("typo3/cms-")), None)   # None = no TYPO3 constraint


def ext_latest(composer, typo3):
    """Newest stable version compatible with the site's TYPO3 version; None if the package is unknown."""
    versions = _pkg_versions(composer.lower())
    if versions is None:
        return None
    best = None
    for p in versions:
        ver = p.get("version", "")
        if not re.fullmatch(r"v?\d+(\.\d+){0,3}", ver):
            continue                                 # dev, alpha, beta, RC
        req = _typo3_req(p)
        if typo3 and req is not None and not constraint_ok(typo3, req):
            continue
        pv = _vparse(ver)[0]
        if best is None or pv > best[0]:
            best = (pv, ver)
    return best[1] if best else ""


def t3m_add_updates(data):
    """Adds latest/update/major to every public extension (errors never fail the t3monitor check)."""
    down = None                                      # Packagist unreachable -> skip the rest of this run
    for e in data["extensions"].values():
        if e.get("local") or not e.get("composer"):
            continue
        if down:
            e["latest_error"] = down
            continue
        try:
            latest = ext_latest(e["composer"], data.get("typo3"))
        except Exception as ex:
            e["latest_error"] = str(ex)[:200]
            if isinstance(ex, (urllib.error.URLError, TimeoutError, OSError)):
                down = f"Packagist not reachable: {getattr(ex, 'reason', ex)}"[:200]
                e["latest_error"] = down
            continue
        e["latest"] = latest                         # None = not on Packagist, "" = no compatible stable version
        cur, new = _vparse(e["version"]), _vparse(latest)
        if cur and new and new[0] > cur[0][:4]:
            a, b = cur[0], new[0]
            e["update"] = True
            e["major"] = b[0] > a[0] or (a[0] == 0 and b[1] > a[1])


# ---------------------------------------------------------------- extension changelog (GitHub / GitLab)
_cl_cache = {}                                       # (composer, version) -> result (tags do not change)
CL_FILES = ("CHANGELOG.md", "Changelog.md", "changelog.md", "CHANGES.md", "Documentation/Changelog.md",
            "ChangeLog", "CHANGELOG", "CHANGES")


def _http_text(url, accept=None):
    """GET text; None on 404."""
    h = {"User-Agent": "t3-watchdock"}
    if accept:
        h["Accept"] = accept
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
            return r.read(400_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"{urllib.parse.urlsplit(url).netloc}: HTTP {e.code}") from None


def _md_section(md, version):
    """Section of a changelog for one version: from its heading to the next heading of the same or higher level."""
    v = re.escape(version.lstrip("v"))
    lines = md.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"(#{1,6})\s+.*?(?<![\d.])v?" + v + r"(?![\d.])", ln)
        if not m:
            continue
        lvl, out = len(m.group(1)), [ln]
        for nxt in lines[i + 1:]:
            h = re.match(r"(#{1,6})\s", nxt)
            if h and len(h.group(1)) <= lvl:
                break
            out.append(nxt)
        return "\n".join(out).strip()
    # plain text, e.g. "Version 7.5.0, 13 November 2026" ... until the next version line
    head = re.compile(r"\s*(?:version|release)\s+v?\d|v?\d+\.\d+(?:\.\d+)?(?![\d.])", re.I)
    for i, ln in enumerate(lines):
        if re.match(r"\s*(?:(?:version|release)\s+)?v?" + v + r"(?![\d.])", ln, re.I):
            out = [ln]
            for nxt in lines[i + 1:]:
                if head.match(nxt):
                    break
                out.append(nxt)
            return "\n".join(out).strip()
    return None


def ext_changelog(composer, version, installed=""):
    """Changelog text for one release of a Composer package, from release notes or a changelog file at its tag."""
    composer = composer.lower()
    if not re.fullmatch(r"[a-z0-9_.\-]+/[a-z0-9_.\-]+", composer):
        raise RuntimeError("Invalid package name")
    key = (composer, version, installed)
    if key in _cl_cache:
        return _cl_cache[key]
    versions = _pkg_versions(composer)
    if versions is None:
        raise RuntimeError("Package not found on Packagist")
    want = _vparse(version)
    pkg = next((p for p in versions if _vparse(p.get("version", "")) and want
                and _vparse(p["version"])[0] == want[0] and re.fullmatch(r"v?[\d.]+", p["version"])), None)
    if not pkg:
        raise RuntimeError(f"Version {version} not found on Packagist")
    tag = pkg["version"]
    src = (pkg.get("source") or {}).get("url") or (pkg.get("support") or {}).get("source") or ""
    src = re.sub(r"\.git$", "", src.strip().rstrip("/"))
    m = re.fullmatch(r"https://(github\.com|gitlab\.com)/([\w.\-]+(?:/[\w.\-]+)+)", src)
    res = {"version": tag, "text": None, "found_in": None, "url": None, "repo": None, "compare": None}
    if not m:
        res["repo"] = src or pkg.get("homepage")
        _cl_cache[key] = res
        return res
    host, path = m.groups()
    repo = f"https://{host}/{path}"
    res["repo"] = repo
    old = next((p["version"] for p in versions if installed and _vparse(p.get("version", ""))
                and _vparse(installed) and _vparse(p["version"])[0] == _vparse(installed)[0]), None)
    if old and old != tag:
        res["compare"] = f"{repo}/compare/{old}...{tag}" if host == "github.com" else f"{repo}/-/compare/{old}...{tag}"
    tags = [tag] + [t for t in (tag.lstrip("v"), "v" + tag.lstrip("v")) if t != tag]
    # 1) release notes
    for t in tags:
        if host == "github.com":
            raw = _http_text(f"https://api.github.com/repos/{path}/releases/tags/{urllib.parse.quote(t)}",
                             "application/vnd.github+json")
            body = json.loads(raw).get("body") if raw else None
            url = f"{repo}/releases/tag/{t}"
        else:
            raw = _http_text(f"https://gitlab.com/api/v4/projects/{urllib.parse.quote(path, safe='')}/releases/{urllib.parse.quote(t)}")
            body = json.loads(raw).get("description") if raw else None
            url = f"{repo}/-/releases/{t}"
        if body and body.strip():
            res.update(text=body.strip(), found_in="Release notes", url=url)
            break
    # 2) changelog file at the tag (section for this version, or a per-version file it links to)
    if not res["text"]:
        rawbase = (f"https://raw.githubusercontent.com/{path}/{tag}/" if host == "github.com"
                   else f"{repo}/-/raw/{tag}/")
        blob = f"{repo}/blob/{tag}/" if host == "github.com" else f"{repo}/-/blob/{tag}/"
        for f in CL_FILES:
            md = _http_text(rawbase + f)
            if not md:
                continue
            sec = _md_section(md, tag)
            link = None
            for ln in md.splitlines():               # index line like "* [8.0.0 (date)](Documentation/Changelog/8.0.0.md)"
                lm = re.search(r"\[[^\]]*?(?<![\d.])v?" + re.escape(tag.lstrip("v")) + r"(?![\d.])[^\]]*\]\(([^)\s]+\.md)\)", ln)
                if lm and not lm.group(1).startswith(("http:", "https:", "/", "..")):
                    link = posixpath.normpath(posixpath.join(posixpath.dirname(f), lm.group(1)))
                    break
            if link and (not sec or len(sec.splitlines()) <= 2):
                sub = _http_text(rawbase + link)
                if sub:
                    res.update(text=sub.strip(), found_in=link, url=blob + link)
                    break
            if sec:
                res.update(text=sec, found_in=f, url=blob + f)
                break
    if res["text"]:
        res["text"] = res["text"][:30000]
        res["base"] = res["url"].rsplit("/", 1)[0] + "/"   # for relative links in the text
    _cl_cache[key] = res
    return res
