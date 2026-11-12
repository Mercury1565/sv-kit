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
perf_app = typer.Typer(help="Performance (CPI / TPI)", no_args_is_help=True)
app.add_typer(perf_app, name="perf")

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
        from svkit.update import version_string
        typer.echo(version_string())
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
def sim(tests: Optional[List[str]] = typer.Argument(None, metavar="[TEST]...",
                                                    help="one or more tests; several form one batch"),
        all_: bool = typer.Option(False, "--all", help="every test, as one batch"),
        flow: Optional[str] = typer.Option(None, help="sim adapter, e.g. vcs / verilator"),
        jobs: int = typer.Option(4), no_cache: bool = typer.Option(False, "--no-cache"),
        name: Optional[str] = typer.Option(None, "--name", help="your own name for this run or batch")):
    """Run one test, several tests, or all tests; records one Run per test.

    Several tests (or --all) run as one batch, which the Perf page can show on its own."""
    cfg = _cfg()
    r = _runner(cfg)
    _fallback_note(r, "sim", flow)
    tests = tests or []
    try:
        if all_ and tests:
            typer.secho("give test names or --all, not both", fg="red", err=True)
            raise typer.Exit(EXIT_CONFIG)
        if all_ or len(tests) > 1:
            gid, runs = r.sim_all(flow=flow, jobs=jobs, tests=tests or None, use_cache=not no_cache, name=name)
            typer.echo(f"batch {gid}" + (f"  \"{name.strip()}\"" if name and name.strip() else ""))
        elif tests:
            runs = [r.sim(tests[0], flow=flow, use_cache=not no_cache, name=name)]
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
          jobs: int = typer.Option(2), no_cache: bool = typer.Option(False, "--no-cache"),
          name: Optional[str] = typer.Option(None, "--name", help="your own name for this run (or matrix batch)")):
    """Run synthesis (+ timing). --matrix runs every [experiments] entry and prints the compare table."""
    from svkit.core import knobs as K
    cfg = _cfg()
    r = _runner(cfg)
    _fallback_note(r, "synth", flow)
    try:
        if matrix:
            gid, runs = r.matrix(flow=flow, jobs=jobs, use_cache=not no_cache, name=name)
        else:
            runs = [r.synth(flow=flow, experiment=exp, sets=K.parse_set(set_), use_cache=not no_cache, name=name)]
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


@app.command()
def rename(item: str = typer.Argument(..., help="a run (r_0012) or a batch (g_0004)"),
           name: str = typer.Argument("", help="the new name; leave out to remove the name")):
    """Give a run or a batch your own name (shown in the dashboard and in compare tables)."""
    from svkit.core.rundb import RunDB
    cfg = _cfg()
    try:
        RunDB.for_project(cfg.root).set_name(item, name)
    except KeyError:
        typer.secho(f"no run or batch {item}", fg="red", err=True)
        raise typer.Exit(EXIT_CONFIG)
    typer.echo(f"{item}: " + (f"named \"{' '.join(name.split())}\"" if name.strip() else "name removed"))


@perf_app.command("period")
def perf_period(source: Optional[str] = typer.Argument(
        None, metavar="[toml|sweep|PS]",
        help="toml: the clock_period knob in svkit.toml; sweep: the latest clock sweep; a number: that period in ps")):
    """Show or set where sim runs take their clock period (for TPI) from as they finish.
    Runs that already finished keep theirs."""
    from svkit.analysis import perf as PF
    from svkit.core.rundb import RunDB
    cfg = _cfg()
    db = RunDB.for_project(cfg.root)
    if source is not None:
        try:
            if source in ("toml", "sweep"):
                PF.set_period_choice(db, source)
            else:
                try:
                    ps = float(source.removesuffix("ps"))
                except ValueError:
                    raise ValueError(f"expected toml, sweep or a period in ps, got {source!r}")
                PF.set_period_choice(db, "manual", ps)
        except ValueError as e:
            typer.secho(str(e), fg="red", err=True)
            raise typer.Exit(EXIT_CONFIG)
    choice = PF.period_choice(db)
    period, src = PF.current_period(cfg, db)
    what = (f"entered by hand ({choice['ps']:g} ps)" if choice["from"] == "manual"
            else {"toml": "svkit.toml (clock_period knob)", "sweep": "the latest clock sweep"}[choice["from"]])
    typer.echo(f"clock period for runs finishing from now on: {what}")
    typer.echo(f"  a run finishing now gets: {f'{period:g} ps from {src}' if period is not None else 'none (no TPI)'}")


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
                   f"{' '.join('#' + t for t in r.tags)}" + (f'  "{r.name}"' if r.name else ""))


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


