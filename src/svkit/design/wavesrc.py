"""Minimal waveform access used by the X tracer.

``WaveSource`` is the protocol the tracer needs; ``PyWellenSource`` implements it over pywellen
(VCD / FST). Values are binary strings (MSB first) over ``01xz``, exactly ``width`` characters.
Times are integer ticks of the file's timescale.

(The full-featured waveform backend for the UI is ``svkit.adapters.waves.wellen``; this module only
keeps what the tracer needs so the two tracks stay independent.)
"""
from __future__ import annotations

import bisect
from pathlib import Path
from typing import Iterable, Optional, Protocol, runtime_checkable


@runtime_checkable
class WaveSource(Protocol):
    def vars(self) -> Iterable[str]: ...

    def has(self, path: str) -> bool: ...

    def width(self, path: str) -> Optional[int]: ...

    def value_at(self, path: str, t: int) -> Optional[str]:
        """Value at time t (after all changes at t); None if the signal is not in the file."""

    def changes(self, path: str, t0: int, t1: int) -> list[tuple[int, str]]:
        """Changes with t0 <= time <= t1, in time order."""

    def end_time(self) -> int: ...


def fmt_value(raw, width: int) -> str:
    """Normalize a pywellen value (int, str over 01xz, or None) to a binary string of width bits."""
    if raw is None:
        return "x" * max(width, 1)
    if isinstance(raw, int):
        if raw < 0:
            raw &= (1 << width) - 1
        s = format(raw, "b")
    else:
        s = str(raw).lower()
        if s.startswith("b"):
            s = s[1:]
        if any(c not in "01xzuwlh-" for c in s):   # real / string values
            return s
        s = s.translate(str.maketrans("uwlh-", "xx01x"))
    if len(s) < width:
        s = (s[0] if s and s[0] in "xz" else "0") * (width - len(s)) + s
    return s[-width:] if width and len(s) > width else s


class _Times:
    """Sequence view over a pywellen signal's change times (for bisect without copying)."""

    def __init__(self, sig):
        self.sig = sig
        self.n = len(sig)

    def __len__(self):
        return self.n

    def __getitem__(self, i):
        return self.sig[i][0]


class PyWellenSource:
    """WaveSource over a VCD/FST file via pywellen."""

    def __init__(self, path: Path | str):
        import pywellen

        self.path = Path(path)
        self.wf = pywellen.Waveform(str(self.path))
        self._vars: dict[str, object] = {}
        for v in self.wf.all_vars():
            name = v.full_name
            self._vars.setdefault(name, v)
        self._sigs: dict[str, tuple] = {}
        self._end: Optional[int] = None

    # -------------------------------------------------------------- protocol
    def vars(self) -> Iterable[str]:
        return self._vars.keys()

    def has(self, path: str) -> bool:
        return path in self._vars

    def width(self, path: str) -> Optional[int]:
        v = self._vars.get(path)
        return None if v is None else int(v.bitwidth or 1)

    def _sig(self, path: str):
        s = self._sigs.get(path)
        if s is None:
            v = self._vars.get(path)
            if v is None:
                return None
            sig = v.signal
            s = (sig, _Times(sig), int(v.bitwidth or 1))
            self._sigs[path] = s
        return s

    def value_at(self, path: str, t: int) -> Optional[str]:
        s = self._sig(path)
        if s is None:
            return None
        sig, times, w = s
        i = bisect.bisect_right(times, t) - 1
        if i < 0:
            return "x" * w
        return fmt_value(sig[i][1], w)

    def changes(self, path: str, t0: int, t1: int) -> list[tuple[int, str]]:
        s = self._sig(path)
        if s is None:
            return []
        sig, times, w = s
        i = bisect.bisect_left(times, t0)
        j = bisect.bisect_right(times, t1)
        return [(sig[k][0], fmt_value(sig[k][1], w)) for k in range(i, j)]

    def last_change(self, path: str, t: int) -> Optional[tuple[int, str]]:
        """Latest change at or before t."""
        s = self._sig(path)
        if s is None:
            return None
        sig, times, w = s
        i = bisect.bisect_right(times, t) - 1
        if i < 0:
            return None
        return sig[i][0], fmt_value(sig[i][1], w)

    def end_time(self) -> int:
        if self._end is None:
            end = 0
            for name in list(self._vars)[:2000]:
                s = self._sig(name)
                if s and s[1].n:
                    end = max(end, s[1][s[1].n - 1])
            self._end = end
        return self._end


class DictSource:
    """In-memory WaveSource for tests: ``{path: [(t, value), ...]}``."""

    def __init__(self, data: dict[str, list[tuple[int, str]]]):
        self.data = {k: sorted(v) for k, v in data.items()}

    def vars(self):
        return self.data.keys()

    def has(self, path):
        return path in self.data

    def width(self, path):
        ch = self.data.get(path)
        return len(ch[0][1]) if ch else None

    def value_at(self, path, t):
        ch = self.data.get(path)
        if ch is None:
            return None
        i = bisect.bisect_right([c[0] for c in ch], t) - 1
        return ch[i][1] if i >= 0 else "x" * len(ch[0][1])

    def changes(self, path, t0, t1):
        return [c for c in self.data.get(path, []) if t0 <= c[0] <= t1]

    def last_change(self, path, t):
        ch = [c for c in self.data.get(path, []) if c[0] <= t]
        return ch[-1] if ch else None

    def end_time(self):
        return max((c[-1][0] for c in self.data.values() if c), default=0)


def open_waves(path: Path | str) -> WaveSource:
    return PyWellenSource(path)
