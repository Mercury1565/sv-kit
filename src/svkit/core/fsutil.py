"""File copies that work on network home directories.

`shutil.copy2` / `copytree` copy extended attributes through `os.listxattr`, which
fails with EAGAIN (not ENOTSUP, which shutil tolerates) on CIFS mounts such as
CAEN home directories. These helpers keep contents, permission bits and
timestamps (make relies on mtimes) and skip xattrs.

CIFS also leaves `.__smbXXXX` placeholders for files deleted while still open. They can stay
listed while opening them fails, so tree copies skip them.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Callable, Iterable, Optional


def is_smb_placeholder(name: str) -> bool:
    """`.__smbXXXX`: a CIFS stand-in for a file deleted while open; never real project content."""
    return name.startswith(".__smb")


def copy_file(src: Path | str, dst: Path | str) -> Path:
    """Like shutil.copy2 for a file destination, minus extended attributes."""
    shutil.copyfile(src, dst)
    st = os.stat(src)
    os.chmod(dst, stat.S_IMODE(st.st_mode))
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    return Path(dst)


def copy_tree(src: Path | str, dst: Path | str,
              ignore: Optional[Callable[[str, list[str]], Iterable[str]]] = None) -> Path:
    """Like shutil.copytree (symlinks kept as links), minus extended attributes."""
    src, dst = Path(src), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    names = os.listdir(src)
    skip = set(ignore(str(src), names)) if ignore else set()
    for name in names:
        if name in skip or is_smb_placeholder(name):
            continue
        s, d = src / name, dst / name
        if s.is_symlink():
            os.symlink(os.readlink(s), d)
        elif s.is_dir():
            copy_tree(s, d, ignore)
        else:
            copy_file(s, d)
    return dst
