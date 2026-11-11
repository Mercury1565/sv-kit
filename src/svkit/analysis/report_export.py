"""One plain-text file holding a synthesis run's reports, for download (GET /api/runs/{id}/reports.txt).

It starts with svkit's normalized summary (the same numbers on every flow), then appends each text report the
run kept, verbatim: DC's .rpt/.rep files, Yosys's stat.txt, OpenSTA's sta.log, a wrapped flow's own reports.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional

from svkit.core.rundb import Run

SYNTH_KINDS = ("synth", "sweep-point")
REPORT_SUFFIXES = (".rpt", ".rep", ".txt")
REPORT_NAMES = ("sta.log",)          # OpenSTA writes its timing reports to its log
RULE = "=" * 100


def is_report(name: str) -> bool:
    return name.endswith(REPORT_SUFFIXES) or name in REPORT_NAMES


def report_names(artifacts: dict[str, str]) -> list[str]:
    """Text reports among a run's artifacts, without the per-section copies DC splits out of a combined
    report (`<stem>.<kind>.rpt` next to `<stem>.rep`): the combined file already holds them."""
    names = [n for n in artifacts if is_report(n)]

    def split_copy(n: str) -> bool:
        m = re.fullmatch(r"(.+)\.[a-z_]+\.rpt", n)
        return bool(m) and any(o != n and Path(o).stem == m.group(1) for o in names)
    return [n for n in names if not split_copy(n)]


def _num(v: Any, unit: str = "", digits: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}{unit}"
    return f"{float(v):,.{digits}f}".rstrip("0").rstrip(".") + unit


def summary_text(run: Run) -> str:
    m: dict[str, Any] = run.metrics or {}
    p = m.get("power") or {}
    lines = [
        f"svkit synthesis reports — run {run.id}" + (f" \"{run.name}\"" if run.name else ""),
        RULE,
        f"Run            {run.id} ({run.kind})" + (f", reused from {run.cached_from}" if run.cached_from else ""),
        f"Name           {run.name or '—'}",
        f"Tool           {m.get('tool') or run.adapter}",
        f"Top            {m.get('top') or '—'}",
        f"Status         {run.status}",
        f"Created        {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(run.created_at))}",
        f"Git            {(run.git_sha or '—')[:12]}{' (dirty)' if run.git_dirty else ''}",
    ]
    if run.experiment:
        lines.append(f"Experiment     {run.experiment}")
    if run.knobs:
        lines.append("Knobs          " + " ".join(f"{k}={v}" for k, v in sorted(run.knobs.items())))
    if m:
        lines += [
            "",
            f"Clock period   {_num(m.get('period'), ' ps')}" + (f"  (corner {m['corner']})" if m.get("corner") else ""),
            f"Setup WNS      {_num(m.get('wns'), ' ps')}    TNS {_num(m.get('tns'), ' ps')}",
            f"Hold WNS       {_num(m.get('hold_wns'), ' ps')}    TNS {_num(m.get('hold_tns'), ' ps')}",
            f"Area           {_num(m.get('area_total'), ' ' + (m.get('area_unit') or ''))}",
            f"Cells          {_num(m.get('cell_count'))}",
            f"Power          total {_num(p.get('total'), ' mW', 4)}   dynamic {_num(p.get('dynamic'), ' mW', 4)}"
            f"   leakage {_num(p.get('leakage'), ' mW', 4)}",
        ]
        paths = m.get("paths") or []
        if paths:
            lines += ["", f"Worst timing paths ({min(len(paths), 10)} of {len(paths)})",
                      f"  {'slack (ps)':>12}  {'type':<5} startpoint -> endpoint"]
            for t in paths[:10]:
                lines.append(f"  {_num(t.get('slack')):>12}  {('hold' if t.get('path_type') == 'min' else 'setup'):<5} "
                             f"{t.get('startpoint')} -> {t.get('endpoint')}")
    elif run.message:
        lines += ["", f"Message        {run.message}"]
    return "\n".join(lines) + "\n"


def reports_text(run: Run, run_dir: Path, runs_root: Path) -> str:
    """The downloadable text: summary, then every kept report (artifact paths may point at the original run
    of a reused result, so they are resolved from `run_dir` and must stay under `runs_root`)."""
    parts = [summary_text(run)]
    names = report_names(run.artifacts)
    root = runs_root.resolve()
    for n in names:
        f = (run_dir / run.artifacts[n]).resolve()
        if not f.is_relative_to(root) or not f.is_file():
            parts.append(f"\n{RULE}\n{n}\n{RULE}\n(missing: the file is no longer in the run directory)\n")
            continue
        text = f.read_text(errors="replace")
        parts.append(f"\n{RULE}\n{n}\n{RULE}\n{text}{'' if text.endswith(chr(10)) else chr(10)}")
    if not names:
        parts.append("\n(This run kept no text reports; the numbers above are all svkit has for it.)\n")
    return "".join(parts)


def download_name(run: Run, suffix: str = "synth-reports.txt") -> str:
    slug: Optional[str] = re.sub(r"[^A-Za-z0-9._-]+", "-", run.name).strip("-.")[:60] if run.name else None
    return f"{run.id}-{slug + '-' if slug else ''}{suffix}"
