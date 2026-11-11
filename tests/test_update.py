"""svkit update / svkit --version (no network: GitHub and pip are faked)."""
import io
import json
import os
import urllib.error

import pytest
from typer.testing import CliRunner

import svkit
from svkit import update as U


def test_versions_order():
    assert U.is_newer("v0.9.0", "0.8.0") and U.is_newer("v1.0.0", "0.99.9")
    assert U.is_newer("v0.8.0", "0.8.0.dev0")          # a release beats its own dev version
    assert U.is_newer("v0.8.0", "0.1.0.dev0")
    assert not U.is_newer("v0.8.0", "0.8.0") and not U.is_newer("v0.7.9", "0.8.0")
    assert U.parse_version("v0.10.0") > U.parse_version("0.9.9")
    with pytest.raises(ValueError):
        U.parse_version("main")


@pytest.mark.parametrize("direct,kind,text", [
    ({"url": "https://github.com/o/r/archive/refs/tags/v0.8.0.tar.gz", "archive_info": {}}, "release", "release v0.8.0"),
    ({"url": "https://github.com/o/r.git", "vcs_info": {"vcs": "git", "requested_revision": "v0.8.1", "commit_id": "abcdef1234"}},
     "release", "release v0.8.1"),
    ({"url": "https://github.com/o/r.git", "vcs_info": {"vcs": "git", "requested_revision": "main", "commit_id": "c1385196aa"}},
     "commit", "from git main @ c138519"),
    ({"url": "https://github.com/o/r.git", "vcs_info": {"vcs": "git", "commit_id": "c1385196aa"}}, "commit", "from git c138519"),
    ({"url": "file:///home/x/sv-kit", "dir_info": {"editable": True}}, "local", "local checkout"),
    (None, "unknown", "install source unknown"),
])
def test_install_info(direct, kind, text):
    info = U.install_info(direct, read=False)
    assert info.kind == kind and info.describe() == text
    assert U.version_string(info) == f"svkit {svkit.__version__} ({text})"


def test_repo_slug(monkeypatch):
    monkeypatch.delenv("SVKIT_REPO", raising=False)
    fork = U.install_info({"url": "https://github.com/someone/sv-kit.git", "vcs_info": {"commit_id": "1"}}, read=False)
    assert U.repo_slug(fork) == "someone/sv-kit"
    arch = U.install_info({"url": "https://github.com/a/b/archive/refs/tags/v1.0.0.tar.gz", "archive_info": {}}, read=False)
    assert U.repo_slug(arch) == "a/b"
    assert U.repo_slug(U.InstallInfo("unknown")) == U.DEFAULT_REPO
    monkeypatch.setenv("SVKIT_REPO", "x/y/")
    assert U.repo_slug(fork) == "x/y"


def _http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))


def test_latest_release():
    rel = U.latest_release("o/r", fetch=lambda url, t: {"tag_name": "v0.9.0", "html_url": "h", "body": "notes",
                                                         "published_at": "2026-11-12T00:00:00Z"})
    assert rel.tag == "v0.9.0" and rel.notes == "notes"

    def fail(code):
        def f(url, t):
            raise _http_error(code)
        return f
    assert U.latest_release("o/r", fetch=fail(404)) is None
    with pytest.raises(U.UpdateError, match="rate limit"):
        U.latest_release("o/r", fetch=fail(403))

    def offline(url, t):
        raise urllib.error.URLError("no route")
    with pytest.raises(U.UpdateError, match="could not reach GitHub"):
        U.latest_release("o/r", fetch=offline)


def test_free_entry_script(tmp_path):
    (tmp_path / "svkit").write_text("#!python\n")
    (tmp_path / ".svkit-old-1-1").write_text("old")
    aside = U.free_entry_script(tmp_path)
    assert aside is not None and aside.read_text() == "#!python\n" and not (tmp_path / "svkit").exists()
    assert not (tmp_path / ".svkit-old-1-1").exists()      # earlier leftovers are cleaned up
    U.restore_entry_script(tmp_path, aside)                # pip failed: the old launcher comes back
    assert (tmp_path / "svkit").read_text() == "#!python\n"
    aside = U.free_entry_script(tmp_path)
    (tmp_path / "svkit").write_text("new")                 # pip wrote a new launcher: keep it
    U.restore_entry_script(tmp_path, aside)
    assert (tmp_path / "svkit").read_text() == "new"
    assert U.free_entry_script(tmp_path / "nowhere") is None


