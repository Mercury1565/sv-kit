"""svkit command-line interface (docs/ARCHITECTURE.md §7)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

import typer

from svkit import __version__
from svkit.config import ConfigError, load

app = typer.Typer(help="svkit — debug and PPA workbench for SystemVerilog projects", no_args_is_help=True,
                  add_completion=False)
runs_app = typer.Typer(help="List, inspect, diff and tag recorded Runs", no_args_is_help=True)
sweep_app = typer.Typer(help="Parameter sweeps", no_args_is_help=True)
app.add_typer(runs_app, name="runs")
app.add_typer(sweep_app, name="sweep")
waves_app = typer.Typer(help="Waveforms svkit keeps for each run", no_args_is_help=True)
app.add_typer(waves_app, name="waves")

EXIT_FAIL, EXIT_CONFIG, EXIT_TOOL = 1, 2, 3


def _cfg():
    try:
        return load()
    except ConfigError as e:
        typer.secho(f"config error: {e}", fg="red", err=True)
        raise typer.Exit(EXIT_CONFIG)


def _runner(cfg):
    from svkit.core.runner import Runner
    from svkit.core.events import EventBus
    bus = EventBus()
    return Runner(cfg, bus=bus)


def _fmt_status(s: str) -> str:
    color = {"passed": "green", "done": "green", "failed": "red", "error": "red", "running": "yellow"}.get(s)
    return typer.style(s, fg=color) if color else s


@app.callback(invoke_without_command=True)
def _main(version: bool = typer.Option(False, "--version", help="Print version and exit")):
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def init(force: bool = typer.Option(False, help="Overwrite an existing svkit.toml"),
         path: Path = typer.Argument(Path("."), help="Project root")):
    """Scan the project and write a best-guess svkit.toml."""
    from svkit import init as I
    try:
        out, notes = I.init(path.resolve(), force=force)
    except FileExistsError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(EXIT_CONFIG)
    typer.echo(f"wrote {out}")
    for n in notes:
        typer.echo(f"  - {n}")


@app.command()
def sim(test: Optional[str] = typer.Argument(None), all_: bool = typer.Option(False, "--all"),
        flow: Optional[str] = typer.Option(None, help="sim adapter, e.g. vcs / verilator"),
        jobs: int = typer.Option(4), no_cache: bool = typer.Option(False, "--no-cache")):
    """Run one test or all tests; records one Run per test."""
    cfg = _cfg()
    r = _runner(cfg)
    _fallback_note(r, "sim", flow)
    try:
        if all_:
            gid, runs = r.sim_all(flow=flow, jobs=jobs, use_cache=not no_cache)
        elif test:
            runs = [r.sim(test, flow=flow, use_cache=not no_cache)]
        else:
            typer.secho("give a test name or --all", fg="red", err=True)
            raise typer.Exit(EXIT_CONFIG)
    except ConfigError as e:
        typer.secho(f"config error: {e}", fg="red", err=True)
        raise typer.Exit(EXIT_CONFIG)
    bad = 0
    for run in runs:
        typer.echo(f"{run.id}  {run.test:<24} {_fmt_status(run.status):<8}  {run.message or ''}")
        bad += run.status not in ("passed", "done")
    typer.echo(f"{len(runs) - bad}/{len(runs)} passed")
    raise typer.Exit(EXIT_FAIL if bad else 0)


def _fallback_note(r, kind: str, flow: Optional[str]) -> None:
    """Say once, up front, when svkit runs an open-source stand-in for a missing tool."""
    from svkit.core.runner import resolve_sim, resolve_synth
    try:
        rs = (resolve_sim if kind == "sim" else resolve_synth)(r.cfg, flow)
    except ConfigError:
        return
    if rs.note:
        typer.secho(f"note: {rs.note}", fg="yellow", err=True)


@app.command()
def synth(flow: Optional[str] = typer.Option(None), set_: List[str] = typer.Option([], "--set"),
          exp: Optional[str] = typer.Option(None), matrix: bool = typer.Option(False),
          jobs: int = typer.Option(2), no_cache: bool = typer.Option(False, "--no-cache")):
    """Run synthesis (+ timing). --matrix runs every [experiments] entry and prints the compare table."""
    from svkit.core import knobs as K
    cfg = _cfg()
    r = _runner(cfg)
    _fallback_note(r, "synth", flow)
    try:
        if matrix:
            gid, runs = r.matrix(flow=flow, jobs=jobs, use_cache=not no_cache)
        else:
            runs = [r.synth(flow=flow, experiment=exp, sets=K.parse_set(set_), use_cache=not no_cache)]
    except ConfigError as e:
        typer.secho(f"config error: {e}", fg="red", err=True)
        raise typer.Exit(EXIT_CONFIG)
    for run in runs:
        m = run.metrics or {}
        typer.echo(f"{run.id}  {run.experiment or '-':<18} {_fmt_status(run.status):<6} "
                   f"WNS={_n(m.get('wns'))}ps area={_n(m.get('area_total'))} "
                   f"power={_n((m.get('power') or {}).get('total'))}mW  {run.message or ''}")
    if len(runs) > 1:
        _print_compare([x.id for x in runs if x.status == "done"], r.db)
    raise typer.Exit(EXIT_TOOL if any(x.status == "error" for x in runs) else 0)


def _n(v) -> str:
    if v is None:
        return "-"
    return f"{v:.4g}" if isinstance(v, float) else str(v)


def _print_compare(ids: list[str], db) -> None:
    try:
        from svkit.analysis.compare import compare
    except ImportError:
        return
    if len(ids) < 2:
        return
    res = compare([db.get_run(i) for i in ids])
    typer.echo("\n" + f"{'metric':<16}" + "".join(f"{l[:14]:>16}" for l in res.labels))
    for m in res.metrics:
        cells = []
        for v, d, verdict in zip(m.values, m.deltas, m.verdict):
            mark = {"better": " ▲", "worse": " ▼"}.get(verdict, "  ")
            cells.append(f"{_n(v) + mark:>16}")
        typer.echo(f"{m.metric:<16}" + "".join(cells))


@sweep_app.command("clock")
def sweep_clock(tol: float = typer.Option(50.0, help="bracket tolerance (ps)"), jobs: int = typer.Option(2),
                flow: Optional[str] = typer.Option(None), start: Optional[float] = typer.Option(None, help="T0 (ps)")):
    """Find the minimum clock period with no setup or hold violation."""
    from svkit.analysis import sweep
    cfg = _cfg()
    r = _runner(cfg)
    try:
        res = sweep.sweep_clock(r, tol=tol, jobs=jobs, flow=flow, start=start)
    except ConfigError as e:
        typer.secho(f"config error: {e}", fg="red", err=True)
        raise typer.Exit(EXIT_CONFIG)
    for p in res.points:
        typer.echo(f"  {p.period:>10.1f} ps  WNS={_n(p.wns):>8}  hold={_n(p.hold_wns):>8}  "
                   f"{'PASS' if p.passed else 'fail'}  {p.run_id}")
    if res.note:
        typer.secho(f"note: {res.note}", fg="yellow")
    if res.t_min is None:
        typer.secho("no passing period found", fg="red")
        raise typer.Exit(EXIT_FAIL)
    typer.secho(f"T_min = {res.t_min:.1f} ps ({res.t_min_run_id}, group {res.group_id})", fg="green")


@app.command()
def xtrace(signal: str, at: str = typer.Argument(..., metavar="@TIME"), run: Optional[str] = typer.Option(None),
           depth: int = typer.Option(12), as_json: bool = typer.Option(False, "--json")):
    """Trace an X on SIGNAL at @TIME back to its root cause."""
    from svkit.analysis import xtrace as X
    cfg = _cfg()
    from svkit.core.rundb import RunDB
    db = RunDB.for_project(cfg.root)
    t = int(at.lstrip("@"))
    node = X.trace_run(cfg, db, run_id=run, signal=signal, time=t, depth=depth)
    if as_json:
        typer.echo(node.model_dump_json(indent=2))
    else:
        X.print_tree(node, typer.echo)


@runs_app.command("ls")
def runs_ls(kind: Optional[str] = None, limit: int = 30):
    from svkit.core.rundb import RunDB
    db = RunDB.for_project(_cfg().root)
    for r in db.list_runs(kind=kind, limit=limit):
        label = r.test or r.experiment or ""
        typer.echo(f"{r.id}  {r.kind:<11} {r.adapter:<10} {label:<20} {_fmt_status(r.status):<8} "
                   f"{(r.duration_s or 0):7.1f}s  {(r.git_sha or '')[:8]}{'*' if r.git_dirty else ''}  "
                   f"{' '.join('#' + t for t in r.tags)}")


@runs_app.command("show")
def runs_show(run_id: str):
    from svkit.core.rundb import RunDB
    db = RunDB.for_project(_cfg().root)
    typer.echo(json.dumps(db.get_run(run_id).to_dict(), indent=2, default=str))


@runs_app.command("diff")
def runs_diff(a: str, b: str):
    from svkit.core.rundb import RunDB
    db = RunDB.for_project(_cfg().root)
    _print_compare([a, b], db)


@runs_app.command("tag")
def runs_tag(run_id: str, tag: str):
    from svkit.core.rundb import RunDB
    db = RunDB.for_project(_cfg().root)
    db.add_tag(run_id, tag)
    typer.echo(f"{run_id} #{tag}")


@waves_app.command("archive")
def waves_archive(to: str = typer.Option(..., "--to", help="fst (compact) or vcd (plain text)"),
                  dry_run: bool = typer.Option(False, "--dry-run", help="only list what would be converted")):
    """Convert the waveforms of recorded runs to FST or back to VCD (new runs follow waves.archive in svkit.toml)."""
    from svkit.core import wavestore
    from svkit.core.rundb import RunDB
    to = to.lower().lstrip(".")
    if to not in wavestore.CONVERTERS:
        typer.secho("--to must be fst or vcd", fg="red", err=True)
        raise typer.Exit(2)
    cfg = _cfg()
    if not dry_run and wavestore.converter(cfg, to) is None:
        typer.secho(f"{wavestore.CONVERTERS[to]} not found (GTKWave / OSS CAD Suite); nothing converted", fg="red", err=True)
        raise typer.Exit(1)
    res = wavestore.rearchive(cfg, RunDB.for_project(cfg.root), to, dry_run=dry_run, progress=typer.echo)
    mb = lambda n: f"{n / 1e6:.1f} MB"  # noqa: E731
    if dry_run:
        typer.echo(f"{len(res.converted)} waveform(s) would be converted to {to.upper()} ({mb(res.before)} now)")
    else:
        typer.echo(f"converted {len(res.converted)} waveform(s) to {to.upper()}: {mb(res.before)} -> {mb(res.after)}")
    for rid, msg in res.failed:
        typer.secho(f"  {rid}: kept as is ({msg})", fg="yellow")
    if cfg.waves.archive != to:
        typer.secho(f"note: [waves].archive = \"{cfg.waves.archive}\", so new runs are still stored as "
                    f"{cfg.waves.archive.upper()}; set archive = \"{to}\" in svkit.toml to match", fg="yellow")


@app.command()
def serve(port: Optional[int] = typer.Option(None), host: Optional[str] = typer.Option(None)):
    """Start the dashboard (tunnel with: ssh -L 3572:localhost:3572 <host>)."""
    import uvicorn
    from svkit.server.app import create_app
    cfg = _cfg()
    port = port or cfg.server.port
    host = host or cfg.server.host
    typer.echo(f"svkit dashboard on http://{host}:{port}  (remote: ssh -L {port}:localhost:{port} <server>)")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


def main() -> None:  # pragma: no cover
    """Console entry point: tools run in their own sessions, so stop them on Ctrl-C / SIGTERM / exit."""
    import atexit
    import signal
    from svkit.adapters.base import kill_children

    def on_signal(signum, frame):
        kill_children()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    atexit.register(kill_children)
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
