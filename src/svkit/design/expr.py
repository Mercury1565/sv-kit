"""Serialized RTL expressions and a small 4-state evaluator.

The DesignGraph stores every right-hand side, condition and index expression
as a plain tuple tree (picklable, no pyslang objects), so the X tracer can
re-evaluate them against waveform values long after the compilation is gone.

Node forms (``w`` = result width in bits):

    ("c", bits, w)                       constant; bits is a binary string over 01xz
    ("s", path, w)                       whole signal reference (w = 0: unpacked aggregate)
    ("m", mem, idx, w, lo, hi)           element read of memory (unpacked array) ``mem``
    ("ps", base, lsb_idx, w, add, right, asc, ew)
                                         part/bit select: lsb element index = eval(lsb_idx) + add
    ("rng", base, off, w)                constant slice (bit offset ``off`` from the LSB)
    ("u", op, a, w)                      unary operator (op = pyslang UnaryOperator name)
    ("b", op, a, b, w)                   binary operator (pyslang BinaryOperator name)
    ("?", cond, a, b, w)                 conditional operator
    ("cv", a, w)                         width conversion
    ("cat", (a, b, ...), w)              concatenation, MSB first
    ("rep", n, a, w)                     replication
    ("x", (deps...), w)                  anything else: value unknown, deps = the signals it reads

Values are binary strings (MSB first) over ``01xz`` with exactly ``w`` chars,
or ``UNK`` when the value cannot be determined (signal not dumped, unsupported
operator). ``UNK`` differs from X: X is a real simulator X, UNK means "can't tell".
"""
from __future__ import annotations

from typing import Callable, Iterator, Optional, Union

Expr = tuple
UNK = None
Value = Optional[str]
Getter = Callable[[str], Value]          # signal path -> binary string or None (not available)
MemGetter = Callable[[str, int], Value]  # (memory path, index) -> word value or None


# ------------------------------------------------------------------ helpers
def has_x(v: Value) -> bool:
    return v is not None and any(ch in "xXzZ" for ch in v)


def fit(v: str, w: int) -> str:
    """Resize a binary string to w bits (zero-extend / truncate from the MSB side)."""
    if w <= 0:
        return v
    if len(v) >= w:
        return v[len(v) - w:]
    return "0" * (w - len(v)) + v


def to_int(v: Value) -> Optional[int]:
    if v is None or v == "" or has_x(v):
        return None
    return int(v, 2)


def from_int(n: int, w: int) -> str:
    w = max(w, 1)
    return format(n & ((1 << w) - 1), "b").zfill(w)


def allx(w: int) -> str:
    return "x" * max(w, 1)


def truth(v: Value) -> Union[bool, str, None]:
    """Verilog truth value of v: True / False / 'x' (has X and no 1 bit) / None (UNK)."""
    if v is None:
        return None
    s = v.lower()
    if "1" in s:
        return True
    if "x" in s or "z" in s:
        return "x"
    return False


def width(e: Expr) -> int:
    k = e[0]
    if k in ("c", "s", "cv", "cat", "x"):
        return e[2]
    if k in ("m", "ps", "rng", "u", "rep"):
        return e[3]
    if k in ("b", "?"):
        return e[4]
    return 0


def signals(e: Expr) -> Iterator[str]:
    """All signal (and memory) paths an expression reads."""
    k = e[0]
    if k == "s":
        yield e[1]
    elif k == "m":
        yield e[1]
        yield from signals(e[2])
    elif k == "c":
        return
    else:
        for ch in children(e):
            yield from signals(ch)


def index_signals(e: Expr) -> Iterator[str]:
    """Signals used as array / select indices inside e."""
    k = e[0]
    if k == "m":
        yield from signals(e[2])
    elif k == "ps":
        yield from signals(e[2])
        yield from index_signals(e[1])
    else:
        for ch in children(e):
            yield from index_signals(ch)


