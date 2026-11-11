"""Self-update from GitHub Releases (`svkit update`) and the install description behind `svkit --version`.

A release is a git tag `vX.Y.Z` whose `svkit.__version__` is `X.Y.Z`, published as a GitHub Release.
`svkit update` asks the GitHub API for the latest release of the repository svkit was installed from,
and when it is newer than the running version, installs that tag's source archive with the same Python
that runs svkit (so the right venv is updated, and git is not needed).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from svkit import __version__

DEFAULT_REPO = "Mercury1565/sv-kit"   # where releases are published; SVKIT_REPO overrides it (forks)
API = "https://api.github.com"
USER_AGENT = "svkit-updater"          # generic: no user or host details are sent
CHECK_EVERY_S = 24 * 3600


class UpdateError(RuntimeError):
    pass


# ------------------------------------------------------------------ versions
def parse_version(v: str) -> tuple:
    """`v0.8.0`, `0.8.0`, `0.8.0.dev0`, `0.8.0rc1` -> a sortable tuple; a pre-release sorts before its release."""
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$", v.strip())
    if not m:
        raise ValueError(f"not a version: {v!r}")
    rest = m.group(4).lstrip(".-+")
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0), 0 if rest else 1, rest)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate)[:4] > parse_version(current)[:4]


# ------------------------------------------------------------------ how svkit was installed
@dataclass
class InstallInfo:
    kind: str                       # "release" | "commit" | "local" | "unknown"
    ref: Optional[str] = None       # release tag, or the branch / ref asked for
    commit: Optional[str] = None
    url: Optional[str] = None

    def describe(self) -> str:
        if self.kind == "release":
            return f"release {self.ref}"
        if self.kind == "commit":
            parts = [p for p in (self.ref, self.commit[:7] if self.commit else None) if p]
            return "from git " + " @ ".join(parts) if parts else "from git"
        if self.kind == "local":
            return "local checkout"
        return "install source unknown"


def _direct_url() -> Optional[dict]:
    try:
        from importlib.metadata import distribution
        text = distribution("svkit").read_text("direct_url.json")
        return json.loads(text) if text else None
    except Exception:
        return None


TAG_RE = re.compile(r"^v\d+(\.\d+)*([.-]?\w+)?$")


def install_info(direct: Optional[dict] = None, *, read: bool = True) -> InstallInfo:
    """What pip recorded about where svkit came from (PEP 610 direct_url.json)."""
    d = direct if direct is not None or not read else _direct_url()
    if not d:
        return InstallInfo("unknown")
    url = d.get("url")
    if (d.get("dir_info") or {}).get("editable") or (url or "").startswith("file:"):
        return InstallInfo("local", url=url)
    if (vcs := d.get("vcs_info")) is not None:
        ref = vcs.get("requested_revision")
        if ref and TAG_RE.match(ref):
            return InstallInfo("release", ref=ref, commit=vcs.get("commit_id"), url=url)
        return InstallInfo("commit", ref=ref, commit=vcs.get("commit_id"), url=url)
    if (m := re.search(r"/archive/refs/tags/([^/]+?)\.(?:tar\.gz|zip)$", url or "")):
        return InstallInfo("release", ref=m.group(1), url=url)
    return InstallInfo("unknown", url=url)


def version_string(info: Optional[InstallInfo] = None) -> str:
    info = info or install_info()
    return f"svkit {__version__} ({info.describe()})"


def repo_slug(info: Optional[InstallInfo] = None) -> str:
    """owner/name of the GitHub repository to update from: $SVKIT_REPO, else the one svkit was
    installed from, else the default."""
    if env := os.environ.get("SVKIT_REPO"):
        return env.strip().strip("/")
    info = info or install_info()
    if info.url and (m := re.search(r"github\.com[:/]([^/]+)/([^/@#?]+?)(?:\.git)?(?:[/@#?]|$)", info.url)):
        return f"{m.group(1)}/{m.group(2)}"
    return DEFAULT_REPO


# ------------------------------------------------------------------ GitHub
@dataclass
class Release:
    tag: str
    name: str
    url: str
    published_at: str
    notes: str


def _get_json(url: str, timeout: float) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if tok := os.environ.get("GITHUB_TOKEN"):       # only raises the rate limit; never required
        headers["Authorization"] = f"Bearer {tok}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return json.loads(r.read().decode())


def latest_release(repo: str, timeout: float = 10.0, *, fetch: Callable[[str, float], dict] = _get_json
                   ) -> Optional[Release]:
    """The latest published (non-draft, non-pre-release) release, or None when the repo has none."""
    try:
        d = fetch(f"{API}/repos/{repo}/releases/latest", timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 403:
            raise UpdateError("GitHub refused the request (rate limit?). Try again later, "
                              "or set GITHUB_TOKEN.") from e
        raise UpdateError(f"GitHub answered {e.code} for {repo}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"could not reach GitHub: {getattr(e, 'reason', e)}") from e
    return Release(tag=d["tag_name"], name=d.get("name") or d["tag_name"], url=d.get("html_url", ""),
                   published_at=d.get("published_at") or "", notes=d.get("body") or "")


def archive_url(repo: str, tag: str) -> str:
    return f"https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz"


# ------------------------------------------------------------------ installing
def free_entry_script(bindir: Path) -> Optional[Path]:
    """Move the `svkit` launcher aside before pip replaces it.

    On a network home directory (NFS), pip cannot remove a launcher that a running svkit (this one, or
    `svkit serve` on any machine sharing the directory) has open: it fails with Errno 11. Renaming it
    within its directory always works; pip then simply writes a new launcher. Leftovers from earlier
    updates are removed when nothing holds them any more. Returns the moved file (to put back on failure).
    """
    for old in bindir.glob(".svkit-old-*"):
        try:
            old.unlink()
        except OSError:
            pass
    script = bindir / "svkit"
    if not script.is_file():
        return None
    aside = bindir / f".svkit-old-{os.getpid()}-{int(time.time())}"
    try:
        os.replace(script, aside)
    except OSError:
        return None
    return aside


def restore_entry_script(bindir: Path, aside: Optional[Path]) -> None:
    script = bindir / "svkit"
    if aside is not None and aside.exists() and not script.exists():
        try:
            os.replace(aside, script)
        except OSError:
            pass


def _run(cmd: list[str]) -> int:
    return subprocess.call(cmd)


def installed_version(python: str = sys.executable) -> Optional[str]:
    try:
        out = subprocess.run([python, "-c", "import svkit; print(svkit.__version__)"], capture_output=True,
                             text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def install_release(repo: str, tag: str, *, python: str = sys.executable, run: Callable[[list[str]], int] = _run,
                    verify: Callable[[str], Optional[str]] = installed_version, log: Callable[[str], None] = print
                    ) -> str:
    """pip-install the tag's source archive into the running Python's environment; returns the version
    now installed. Dependencies are only upgraded when the new release needs it."""
    bindir = Path(python).parent
    want = tag.lstrip("v")
    req = f"svkit @ {archive_url(repo, tag)}"
    pip = [python, "-m", "pip", "install", "--disable-pip-version-check", "--quiet"]
    aside = free_entry_script(bindir)
    try:
        rc = run([*pip, "--upgrade", "--upgrade-strategy", "only-if-needed", req])
        got = verify(python) if rc == 0 else None
        if rc == 0 and got != want:
            # pip kept the old files (same version number on a re-tagged release): replace svkit itself only
            log(f"[svkit] reinstalling svkit {tag} itself")
            rc = run([*pip, "--force-reinstall", "--no-deps", req])
            got = verify(python) if rc == 0 else None
    finally:
        restore_entry_script(bindir, aside)
    if rc != 0:
        raise UpdateError(f"pip failed (exit {rc}); svkit is unchanged. Run the same with -v for details:\n  "
                          f"{python} -m pip install --upgrade '{req}'")
    if got != want:
        raise UpdateError(f"installed, but svkit reports version {got!r} instead of {want!r}: the release tag "
                          f"and svkit.__version__ disagree")
    return got


def running_servers(bindir: Path, me: int | None = None) -> list[int]:
    """PIDs of other `svkit serve` processes started from this environment, on this machine (Linux /proc)."""
    me = os.getpid() if me is None else me
    target = str(bindir / "svkit")
    out = []
    for d in Path("/proc").glob("[0-9]*"):
        try:
            if int(d.name) == me or d.stat().st_uid != os.getuid():
                continue
            argv = (d / "cmdline").read_bytes().split(b"\0")
        except (OSError, ValueError):
            continue
        args = [a.decode(errors="replace") for a in argv if a]
        # launched through the shebang, the launcher's full path is in the command line: python <bin>/svkit serve
        if "serve" in args and target in args[:2]:
            out.append(int(d.name))
    return out


# ------------------------------------------------------------------ quiet check at `svkit serve`
def _cache_file() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "svkit" / "update-check.json"


def newer_release_notice(*, now: float | None = None, fetch: Callable[[str, float], dict] = _get_json
                         ) -> Optional[str]:
    """A one-line notice when a newer release exists; checks GitHub at most once a day, fails silently.
    Off with SVKIT_NO_UPDATE_CHECK=1."""
    if os.environ.get("SVKIT_NO_UPDATE_CHECK"):
        return None
    now = time.time() if now is None else now
    f = _cache_file()
    tag = None
    try:
        c = json.loads(f.read_text())
        if now - float(c.get("checked_at", 0)) < CHECK_EVERY_S:
            tag = c.get("tag")
        else:
            raise ValueError("stale")
    except (OSError, ValueError, TypeError):
        try:
            rel = latest_release(repo_slug(), timeout=3.0, fetch=fetch)
        except UpdateError:
            return None
        tag = rel.tag if rel else None
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"checked_at": now, "tag": tag}))
        except OSError:
            pass
    try:
        if tag and is_newer(tag, __version__):
            return f"svkit {tag} is available (you have {__version__}): stop the server, run `svkit update`, start it again"
    except ValueError:
        return None
    return None
