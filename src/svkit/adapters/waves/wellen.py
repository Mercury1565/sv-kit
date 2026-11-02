"""Waveform access (VCD / FST) through pywellen.

`WaveFile` wraps one open waveform. Files are opened once and kept in a small
LRU cache keyed by (path, mtime, size) — `open_wave(path)` — so a multi-hundred-MB
dump is parsed once and later queries only touch the signals they ask for
(signals are loaded lazily, one at a time, and cached).

Value encoding (wave API, docs/ARCHITECTURE.md §6):
  * bit vectors: binary strings over `01xz`, MSB first, exactly `width` chars
  * real:        `str(float)`
  * string vars: the string itself
  * `"*"`:       DENSE marker produced by downsampling: "many transitions from
                 here until the next entry". The entry after a `"*"` carries the
                 settled value at the end of the dense region.

Times are integer ticks of the file's timescale (see `meta()`).
"""
from __future__ import annotations

import fnmatch
import math
import re
import struct
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Optional

import pywellen

DENSE = "*"
SEARCH_SCAN_CAP = 5_000_000      # max hierarchy entries scanned per search
_CACHE_SIZE = 4


class WaveError(Exception):
    pass


class UnknownSignal(WaveError, KeyError):
    pass


def _fmt(value: Any, width: Optional[int], is_real: bool) -> Optional[str]:
    """Normalize a pywellen value to the wave-API string encoding."""
    if value is None:
        return None
    if is_real or isinstance(value, float):
        return repr(float(value)) if not isinstance(value, str) else value
    if isinstance(value, int):
        if width is None or width <= 0:
            return format(value, "b")
        return format(value, f"0{width}b")[-width:] if value >= 0 else format(value & ((1 << width) - 1), f"0{width}b")
    s = str(value).lower()
    if width and len(s) < width and all(c in "01xz" for c in s):
        pad = s[0] if s and s[0] in "xz" else "0"   # VCD left-extension rule
        s = pad * (width - len(s)) + s
    return s


class _Sig:
    """A loaded signal: change times + lazily formatted values."""
    __slots__ = ("sig", "n", "width", "is_real")

    def __init__(self, sig, width: Optional[int], is_real: bool):
        self.sig = sig
        self.n = len(sig)
        self.width = width
        self.is_real = is_real

    def time(self, i: int) -> int:
        return self.sig[i][0]

    def value(self, i: int) -> Optional[str]:
        return _fmt(self.sig[i][1], self.width, self.is_real)

    def bisect_right(self, t: int) -> int:
        """Number of changes with time <= t (index of the first change after t)."""
        lo, hi = 0, self.n
        while lo < hi:
            mid = (lo + hi) // 2
            if self.time(mid) <= t:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def bisect_left(self, t: int) -> int:
        """Index of the first change with time >= t."""
        lo, hi = 0, self.n
        while lo < hi:
            mid = (lo + hi) // 2
            if self.time(mid) < t:
                lo = mid + 1
            else:
                hi = mid
        return lo