@runs_app.command("rm")
def runs_rm(run_ids: List[str] = typer.Argument(..., help="Run ids (r_0012) or batch ids (g_0003: the whole batch)"),
            yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation")):
    """Remove runs and their files; a batch id (g_...) removes that whole batch. Runs that reused a
    removed run's result keep working."""
    from svkit.core.rundb import RunDB
    db = RunDB.for_project(_cfg().root)
    batches = [i for i in run_ids if i.startswith("g_")]
    run_ids = [i for i in run_ids if not i.startswith("g_")]
    for gid in batches:
        try:
            g = db.get_group(gid)
        except KeyError:
            typer.secho(f"no batch {gid}", fg="red", err=True)
            raise typer.Exit(EXIT_FAIL)
        n = db.count_runs(group_id=gid)
        typer.echo(f"  {gid}  {g.kind:<12} {g.name or ''} ({n} run{'s' if n != 1 else ''})")
    if batches and not run_ids:
        if not yes and not typer.confirm(f"Remove {len(batches)} batch{'es' if len(batches) != 1 else ''} with all their runs and files?"):
            raise typer.Exit(EXIT_FAIL)
    runs = []
    for rid in run_ids:
        try:
            runs.append(db.get_run(rid))
        except KeyError:
            typer.secho(f"no run {rid}", fg="red", err=True)
            raise typer.Exit(EXIT_FAIL)
    for r in runs:
        typer.echo(f"  {r.id}  {r.kind:<12} {r.name or r.test or r.experiment or ''}")
    if runs and not yes and not typer.confirm(
            f"Remove {len(runs)} run{'s' if len(runs) != 1 else ''}{' and the batches above' if batches else ''} and their files?"):
        raise typer.Exit(EXIT_FAIL)
    failed = False
    for gid in batches:
        try:
            removed, _ = db.delete_group(gid)
        except ValueError as e:
            typer.secho(str(e), fg="red", err=True)
            failed = True
            continue
        typer.echo(f"removed batch {gid} ({len(removed)} run{'s' if len(removed) != 1 else ''})")
    for r in runs:
        try:
            changed = db.delete_run(r.id)
        except ValueError as e:
            typer.secho(str(e), fg="red", err=True)
            failed = True
            continue
        extra = f" (its result now lives in {changed[0]}, which reused it)" if changed and not r.cached_from else ""
        typer.echo(f"removed {r.id}{extra}")
    if failed:
        raise typer.Exit(EXIT_FAIL)


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
    _notice_new_release()
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


def _notice_new_release() -> None:
    """Tell the user about a newer release without delaying startup (checked at most once a day)."""
    import threading

    def check():
        from svkit.update import newer_release_notice
        if msg := newer_release_notice():
            typer.secho(msg, fg="yellow", err=True)
    threading.Thread(target=check, daemon=True, name="svkit-update-check").start()


@app.command()
def update(check: bool = typer.Option(False, "--check", help="Only say whether a newer release exists"),
           to: Optional[str] = typer.Option(None, "--to", help="Install this release tag instead (e.g. v0.7.0)"),
           force: bool = typer.Option(False, "--force", help="Reinstall even when already up to date")):
    """Update svkit to the latest GitHub release (does nothing when already up to date)."""
    from pathlib import Path as _P
    from svkit import update as U
    info = U.install_info()
    repo = U.repo_slug(info)
    typer.echo(f"installed: {U.version_string(info)}")
    if info.kind == "local" and not check:
        typer.secho("svkit runs from a local checkout here: update it with git (git pull), not svkit update.",
                    fg="yellow")
        raise typer.Exit(EXIT_FAIL)
    try:
        if to:
            tag, notes = (to if to.startswith("v") else f"v{to}"), ""
        else:
            rel = U.latest_release(repo)
            if rel is None:
                typer.echo(f"{repo} has no published release yet; nothing to do.")
                return
            tag, notes = rel.tag, rel.notes
            typer.echo(f"latest:    {tag}  ({rel.published_at[:10] or 'date unknown'})  {rel.url}")
            if not force and not U.is_newer(tag, __version__):
                typer.secho(f"svkit is up to date ({__version__}).", fg="green")
                return
        if check:
            typer.secho(f"svkit {tag} is available: run `svkit update` to install it.", fg="yellow")
            return
        typer.echo(f"installing svkit {tag} from github.com/{repo} into {_P(sys.executable).parent.parent} ...")
        got = U.install_release(repo, tag, log=typer.echo)
    except U.UpdateError as e:
        typer.secho(f"update failed: {e}", fg="red", err=True)
        raise typer.Exit(EXIT_FAIL)
    typer.secho(f"svkit updated: {__version__} -> {got} ({tag}).", fg="green")
    if notes.strip():
        typer.echo("\nWhat's new:\n" + "\n".join("  " + ln for ln in notes.strip().splitlines()[:40]))
    if pids := U.running_servers(_P(sys.executable).parent):
        typer.secho(f"\n`svkit serve` is still running the old version here (pid {', '.join(map(str, pids))}): "
                    "stop it (Ctrl-C) and start it again, then reload the browser tab.", fg="yellow")
    else:
        typer.echo("Restart any running `svkit serve` (on any machine) and reload the browser tab.")


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