def children(e: Expr) -> list[Expr]:
    k = e[0]
    if k in ("c", "s"):
        return []
    if k == "m":
        return [e[2]]
    if k == "ps":
        return [e[1], e[2]]
    if k in ("rng", "cv"):
        return [e[1]]
    if k == "u":
        return [e[2]]
    if k == "b":
        return [e[2], e[3]]
    if k == "?":
        return [e[1], e[2], e[3]]
    if k == "cat":
        return list(e[1])
    if k == "rep":
        return [e[2]]
    if k == "x":
        return list(e[1])
    return []


def to_text(e: Expr) -> str:
    """Compact human-readable rendering (for notes; not round-trippable)."""
    k = e[0]
    if k == "c":
        return f"{e[2]}'b{e[1]}"
    if k == "s":
        return e[1].rsplit(".", 1)[-1]
    if k == "m":
        return f"{e[1].rsplit('.', 1)[-1]}[{to_text(e[2])}]"
    if k == "?":
        return f"({to_text(e[1])} ? {to_text(e[2])} : {to_text(e[3])})"
    if k == "b":
        return f"({to_text(e[2])} {_BIN_SYM.get(e[1], e[1])} {to_text(e[3])})"
    if k == "u":
        return f"{_UN_SYM.get(e[1], e[1])}{to_text(e[2])}"
    if k in ("cv",):
        return to_text(e[1])
    if k == "cat":
        return "{" + ", ".join(to_text(x) for x in e[1]) + "}"
    return "(...)"


_BIN_SYM = {"Add": "+", "Subtract": "-", "Multiply": "*", "Divide": "/", "Mod": "%", "BinaryAnd": "&",
            "BinaryOr": "|", "BinaryXor": "^", "BinaryXnor": "~^", "Equality": "==", "Inequality": "!=",
            "CaseEquality": "===", "CaseInequality": "!==", "GreaterThanEqual": ">=", "GreaterThan": ">",
            "LessThanEqual": "<=", "LessThan": "<", "LogicalAnd": "&&", "LogicalOr": "||",
            "LogicalShiftLeft": "<<", "LogicalShiftRight": ">>", "ArithmeticShiftLeft": "<<<",
            "ArithmeticShiftRight": ">>>"}
_UN_SYM = {"Plus": "+", "Minus": "-", "BitwiseNot": "~", "BitwiseAnd": "&", "BitwiseOr": "|", "BitwiseXor": "^",
           "BitwiseNand": "~&", "BitwiseNor": "~|", "BitwiseXnor": "~^", "LogicalNot": "!"}