def test_install_release(tmp_path):
    py = tmp_path / "bin" / "python"
    py.parent.mkdir()
    (py.parent / "svkit").write_text("launcher")
    calls = []
    installed = {"v": "0.8.0"}

    def run(cmd):
        calls.append(cmd)
        assert not (py.parent / "svkit").exists()          # the busy launcher was moved out of pip's way
        installed["v"] = "0.9.0"
        (py.parent / "svkit").write_text("new launcher")
        return 0
    got = U.install_release("o/r", "v0.9.0", python=str(py), run=run, verify=lambda p: installed["v"])
    assert got == "0.9.0" and len(calls) == 1
    assert calls[0][:4] == [str(py), "-m", "pip", "install"] and "--upgrade" in calls[0]
    assert calls[0][-1] == "svkit @ https://github.com/o/r/archive/refs/tags/v0.9.0.tar.gz"
    assert (py.parent / "svkit").read_text() == "new launcher"

    # pip kept the old files: svkit alone is reinstalled
    seq = iter(["0.8.0", "0.9.0"])
    calls.clear()
    got = U.install_release("o/r", "v0.9.0", python=str(py), run=lambda c: calls.append(c) or 0,
                            verify=lambda p: next(seq), log=lambda m: None)
    assert got == "0.9.0" and "--force-reinstall" in calls[1] and "--no-deps" in calls[1]

    # pip fails: an error, and the launcher is back where it was
    (py.parent / "svkit").write_text("launcher")
    with pytest.raises(U.UpdateError, match="pip failed"):
        U.install_release("o/r", "v0.9.0", python=str(py), run=lambda c: 1, verify=lambda p: None)
    assert (py.parent / "svkit").read_text() == "launcher"

    with pytest.raises(U.UpdateError, match="disagree"):
        U.install_release("o/r", "v0.9.0", python=str(py), run=lambda c: 0, verify=lambda p: "0.8.5",
                          log=lambda m: None)


def test_notice_checks_once_a_day(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("SVKIT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(U, "__version__", "0.8.0")
    asked = []

    def fetch(url, t):
        asked.append(url)
        return {"tag_name": "v0.9.0"}
    msg = U.newer_release_notice(now=1000.0, fetch=fetch)
    assert "v0.9.0 is available" in msg and "svkit update" in msg
    assert U.newer_release_notice(now=1000.0 + 3600, fetch=fetch) == msg and len(asked) == 1   # cached
    U.newer_release_notice(now=1000.0 + 2 * U.CHECK_EVERY_S, fetch=fetch)
    assert len(asked) == 2
    monkeypatch.setattr(U, "__version__", "0.9.0")
    assert U.newer_release_notice(now=1000.0 + 2 * U.CHECK_EVERY_S, fetch=fetch) is None
    monkeypatch.setenv("SVKIT_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(U, "__version__", "0.1.0")
    assert U.newer_release_notice(now=0, fetch=fetch) is None

    def offline(url, t):
        raise urllib.error.URLError("x")
    monkeypatch.delenv("SVKIT_NO_UPDATE_CHECK")
    (tmp_path / "svkit" / "update-check.json").unlink()
    assert U.newer_release_notice(now=5.0, fetch=offline) is None   # never in the way


@pytest.fixture
def cli(monkeypatch):
    from svkit.cli import app
    monkeypatch.setattr("svkit.cli.__version__", "0.8.0")
    monkeypatch.setattr(U, "install_info", lambda *a, **k: U.InstallInfo("release", ref="v0.8.0",
                        url="https://github.com/o/r/archive/refs/tags/v0.8.0.tar.gz"))
    monkeypatch.setattr(U, "running_servers", lambda bindir: [])
    return lambda *args: CliRunner().invoke(app, ["update", *args])


def _latest(monkeypatch, tag):
    monkeypatch.setattr(U, "latest_release", lambda repo, timeout=10.0: U.Release(tag, tag, "u", "2026-11-12", "- a fix"))


def test_cli_update(cli, monkeypatch):
    installs = []
    monkeypatch.setattr(U, "install_release", lambda repo, tag, log=print: installs.append((repo, tag)) or tag[1:])
    _latest(monkeypatch, "v0.8.0")
    res = cli()
    assert res.exit_code == 0 and "up to date" in res.output and not installs
    _latest(monkeypatch, "v0.9.0")
    res = cli("--check")
    assert res.exit_code == 0 and "v0.9.0 is available" in res.output and not installs
    res = cli()
    assert res.exit_code == 0, res.output
    assert installs == [("o/r", "v0.9.0")] and "0.8.0 -> 0.9.0" in res.output and "- a fix" in res.output
    res = cli("--to", "0.7.0")                      # roll back to a chosen release
    assert res.exit_code == 0 and installs[-1] == ("o/r", "v0.7.0")
    monkeypatch.setattr(U, "latest_release", lambda repo, timeout=10.0: None)
    assert "no published release" in cli().output


def test_cli_update_errors(cli, monkeypatch):
    def boom(repo, timeout=10.0):
        raise U.UpdateError("could not reach GitHub: no route")
    monkeypatch.setattr(U, "latest_release", boom)
    res = cli()
    assert res.exit_code == 1 and "could not reach GitHub" in res.output
    monkeypatch.setattr(U, "install_info", lambda *a, **k: U.InstallInfo("local"))
    res = cli()
    assert res.exit_code == 1 and "git pull" in res.output


def test_cli_version():
    from svkit.cli import app
    res = CliRunner().invoke(app, ["--version"])
    assert res.exit_code == 0 and res.output.startswith(f"svkit {svkit.__version__} (")


def test_release_script_bumps(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("release", os.path.join(os.path.dirname(__file__), "..", "scripts", "release.py"))
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)
    assert R.next_version("patch", "0.8.0.dev0") == "0.8.0"
    assert R.next_version("patch", "0.8.0") == "0.8.1"
    assert R.next_version("minor", "0.8.3") == "0.9.0"
    assert R.next_version("major", "0.8.3") == "1.0.0"
    assert R.next_version("v1.2.3", "0.8.3") == "1.2.3"
