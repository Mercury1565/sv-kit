"""Shared helpers for the Synopsys adapters (VCS, Design Compiler).

License seats: Synopsys licenses are shared, so every licensed tool invocation made by svkit
(dc_shell-t, vcs compile, simv run, vcs -vpd2vcd) holds one *host-wide* seat. Seats are
flock()ed slot files, so the cap holds across threads, svkit processes and agents on the
same machine. Defaults: 2 seats (`SVKIT_SYNOPSYS_MAX_JOBS`), lock dir
`$SVKIT_LOCK_DIR` or `~/.cache/svkit/locks`.

The runner's per-flow `license_seats` semaphore still applies on top of this.
"""
from __future__ import annotations

import fcntl
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

DEFAULT_SEATS = 2


def lock_dir() -> Path:
    d = Path(os.environ.get("SVKIT_LOCK_DIR") or Path.home() / ".cache" / "svkit" / "locks")
    d.mkdir(parents=True, exist_ok=True)
    return d


def max_seats() -> int:
    try:
        return max(1, int(os.environ.get("SVKIT_SYNOPSYS_MAX_JOBS", DEFAULT_SEATS)))
    except ValueError:
        return DEFAULT_SEATS


class Cancelled(RuntimeError):
    pass


@contextmanager
def synopsys_seat(cancel: Optional[threading.Event] = None, log: Callable[[str], None] = lambda s: None,
                  *, name: str = "synopsys", poll_s: float = 0.5) -> Iterator[int]:
    """Hold one of `max_seats()` host-wide seats while running a licensed Synopsys tool."""
    n = max_seats()
    d = lock_dir()
    waited = False
    while True:
        for i in range(n):
            fh = open(d / f"{name}.{i}.lock", "a+")
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                continue
            try:
                yield i
            finally:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                finally:
                    fh.close()
            return
        if not waited:
            log(f"[svkit] waiting for a Synopsys seat ({n} in use on this host)")
            waited = True
        if cancel is not None and cancel.wait(poll_s):
            raise Cancelled("cancelled while waiting for a Synopsys seat")
        if cancel is None:
            time.sleep(poll_s)
