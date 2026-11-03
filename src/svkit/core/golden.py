"""Pass/fail evaluation shared by all sim adapters: golden diff + log regexes."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from svkit.config import Golden
from svkit.schemas import Divergence


def first_divergence(actual: Path, expected: Path, golden: Golden) -> Optional[Divergence]:
    """Line-by-line compare (after dropping `ignore` lines). Returns None if identical."""
    ign = [re.compile(p) for p in golden.ignore]

    def lines(p: Path) -> list[tuple[int, str]]:
        out = []
        for i, l in enumerate(p.read_text(errors="replace").splitlines(), 1):
            l = l.rstrip()
            if not any(r.search(l) for r in ign):
                out.append((i, l))
        return out

    a, e = lines(actual), lines(expected)
    for k in range(max(len(a), len(e))):
        al = a[k] if k < len(a) else None
        el = e[k] if k < len(e) else None
        if al is None or el is None or al[1] != el[1]:
            line_no = al[0] if al else (a[-1][0] + 1 if a else 1)
            d = Divergence(line=line_no, expected=el[1] if el else None, actual=al[1] if al else None)
            if golden.time_regex and al:
                m = re.search(golden.time_regex, al[1])
                if m:
                    v = int(m.group(1), 0)
                    if golden.time_unit == "cycle":
                        d.cycle = v
                    else:
                        d.time = v
            return d
    return None


def evaluate(*, exit_code: int, log_text: str, pass_regex: str | None, fail_regex: str | None,
             actual: Path | None, expected: Path | None, golden: Golden | None
             ) -> tuple[Optional[bool], Optional[str], Optional[Divergence]]:
    """Returns (passed, reason, divergence). passed=None when nothing decides it.

    A non-zero exit always fails, but if the output still differs from golden the mismatch is
    reported (with its divergence): check targets like `make t.check` exit non-zero precisely
    because of that difference."""
    if exit_code != 0:
        if golden is not None and actual is not None and expected is not None \
                and actual.is_file() and expected.is_file():
            d = first_divergence(actual, expected, golden)
            if d:
                return False, f"golden mismatch at line {d.line} (exit code {exit_code})", d
        return False, f"exit code {exit_code}", None
    if fail_regex and re.search(fail_regex, log_text, re.MULTILINE):
        return False, f"log matched fail_regex {fail_regex!r}", None
    if golden is not None and actual is not None and expected is not None:
        if not expected.exists():
            return None, f"no golden file {expected}", None
        if not actual.exists():
            return False, f"expected output {actual} was not produced", None
        d = first_divergence(actual, expected, golden)
        if d:
            return False, f"golden mismatch at line {d.line}", d
        return True, None, None
    if pass_regex:
        if re.search(pass_regex, log_text, re.MULTILINE):
            return True, None, None
        return False, f"log did not match pass_regex {pass_regex!r}", None
    return (True if exit_code == 0 else False), None, None