# ------------------------------------------------------------------ evaluator
class Evaluator:
    """Evaluate serialized expressions against signal values (4-state, standard Verilog semantics,
    unsigned arithmetic). ``get(path)`` returns a binary string or None; ``mem(path, idx)`` returns
    a memory word or None."""

    def __init__(self, get: Getter, mem: Optional[MemGetter] = None):
        self.get = get
        self.mem = mem or (lambda p, i: None)

    def eval(self, e: Expr) -> Value:
        try:
            return self._eval(e)
        except (ValueError, IndexError, TypeError, ZeroDivisionError, RecursionError):
            return UNK

    def _eval(self, e: Expr) -> Value:
        k = e[0]
        if k == "c":
            return fit(e[1], e[2]) if e[2] > 0 else e[1]
        if k == "s":
            if e[2] <= 0:
                return UNK
            v = self.get(e[1])
            return None if v is None else fit(v, e[2])
        if k == "m":
            idx = to_int(self._eval(e[2]))
            if idx is None:
                return UNK if self._eval(e[2]) is None else allx(e[3])
            if idx < e[4] or idx > e[5]:
                return allx(e[3])          # out-of-range read returns X (4-state)
            v = self.mem(e[1], idx)
            return None if v is None else fit(v, e[3])
        if k == "rng":
            b = self._eval(e[1])
            if b is None:
                return UNK
            off, w = e[2], e[3]
            return _slice(b, off, w)
        if k == "ps":
            _, base, lsb, w, add, right, asc, ew = e
            b = self._eval(base)
            i = self._eval(lsb)
            if b is None or i is None:
                return UNK
            ii = to_int(i)
            if ii is None:
                return allx(w)
            idx = ii + add
            off = ((idx - right) if not asc else (right - idx)) * max(ew, 1)
            if off < 0 or off + w > len(b):
                return allx(w)
            return _slice(b, off, w)
        if k == "cv":
            a = self._eval(e[1])
            return None if a is None else fit(a, e[2])
        if k == "u":
            return self._unary(e[1], self._eval(e[2]), e[3])
        if k == "b":
            return self._binary(e[1], e[2], e[3], e[4])
        if k == "?":
            c = truth(self._eval(e[1]))
            if c is True:
                return _fitv(self._eval(e[2]), e[4])
            if c is False:
                return _fitv(self._eval(e[3]), e[4])
            a, b = _fitv(self._eval(e[2]), e[4]), _fitv(self._eval(e[3]), e[4])
            if c is None or a is None or b is None:
                return UNK
            return "".join(x if x == y and x in "01" else "x" for x, y in zip(a, b))
        if k == "cat":
            parts = [self._eval(p) for p in e[1]]
            if any(p is None for p in parts):
                return UNK
            return fit("".join(parts), e[2])
        if k == "rep":
            a = self._eval(e[2])
            return None if a is None else fit(a * e[1], e[3])
        return UNK

    # -------------------------------------------------------------- operators
    @staticmethod
    def _unary(op: str, a: Value, w: int) -> Value:
        if a is None:
            return UNK
        if op == "LogicalNot":
            t = truth(a)
            return "x" if t == "x" else ("0" if t else "1")
        if op == "BitwiseNot":
            return fit("".join({"0": "1", "1": "0"}.get(ch, "x") for ch in a), w)
        if op in ("BitwiseAnd", "BitwiseNand"):
            r = "0" if "0" in a else ("x" if has_x(a) else "1")
            return _inv(r) if op == "BitwiseNand" else r
        if op in ("BitwiseOr", "BitwiseNor"):
            r = "1" if "1" in a else ("x" if has_x(a) else "0")
            return _inv(r) if op == "BitwiseNor" else r
        if op in ("BitwiseXor", "BitwiseXnor"):
            if has_x(a):
                return "x"
            r = str(a.count("1") % 2)
            return _inv(r) if op == "BitwiseXnor" else r
        n = to_int(a)
        if n is None:
            return allx(w)
        if op == "Plus":
            return from_int(n, w)
        if op == "Minus":
            return from_int(-n, w)
        return UNK

    def _binary(self, op: str, le: Expr, re_: Expr, w: int) -> Value:
        if op in ("LogicalAnd", "LogicalOr"):
            a = truth(self._eval(le))
            if op == "LogicalAnd" and a is False:
                return "0"
            if op == "LogicalOr" and a is True:
                return "1"
            b = truth(self._eval(re_))
            if op == "LogicalAnd" and b is False:
                return "0"
            if op == "LogicalOr" and b is True:
                return "1"
            if a is None or b is None:
                return UNK
            if a == "x" or b == "x":
                return "x"
            return "1" if (a and b if op == "LogicalAnd" else a or b) else "0"
        a, b = self._eval(le), self._eval(re_)
        if a is None or b is None:
            return UNK
        if op in ("BinaryAnd", "BinaryOr", "BinaryXor", "BinaryXnor"):
            n = max(len(a), len(b), w)
            a, b = fit(a, n), fit(b, n)
            out = []
            for x, y in zip(a, b):
                out.append(_bit(op, x, y))
            return fit("".join(out), w)
        if op in ("CaseEquality", "CaseInequality"):
            n = max(len(a), len(b))
            eq = fit(a, n).lower().replace("z", "z") == fit(b, n).lower()
            return "1" if eq == (op == "CaseEquality") else "0"
        if op in ("Equality", "Inequality"):
            n = max(len(a), len(b))
            a, b = fit(a, n), fit(b, n)
            # definite mismatch on a known bit decides even with X elsewhere
            if any(x in "01" and y in "01" and x != y for x, y in zip(a, b)):
                return "0" if op == "Equality" else "1"
            if has_x(a) or has_x(b):
                return "x"
            return "1" if op == "Equality" else "0"
        x, y = to_int(a), to_int(b)
        if x is None or y is None:
            return allx(w) if w > 0 else "x"
        if op == "Add":
            return from_int(x + y, w)
        if op == "Subtract":
            return from_int(x - y, w)
        if op == "Multiply":
            return from_int(x * y, w)
        if op == "Divide":
            return allx(w) if y == 0 else from_int(x // y, w)
        if op == "Mod":
            return allx(w) if y == 0 else from_int(x % y, w)
        if op in ("LogicalShiftLeft", "ArithmeticShiftLeft"):
            return from_int(x << min(y, 4096), w)
        if op in ("LogicalShiftRight", "ArithmeticShiftRight"):
            return from_int(x >> y, w)
        if op == "Power":
            return from_int(pow(x, min(y, 4096)), w)
        cmp = {"GreaterThanEqual": x >= y, "GreaterThan": x > y, "LessThanEqual": x <= y,
               "LessThan": x < y, "LogicalImplication": (not x) or bool(y),
               "LogicalEquivalence": bool(x) == bool(y)}
        if op in cmp:
            return "1" if cmp[op] else "0"
        return UNK


def _inv(r: str) -> str:
    return {"0": "1", "1": "0"}.get(r, "x")


def _bit(op: str, x: str, y: str) -> str:
    x, y = x.lower(), y.lower()
    if op == "BinaryAnd":
        if x == "0" or y == "0":
            return "0"
        return "1" if x == "1" and y == "1" else "x"
    if op == "BinaryOr":
        if x == "1" or y == "1":
            return "1"
        return "0" if x == "0" and y == "0" else "x"
    if x not in "01" or y not in "01":
        return "x"
    r = "1" if x != y else "0"
    return _inv(r) if op == "BinaryXnor" else r


def _slice(b: str, off: int, w: int) -> str:
    n = len(b)
    if off < 0 or w <= 0 or off >= n:
        return allx(w)
    hi = min(n, off + w)
    s = b[n - hi:n - off]
    return fit(s, w) if len(s) == w else "x" * (w - len(s)) + s


def _fitv(v: Value, w: int) -> Value:
    return None if v is None else fit(v, w)


# ------------------------------------------------------------------ X dependencies
def x_deps(e: Expr, ev: Evaluator) -> list[tuple]:
    """Which inputs of ``e`` explain an X (or unknown) in its value.

    Returns items:
        ("sig", path)                    a signal whose value is X (or not available)
        ("mem", mem, idx)                a memory word mem[idx] that is read (value X or not dumped)
        ("oor", mem, idx, lo, hi)        an out-of-range memory read
    Known (X-free) sub-expressions contribute nothing; the taken branch of a
    conditional is followed when the select is known.
    """
    out: list[tuple] = []
    _xd(e, ev, out)
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def _xd(e: Expr, ev: Evaluator, out: list) -> None:
    k = e[0]
    if k == "c":
        return
    v = ev.eval(e)
    if v is not None and not has_x(v):
        return
    if k == "s":
        out.append(("sig", e[1]))
        return
    if k == "m":
        iv = ev.eval(e[2])
        idx = to_int(iv)
        if idx is None:
            _xd(e[2], ev, out)
            if iv is None:
                out.append(("mem", e[1], None))
            return
        if idx < e[4] or idx > e[5]:
            out.append(("oor", e[1], idx, e[4], e[5]))
            return
        out.append(("mem", e[1], idx))
        return
    if k == "?":
        c = truth(ev.eval(e[1]))
        if c is True:
            _xd(e[2], ev, out)
            return
        if c is False:
            _xd(e[3], ev, out)
            return
        _xd(e[1], ev, out)
        _xd(e[2], ev, out)
        _xd(e[3], ev, out)
        return
    if k == "ps":
        iv = ev.eval(e[2])
        if iv is None or has_x(iv):
            _xd(e[2], ev, out)
        _xd(e[1], ev, out)
        return
    for ch in children(e):
        _xd(ch, ev, out)
