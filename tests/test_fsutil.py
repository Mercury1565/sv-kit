"""Copies must survive filesystems where listxattr fails with EAGAIN (CIFS homes on CAEN)."""
import errno
import os
import shutil

import pytest

from svkit.core.fsutil import copy_file, copy_tree


@pytest.fixture
def cifs_xattrs(monkeypatch):
    def boom(*a, **k):
        raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
    monkeypatch.setattr(os, "listxattr", boom)


def test_shutil_copy2_fails_like_cifs(tmp_path, cifs_xattrs):
    (tmp_path / "a").write_text("x")
    with pytest.raises(BlockingIOError):
        shutil.copy2(tmp_path / "a", tmp_path / "b")


def test_copy_file_keeps_mode_and_mtime(tmp_path, cifs_xattrs):
    src = tmp_path / "Makefile"
    src.write_text("all:\n")
    os.chmod(src, 0o750)
    os.utime(src, ns=(1_000_000_000, 2_000_000_000))
    dst = copy_file(src, tmp_path / "copy")
    assert dst.read_text() == "all:\n"
    assert dst.stat().st_mode & 0o777 == 0o750
    assert dst.stat().st_mtime_ns == 2_000_000_000


def test_copy_tree_ignore_and_symlinks(tmp_path, cifs_xattrs):
    src = tmp_path / "proj"
    (src / "rtl").mkdir(parents=True)
    (src / "rtl" / "top.sv").write_text("module top; endmodule\n")
    (src / ".svkit").mkdir()
    (src / ".svkit" / "runs.sqlite").write_text("")
    os.symlink("rtl/top.sv", src / "link.sv")
    dst = copy_tree(src, tmp_path / "out", ignore=shutil.ignore_patterns(".svkit"))
    assert (dst / "rtl" / "top.sv").read_text() == "module top; endmodule\n"
    assert os.readlink(dst / "link.sv") == "rtl/top.sv"
    assert not (dst / ".svkit").exists()