class WaveFile:
    """One open VCD/FST waveform. Thread-safe (queries are serialized per file)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if not self.path.is_file():
            raise WaveError(f"waveform {self.path} not found")
        try:
            self.w = pywellen.Waveform(str(self.path))
        except Exception as e:  # pywellen raises plain exceptions for parse errors
            raise WaveError(f"cannot open waveform {self.path.name}: {e}") from e
        self._lock = threading.RLock()
        self._vars: Optional[dict[str, Any]] = None        # full path -> Var
        self._scopes: Optional[dict[str, Any]] = None      # full path -> Scope
        self._sigs: "OrderedDict[str, _Sig]" = OrderedDict()
        self._range: Optional[tuple[int, int]] = None

    # ------------------------------------------------------------ hierarchy index
    def _var_index(self) -> dict[str, Any]:
        if self._vars is None:
            self._vars = {v.full_name: v for v in self.w.all_vars()}
        return self._vars

    def _scope_index(self) -> dict[str, Any]:
        if self._scopes is None:
            self._scopes = {s.full_name: s for s in self.w.all_scopes()}
        return self._scopes

    def var(self, path: str):
        v = self._var_index().get(path)
        if v is None:
            raise UnknownSignal(path)
        return v

    @staticmethod
    def _width(v) -> Optional[int]:
        if v.is_real:
            return None
        return v.bitwidth if v.bitwidth is not None else v.length

    @staticmethod
    def _kind(v) -> str:
        return str(v.var_type).lower()

    def _var_info(self, v) -> dict[str, Any]:
        d: dict[str, Any] = {"name": v.name, "path": v.full_name, "width": self._width(v),
                             "kind": "real" if v.is_real else self._kind(v)}
        et = v.enum_type
        if et:
            d["type"] = str(et[0]) if isinstance(et, (tuple, list)) else str(et)
        return d

    def signal(self, path: str) -> _Sig:
        with self._lock:
            s = self._sigs.get(path)
            if s is not None:
                self._sigs.move_to_end(path)
                return s
            v = self.var(path)
            s = _Sig(v.signal, self._width(v), bool(v.is_real))
            self._sigs[path] = s
            while len(self._sigs) > 512:
                self._sigs.popitem(last=False)
            return s

    # ------------------------------------------------------------ meta
    def time_range(self) -> tuple[int, int]:
        if self._range is None:
            self._range = _file_time_range(self.path, self.w.file_format) or self._range_from_signals()
        return self._range

    def _range_from_signals(self) -> tuple[int, int]:
        lo, hi = None, 0
        for v in list(self.w.vars())[:64] + [x for s in self.w.scopes() for x in s.vars()][:64]:
            s = self.signal(v.full_name)
            if s.n:
                lo = s.time(0) if lo is None else min(lo, s.time(0))
                hi = max(hi, s.time(s.n - 1))
        return (lo or 0, hi)

    def meta(self) -> dict[str, Any]:
        ts = self.w.timescale
        t0, t1 = self.time_range()
        return {"file": self.path.name, "format": str(self.w.file_format).lower(),
                "timescale": {"factor": int(ts.factor) if ts else 1, "unit": str(ts.unit) if ts else "s"},
                "t_start": t0, "t_end": t1}

    def scope(self, path: str = "") -> dict[str, Any]:
        with self._lock:
            if not path:
                scopes, vars_ = self.w.scopes(), self.w.vars()
            else:
                sc = self._scope_index().get(path)
                if sc is None:
                    raise UnknownSignal(path)
                scopes, vars_ = sc.scopes(), sc.vars()
            return {"path": path,
                    "scopes": [{"name": s.name, "path": s.full_name, "kind": str(s.scope_type).lower()}
                               for s in scopes],
                    "vars": [self._var_info(v) for v in vars_]}

    def search(self, q: str, limit: int = 100) -> list[dict[str, Any]]:
        """Case-insensitive substring match, or glob when `q` contains * ? [ ."""
        q = q.strip().lower()
        if not q:
            return []
        with self._lock:
            idx = self._var_index()
            if any(c in q for c in "*?["):
                rx = re.compile(fnmatch.translate(q))
                match = lambda p: rx.match(p) is not None  # noqa: E731
            else:
                match = lambda p: q in p  # noqa: E731
            out = []
            for n, (p, v) in enumerate(idx.items()):
                if n >= SEARCH_SCAN_CAP or len(out) >= limit:
                    break
                if match(p.lower()):
                    out.append({"path": p, "width": self._width(v), "kind": "real" if v.is_real else self._kind(v)})
            return out

    # ------------------------------------------------------------ values
    def value_at(self, path: str, t: int) -> Optional[str]:
        with self._lock:
            s = self.signal(path)
            i = s.bisect_right(t) - 1
            return s.value(i) if i >= 0 else None

    def changes(self, path: str, t0: int | None = None, t1: int | None = None) -> Iterable[tuple[int, str]]:
        """All (time, value) changes with t0 <= time <= t1 (no downsampling)."""
        with self._lock:
            s = self.signal(path)
            i = 0 if t0 is None else s.bisect_left(t0)
            j = s.n if t1 is None else s.bisect_right(t1)
            return [(s.time(k), s.value(k)) for k in range(i, j)]

    def data(self, signals: list[str], t0: int, t1: int, max_changes: int = 2000) -> dict[str, Any]:
        if t1 < t0:
            t0, t1 = t1, t0
        max_changes = max(1, int(max_changes))
        missing = [p for p in signals if p not in self._var_index()]
        if missing:
            raise UnknownSignal(", ".join(missing))
        out = {}
        with self._lock:
            for p in signals:
                out[p] = self._data1(p, t0, t1, max_changes)
        return {"signals": out}

    def _data1(self, path: str, t0: int, t1: int, max_changes: int) -> dict[str, Any]:
        s = self.signal(path)
        a = s.bisect_right(t0)            # first change strictly after t0
        b = s.bisect_right(t1)            # end (exclusive) of changes <= t1
        init = s.value(a - 1) if a > 0 else None
        if b - a <= max_changes:
            return {"width": s.width, "init": init, "changes": [[s.time(k), s.value(k)] for k in range(a, b)],
                    "truncated": False}
        return {"width": s.width, "init": init, "changes": thin(s, a, b, t0, t1, max_changes), "truncated": True}

    def next(self, path: str, t: int, what: str = "change", direction: int = 1) -> Optional[int]:
        """Time of the next (dir=1) / previous (dir=-1) change, or change INTO a value with x/z bits."""
        with self._lock:
            s = self.signal(path)
            if direction >= 0:
                i = s.bisect_right(t)
                rng = range(i, s.n)
            else:
                i = s.bisect_left(t) - 1
                rng = range(i, -1, -1)
            for k in rng:
                if what == "change":
                    return s.time(k)
                v = s.value(k)
                if v is not None and ("x" in v or "z" in v) and not s.is_real:
                    return s.time(k)
            return None


def thin(s: _Sig, a: int, b: int, t0: int, t1: int, max_changes: int) -> list[list]:
    """Downsample changes s[a:b] so no two returned entries are closer than
    w = ceil((t1 - t0) / max_changes). A run of changes closer than w collapses
    to [t_first, "*"] followed by [t_end, settled value] (omitted if t_end > t1).
    Work is O(max_changes · log n), independent of how dense the signal is."""
    w = max(1, math.ceil((t1 - t0) / max_changes))
    out: list[list] = []
    i = a
    while i < b:
        t = s.time(i)
        j = s.bisect_left(t + w)
        if j - i == 1:
            out.append([t, s.value(i)])
            i = j
            continue
        e = t + w
        k = j
        while k < b and s.time(k) < e + w:      # next change too close to the block end: absorb it
            e = s.time(k) + w
            k = s.bisect_left(e)
        out.append([t, DENSE])
        if e <= t1:
            out.append([e, s.value(k - 1)])
        i = k
    return out


# ------------------------------------------------------------------ time range
def _file_time_range(path: Path, fmt: str) -> Optional[tuple[int, int]]:
    try:
        if str(fmt).upper() == "FST":
            with open(path, "rb") as f:
                hdr = f.read(25)
            if len(hdr) == 25 and hdr[0] == 0:
                start, end = struct.unpack(">QQ", hdr[9:25])
                return int(start), int(end)
            return None
        if str(fmt).upper() == "VCD":
            return _vcd_range(path)
    except OSError:
        return None
    return None


_TS = re.compile(rb"(?m)^\s*#(\d+)")


def _vcd_range(path: Path) -> Optional[tuple[int, int]]:
    size = path.stat().st_size
    start = None
    with open(path, "rb") as f:
        # first timestamp after $enddefinitions (stream the header in chunks)
        buf, seen_end = b"", False
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            buf += chunk
            if not seen_end:
                k = buf.find(b"$enddefinitions")
                if k < 0:
                    buf = buf[-32:]
                    continue
                seen_end, buf = True, buf[k:]
            m = _TS.search(buf)
            if m:
                start = int(m.group(1))
                break
            buf = buf[-64:]
        end = None
        back = 1 << 16
        while back <= max(size, 1 << 16) * 2:
            f.seek(max(0, size - back))
            tail = f.read()
            ms = list(_TS.finditer(tail))
            if ms:
                end = int(ms[-1].group(1))
                break
            if back >= size:
                break
            back *= 4
    if start is None or end is None:
        return None
    return start, end


# ------------------------------------------------------------------ cache
_cache: "OrderedDict[tuple, WaveFile]" = OrderedDict()
_cache_lock = threading.Lock()


def open_wave(path: Path | str) -> WaveFile:
    """Open (or reuse) a waveform. LRU-cached by (path, mtime, size)."""
    p = Path(path).resolve()
    try:
        st = p.stat()
    except FileNotFoundError:
        raise WaveError(f"waveform {p} not found")
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _cache_lock:
        wf = _cache.get(key)
        if wf is not None:
            _cache.move_to_end(key)
            return wf
    wf = WaveFile(p)        # parse outside the global lock
    with _cache_lock:
        for k in [k for k in _cache if k[0] == key[0]]:   # stale versions of the same file
            del _cache[k]
        _cache[key] = wf
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return wf


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ------------------------------------------------------------------ helpers for other modules
def find_vars(wf: WaveFile, name: str, under: Optional[str] = None) -> list[str]:
    """Full paths of vars whose leaf name is `name`, shallowest first; ones under
    scope `under` (a scope name anywhere in the path) are preferred."""
    hits = [p for p, v in wf._var_index().items() if v.name == name]
    def key(p: str):
        parts = p.split(".")
        return (0 if under and under in parts[:-1] else 1, len(parts), p)
    return sorted(hits, key=key)
