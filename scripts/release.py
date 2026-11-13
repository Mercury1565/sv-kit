#!/usr/bin/env python3
"""Cut a svkit release: set the version, commit, and tag `vX.Y.Z` (locally).

    python3 scripts/release.py 0.9.0          # or: patch | minor | major
    git push origin main v0.9.0               # publishing the tag makes GitHub create the release

Push one release tag per push: GitHub starts no workflow when a push carries more than 3 tags (so
never `--follow-tags` / `--tags` while iter-N tags are unpushed). A tag already on GitHub without a
release: Actions -> release -> Run workflow, with the tag.

Then, on any machine: `svkit update`.

Checks first: on the main branch, nothing uncommitted, the tag is new and the version goes up.
The tag message (and so the release notes) lists the commits since the previous release.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "src" / "svkit" / "__init__.py"
VERSION_RE = re.compile(r'^__version__ = "([^"]+)"$', re.M)


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout.strip()


def current() -> str:
    m = VERSION_RE.search(INIT.read_text())
    if not m:
        sys.exit(f"no __version__ line in {INIT}")
    return m.group(1)


def nums(v: str) -> tuple[int, int, int]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        sys.exit(f"not a version: {v}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def next_version(arg: str, cur: str) -> str:
    major, minor, patch = nums(cur)
    if arg == "major":
        return f"{major + 1}.0.0"
    if arg == "minor":
        return f"{major}.{minor + 1}.0"
    if arg == "patch":
        # a pre-release (0.8.0.dev0) becomes its release (0.8.0); a release goes up by one
        prerelease = not re.fullmatch(r"\d+\.\d+\.\d+", cur)
        return f"{major}.{minor}.{patch if prerelease else patch + 1}"
    if not re.fullmatch(r"\d+\.\d+\.\d+", arg.lstrip("v")):
        sys.exit("give a version X.Y.Z, or patch / minor / major")
    return arg.lstrip("v")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    cur = current()
    new = next_version(sys.argv[1], cur)
    tag = f"v{new}"
    if nums(new) < nums(cur) or (nums(new) == nums(cur) and not re.search(r"[a-z]", cur)):
        sys.exit(f"{new} does not go up from {cur}")
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        sys.exit("release from the main branch")
    if git("status", "--porcelain"):
        sys.exit("commit or stash your changes first")
    if git("tag", "--list", tag):
        sys.exit(f"tag {tag} already exists")
    prev = git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", check=False)
    log = git("log", "--no-merges", "--format=- %s", f"{prev}..HEAD" if prev else "HEAD")
    INIT.write_text(VERSION_RE.sub(f'__version__ = "{new}"', INIT.read_text(), count=1))
    git("add", str(INIT))
    git("commit", "-q", "-m", f"Release {tag}")
    notes = f"svkit {tag}\n\nChanges since {prev or 'the start'}:\n{log or '- (no commits)'}\n"
    git("tag", "-a", tag, "-m", notes)
    print(notes)
    print(f"Tagged {tag} (was {cur}). Publish it with:\n\n    git push origin main {tag}\n\n"
          f"GitHub then creates the release, and `svkit update` installs it. Push this one tag on its own:\n"
          f"GitHub ignores pushes of more than 3 tags (so not --follow-tags or --tags).")


if __name__ == "__main__":
    main()
