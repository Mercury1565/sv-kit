"""Job runner: executes sim/synth Runs through adapters, with caching, isolated
work dirs, license-seat caps and live events. Used by the CLI and the server.
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from svkit import compat
from svkit.adapters import registry
from svkit.adapters.base import RunContext, SimAdapter, SynthAdapter
from svkit.config import Config, ConfigError
from svkit.core import knobs as K
from svkit.core import project as P
from svkit.core.events import NULL_BUS, EventBus
from svkit.core.rundb import Run, RunDB
from svkit.schemas import Msg, Severity

_SEATS: dict[str, threading.BoundedSemaphore] = {}
_SEATS_LOCK = threading.Lock()


def seats(adapter: str, n: int) -> threading.BoundedSemaphore | None:
    """Process-wide semaphore capping concurrent runs of one adapter (license seats)."""
    if n <= 0:
        return None
    with _SEATS_LOCK:
        s = _SEATS.get(adapter)
        if s is None:
            s = _SEATS[adapter] = threading.BoundedSemaphore(n)
        return s


def missing_tools(adapter) -> list[str]:
    return [adapter.cfg.tool(t) for t in adapter.tools if not shutil.which(adapter.cfg.tool(t))]


def _which(cfg: Config, name: str) -> Optional[str]:
    return shutil.which(cfg.tool(name))


def tools_hint(adapter, flow_name: str) -> str:
    miss = missing_tools(adapter)
    if not miss:
        return ""
    return (f"flow '{flow_name}' needs {', '.join(miss)}, which is not on PATH on this host. Load the tool's "
            f"environment, set [tools] in svkit.toml, or pick another flow (--flow).")


def require_tools(adapter, flow_name: str, mode: str) -> None:
    """Builtin flows invoke the tool themselves: fail fast when it is missing. Wrap flows run the
    user's command, which may call the tool by absolute path, so only hint on failure."""
    if mode == "builtin" and (h := tools_hint(adapter, flow_name)):
        raise ConfigError(h)


@dataclass
class Resolved:
    """The flow a job actually runs on this host (after tool fallback)."""
    name: str                 # flow key under [flows.<kind>]
    flow: Any                 # SimFlow | SynthFlow
    adapter: Any              # adapter instance that runs it
    label: str                # adapter name recorded on the Run (and in the cache key)
    note: Optional[str] = None
    shims: list[str] = field(default_factory=list)   # tools replaced by svkit.compat stand-ins
    cfg: Optional[Config] = None                      # patched config (implicit fallback flows)


def _available(cfg: Config, name: str) -> bool:
    try:
        return not missing_tools(registry.get(cfg.sim_adapter(name) if name in cfg.flows.sim else name, cfg))
    except Exception:
        return False


def sim_adapter(cfg: Config, name: str):
    """Adapter instance for the sim flow `name` (its `adapter` field, else the flow key)."""
    return registry.get(cfg.sim_adapter(name), cfg)


def generic_liberty() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "svkit_tiny.lib"


def resolve_sim(cfg: Config, flow: str | None) -> Resolved:
    name, fl = cfg.sim_flow(flow)
    adapter = sim_adapter(cfg, name)
    aname = cfg.sim_adapter(name)
    if not missing_tools(adapter) or not cfg.flows.fallback:
        return Resolved(name, fl, adapter, aname)
    if flow is None or fl.suite_id != "system":
        # another configured flow of the same suite (same tests) that can run here
        for other, ofl in cfg.flows.sim.items():
            if other != name and ofl.suite_id == fl.suite_id and _available(cfg, other):
                return Resolved(other, ofl, sim_adapter(cfg, other), cfg.sim_adapter(other),
                                f"{', '.join(missing_tools(adapter))} not found on this host: "
                                f"using the '{other}' sim flow instead of '{name}'")
    if "vcs" in adapter.tools and _which(cfg, "verilator"):
        if fl.mode == "wrap":
            return Resolved(name, fl, adapter, "verilator",
                            "vcs not found on this host: running your build with Verilator standing in "
                            "for vcs (2-state: X/Z read as 0)", shims=["vcs"])
        return Resolved(name, fl, registry.get("verilator", cfg), "verilator",
                        "vcs not found on this host: compiling the sources with Verilator instead")
    return Resolved(name, fl, adapter, aname)


def resolve_synth(cfg: Config, flow: str | None) -> Resolved:
    name, fl = cfg.synth_flow(flow)
    adapter = registry.get(name, cfg)
    if not missing_tools(adapter) or not cfg.flows.fallback:
        return Resolved(name, fl, adapter, name)
    miss = ", ".join(missing_tools(adapter))
    if flow is None:
        for other, ofl in cfg.flows.synth.items():
            if other != name and _available(cfg, other):
                return Resolved(other, ofl, registry.get(other, cfg), other,
                                f"{miss} not found on this host: using the '{other}' synth flow instead of '{name}'")
    if name != "yosys" and _available(cfg, "yosys"):
        from svkit.config import StaFlow, SynthFlow
        c2 = cfg.model_copy(deep=True)
        has_sta = bool(_which(cfg, "sta"))
        note = f"{miss} not found on this host: synthesizing with Yosys" + (" + OpenSTA" if has_sta else "")
        if not (c2.library.liberty or c2.library.corners):
            c2.library.liberty = [str(generic_liberty())]
            note += (" on svkit's generic demo library (invented numbers: compare runs with each other, "
                     "not with your PDK)")
        if has_sta:
            c2.flows.sta.setdefault("opensta", StaFlow())
        else:
            note += "; OpenSTA (sta) not found either, so area and cells only, no timing or power"
        yfl = SynthFlow(mode="builtin", sta="opensta" if has_sta else None)
        return Resolved("yosys", yfl, registry.get("yosys", c2), "yosys", note, cfg=c2)
    return Resolved(name, fl, adapter, name)


def backends(cfg: Config) -> dict[str, Any]:
    """What `sim` / `synth` without --flow will actually run on this host (for the UI and CLI)."""
    out: dict[str, Any] = {}
    for kind, fn in (("sim", resolve_sim), ("synth", resolve_synth)):
        try:
            rs = fn(cfg, None)
            conf, _ = (cfg.sim_flow if kind == "sim" else cfg.synth_flow)(None)
            missing = missing_tools(sim_adapter(cfg, conf) if kind == "sim" else registry.get(conf, cfg)) if rs.note else []
            out[kind] = {"flow": rs.name, "configured": conf, "missing": missing, "runs_on": rs.label, "note": rs.note,
                         "available": not missing_tools(rs.adapter) or bool(rs.shims)}
        except ConfigError:
            pass
    return out


def _tail(path: Path, n: int = 8) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def _error_text(e: BaseException) -> str:
    """One-line message for an error run. Tool failures (RuntimeError / ConfigError raised by
    adapters) already read as sentences; anything else is an svkit bug, so keep its type."""
    return str(e) if isinstance(e, (RuntimeError, ConfigError)) and str(e) else f"{type(e).__name__}: {e}"


def run_summary(r: Run) -> dict[str, Any]:
    d = r.to_dict()
    d.pop("metrics", None)
    return d


class Runner:
    def __init__(self, cfg: Config, db: RunDB | None = None, bus: EventBus | None = None):
        self.cfg = cfg
        self.db = db or RunDB.for_project(cfg.root)
        self.bus = bus or NULL_BUS
        self.work_root = self.db.state_dir / "work"
        self.tool_notes = compat.pin_tools(cfg)
        self.db.reap_orphans()   # runs a previous (exited) svkit process left queued/running

    # ------------------------------------------------------------- helpers
    def _emit_status(self, run_id: str, status: str, message: str | None = None) -> None:
        self.bus.publish({"type": "run.status", "run_id": run_id, "status": status, "message": message})

    def _ctx(self, run: Run, flow_name: str, workdir: Path, plan) -> RunContext:
        env = dict(os.environ)
        env.update(plan.env)
        logf = self.db.run_dir(run.id) / "svkit.log"

        def log(line: str, _rid=run.id, _f=logf) -> None:
            with open(_f, "a") as fh:
                fh.write(line + "\n")
            self.bus.publish({"type": "run.log", "run_id": _rid, "line": line})

        return RunContext(cfg=self.cfg, root=self.cfg.root, workdir=workdir, run_dir=self.db.run_dir(run.id),
                          run_id=run.id, flow_name=flow_name, plan=plan, env=env, log=log,
                          cache_dir=self.db.state_dir / "build")

    def _compat_env(self, rs: Resolved, cwd: Path, log: Callable[[str], None] | None = None) -> dict[str, str]:
        """Env for running a wrap command on this host: tool stand-ins on PATH, make SHELL fix."""
        shim_root = self.db.state_dir / "shims"
        env, note = compat.make_env(cwd, shim_root)
        if note and log:
            log(f"[svkit] {note}")
        if rs.shims:
            d = compat.tool_shims(shim_root, rs.shims)
            env["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"
            env["SVKIT_SHIM_CACHE"] = str(self.db.state_dir / "build" / "vcs-shim")
            env["SVKIT_VCS_SHIM"] = "1"
            env["SVKIT_SHIM_VERILATOR"] = _which(self.cfg, "verilator") or "verilator"
        return env

    def _cached(self, key: str, kind: str, adapter: str, **create) -> Optional[Run]:
        hit = self.db.find_cached(key)
        if hit is None:
            return None
        while hit.cached_from and (orig := self.db.get_run(hit.cached_from)) is not None:
            hit = orig                       # always point at the run that really executed
        r = self.db.create_run(kind, adapter, cache_key=key, status=hit.status, **create)
        # artifacts are referenced from the original run's directory
        arts = {n: f"../{hit.id}/{p}" for n, p in hit.artifacts.items()}
        self.db.update_run(r.id, metrics=hit.metrics, artifacts=arts, cached_from=hit.id, exit_code=hit.exit_code,
                           started_at=time.time(), finished_at=time.time(), duration_s=0.0,
                           message=f"cached from {hit.id}")
        r = self.db.get_run(r.id)
        self.bus.publish({"type": "run.created", "run": run_summary(r)})
        return r

    def _finish(self, run: Run, ctx: RunContext, t0: float, status: str, metrics: dict | None,
                exit_code: int | None, message: str | None) -> Run:
        self.db.update_run(run.id, status=status, metrics=metrics, artifacts=ctx.artifacts if ctx else {},
                           finished_at=time.time(), duration_s=time.time() - t0, exit_code=exit_code,
                           message=message)
        self._emit_status(run.id, status, message)
        return self.db.get_run(run.id)

    @contextmanager
    def _test_lock(self, flow: str, test: str, ctx: RunContext, poll_s: float = 0.2) -> Iterator[None]:
        """Serialize in-place runs of one test, across threads and svkit processes on this host:
        concurrent `make t.out` in one tree delete each other's outputs mid-run."""
        d = self.db.state_dir / "locks"
        d.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.-]", "_", test)
        fh = open(d / f"sim-{flow}-{safe}.lock", "a+")
        try:
            waited = False
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if not waited:
                        ctx.log(f"[svkit] waiting: another run of {test} is using the same output files")
                        waited = True
                    if ctx.cancel.wait(poll_s):
                        raise RuntimeError("cancelled while waiting for another run of the same test")
            yield
        finally:
            fh.close()

    # ------------------------------------------------------------- sim
    def sim(self, test: str, *, flow: str | None = None, group_id: str | None = None,
            sets: dict[str, Any] | None = None, use_cache: bool = True, name: str | None = None) -> Run:
        run_name = name
        rs = resolve_sim(self.cfg, flow)
        name, fl, adapter, label = rs.name, rs.flow, rs.adapter, rs.label
        assert isinstance(adapter, SimAdapter)
        require_tools(adapter, name, fl.mode)
        values = {k: v for k, v in (sets or {}).items()}
        plan = K.plan(self.cfg, values, stage="sim")
        sha, dirty = P.git_info(self.cfg.root)
        extra = P.flow_files(self.cfg)
        if fl.tests.glob:   # the per-test input (program) and its golden affect the result
            extra += [p for p in self.cfg.root.glob(fl.tests.glob) if p.stem == test]
        if fl.golden:
            extra.append(self.cfg.root / P.tmpl(fl.golden.expected, self.cfg, test=test))
        if fl.testbench:    # unit test: its own testbench file
            from svkit.core import unit as U
            extra.append(U.testbench_file(self.cfg, fl, test))
        extra += P.resolve_sources(self.cfg, fl.sources)
        src = P.source_hash(self.cfg, extra)
        key = P.cache_key("sim", label if label == name else f"{name}~{label}", src, values, test)
        common = dict(test=test, knobs=values, group_id=group_id, git_sha=sha, git_dirty=dirty, source_hash=src,
                      flow=name, name=run_name)
        if use_cache and (hit := self._cached(key, "sim", label, **common)):
            return hit
        run = self.db.create_run("sim", label, cache_key=key, **common)
        self.bus.publish({"type": "run.created", "run": run_summary(run)})
        t0 = time.time()
        workdir = self.cfg.root
        if fl.isolate or plan.files:
            workdir = P.make_workdir(self.cfg, self.work_root / run.id)
            K.apply_files(plan, workdir)
        ctx = self._ctx(run, name, workdir, plan)
        if rs.note:
            ctx.log(f"[svkit] {rs.note}")
        if fl.mode == "wrap":
            ctx.env.update(self._compat_env(rs, workdir / fl.cwd, ctx.log))
        ctx.env.update(fl.env)
        self.db.update_run(run.id, status="running", started_at=t0)
        self._emit_status(run.id, "running", rs.note)
        try:
            if workdir == self.cfg.root:      # in place: same-test runs share output files
                with self._test_lock(name, test, ctx):
                    res = adapter.run(ctx, fl, test)
            else:
                res = adapter.run(ctx, fl, test)
            status = "passed" if res.passed else ("failed" if res.passed is False else "done")
            from svkit.core import wavestore
            if warn := wavestore.archive_run(self.cfg, ctx.run_dir, res, ctx.artifacts, ctx.log):
                res.warnings.append(Msg(severity=Severity.info, code="WAVES", text=warn))
            if rs.note:
                res.warnings.insert(0, Msg(severity=Severity.info, code="FALLBACK", text=rs.note))
            # exit_code is the tool's: unknown here (the reason carries it), not svkit's own 0
            return self._finish(run, ctx, t0, status, res.model_dump(mode="json"), None, res.reason)
        except Exception as e:  # adapter crash -> error run, never kills the caller
            ctx.log(traceback.format_exc())
            return self._finish(run, ctx, t0, "error", None, None, _error_text(e))
        finally:
            if workdir != self.cfg.root:
                shutil.rmtree(workdir, ignore_errors=True)

    def tests(self, flow: str | None = None) -> list[str]:
        from svkit.core import unit as U
        _, fl = self.cfg.sim_flow(flow)
        return U.discover(self.cfg, fl)

    def suites(self) -> list[dict[str, Any]]:
        """Test suites for the dashboard: each runs on its first flow (with fallback)."""
        out = []
        for sid, flows in self.cfg.suites().items():
            fl = self.cfg.flows.sim[flows[0]]
            try:
                tests = self.tests(flows[0])
            except (ConfigError, OSError):
                tests = []
            label = {"system": "System tests", "unit": "Unit tests"}.get(sid, sid)
            out.append({"id": sid, "label": label, "flow": flows[0], "flows": flows, "tests": tests,
                        "unit": bool(fl.testbench), "golden": fl.golden is not None,
                        "default": flows[0] == self.cfg.sim_flow(None)[0]})
        return out

    def sim_all(self, *, flow: str | None = None, jobs: int = 4, tests: list[str] | None = None,
                use_cache: bool = True, name: str | None = None) -> tuple[str, list[Run]]:
        """Run a batch: every test of the flow, or only `tests` (in that order). `name` labels the batch."""
        batch_name = name
        known = self.tests(flow)
        if tests:
            tests = list(dict.fromkeys(tests))
            unknown = [x for x in tests if x not in known]
            if unknown:
                raise ConfigError(f"unknown test(s): {', '.join(unknown)}"
                                  + (f" (available: {', '.join(known[:20])}{' …' if len(known) > 20 else ''})"
                                     if known else ""))
        else:
            tests = known
        if not tests:
            raise ConfigError("no tests found (set [flows.sim.<x>].tests)")
        rs = resolve_sim(self.cfg, flow)
        name, fl, adapter = rs.name, rs.flow, rs.adapter
        require_tools(adapter, name, fl.mode)
        flow = name           # every test runs the flow resolved here
        if not fl.parallel:
            jobs = 1
        g = self.db.create_group("sim-all", {"flow": flow, "tests": tests, "backend": rs.label, "note": rs.note,
                                             "subset": len(tests) < len(known)}, name=batch_name)
        self.bus.publish({"type": "group.updated", "group_id": g.id, "status": g.status})
        if fl.build_cmd and fl.mode == "wrap":
            from svkit.adapters.base import RunContext
            ctx = RunContext(cfg=self.cfg, root=self.cfg.root, workdir=self.cfg.root,
                             run_dir=self.db.state_dir, run_id=g.id, flow_name=name,
                             env={**os.environ, **self._compat_env(rs, self.cfg.root / fl.cwd), **fl.env})
            blog = self.db.state_dir / f"build-{g.id}.log"
            for line in filter(None, [rs.note, compat.make_env(self.cfg.root / fl.cwd, self.db.state_dir / "shims")[1]]):
                with open(blog, "a") as fh:
                    fh.write(f"[svkit] {line}\n")
            rc = ctx.sh(P.tmpl(fl.build_cmd, self.cfg), cwd=self.cfg.root / fl.cwd, log_file=blog)
            if rc != 0:
                tail = _tail(blog)
                self.db.update_group(g.id, status="failed", result={"error": f"build_cmd exit {rc}", "log_tail": tail})
                hint = "" if rs.shims else tools_hint(adapter, name)
                raise ConfigError(f"build_cmd `{fl.build_cmd}` failed (exit {rc}); see .svkit/build-{g.id}.log"
                                  + (f"\n{hint}" if hint else "") + (f"\n{tail}" if tail else ""))
        one = lambda t: self.sim(t, flow=flow, group_id=g.id, use_cache=use_cache)  # noqa: E731
        runs = []
        if jobs > 1 and fl.mode == "wrap" and len(tests) > 1:
            # warm-up: the first test runs alone, so prerequisites the user's build creates on
            # demand and shares between tests (tools, generated files) exist before tests fan out
            runs.append(one(tests[0]))
            tests = tests[1:]
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            runs += list(ex.map(one, tests))
        status = "done" if all(r.status in ("passed", "done") for r in runs) else "failed"
        self.db.update_group(g.id, status=status,
                             result={"passed": sum(r.status == "passed" for r in runs), "total": len(runs)})
        self.bus.publish({"type": "group.updated", "group_id": g.id, "status": status})
        return g.id, runs

    # ------------------------------------------------------------- synth
    def synth(self, *, flow: str | None = None, experiment: str | None = None,
              sets: dict[str, Any] | None = None, group_id: str | None = None,
              kind: str = "synth", use_cache: bool = True, name: str | None = None) -> Run:
        rs = resolve_synth(self.cfg, flow)
        name, fl, adapter = rs.name, rs.flow, rs.adapter
        rcfg = rs.cfg or self.cfg
        assert isinstance(adapter, SynthAdapter)
        require_tools(adapter, name, fl.mode)
        values = K.resolve(self.cfg, experiment=experiment, sets=sets)
        plan = K.plan(self.cfg, values, stage="synth", append_before=fl.append_before)
        sha, dirty = P.git_info(self.cfg.root)
        src = P.source_hash(self.cfg, P.flow_files(self.cfg, fl.script))
        key = P.cache_key("synth", name if rs.cfg is None else f"{name}~{rcfg.library.liberty}", src, values)
        common = dict(knobs=values, experiment=experiment, group_id=group_id, git_sha=sha, git_dirty=dirty,
                      source_hash=src, name=name)
        if use_cache and (hit := self._cached(key, kind, name, **common)):
            return hit
        run = self.db.create_run(kind, name, cache_key=key, **common)
        self.bus.publish({"type": "run.created", "run": run_summary(run)})
        sem = seats(name, fl.license_seats)
        ctx = None
        t0 = time.time()
        if sem:
            self._emit_status(run.id, "queued", "waiting for a license seat")
            sem.acquire()
        workdir = self.work_root / run.id
        try:
            # synthesis ALWAYS runs in an isolated work dir (knobs are applied there)
            P.make_workdir(self.cfg, workdir)
            K.apply_files(plan, workdir)
            if fl.script:
                K.prepare_script(plan, fl.script, workdir)
            ctx = self._ctx(run, name, workdir, plan)
            ctx.cfg = rcfg
            if rs.note:
                ctx.log(f"[svkit] {rs.note}")
            if fl.mode == "wrap":
                ctx.env.update(self._compat_env(rs, workdir, ctx.log))
            ctx.env.update(fl.env)
            t0 = time.time()
            self.db.update_run(run.id, status="running", started_at=t0)
            self._emit_status(run.id, "running")
            res = adapter.run(ctx, fl)
            if rs.note:
                res.extra["fallback"] = rs.note
                res.warnings.insert(0, Msg(severity=Severity.info, code="FALLBACK", text=rs.note))
            return self._finish(run, ctx, t0, "done", res.model_dump(mode="json"), 0, None)
        except Exception as e:
            if ctx:
                ctx.log(traceback.format_exc())
            hint = "" if rs.shims or fl.mode != "wrap" else tools_hint(adapter, name)
            return self._finish(run, ctx, t0, "error", None, None,
                                _error_text(e) + (f"\n{hint}" if hint else ""))
        finally:
            if sem:
                sem.release()
            shutil.rmtree(workdir, ignore_errors=True)

    def matrix(self, *, flow: str | None = None, jobs: int = 2, experiments: list[str] | None = None,
               use_cache: bool = True, name: str | None = None) -> tuple[str, list[Run]]:
        exps = experiments or list(self.cfg.experiments)
        if not exps:
            raise ConfigError("no [experiments] defined in svkit.toml")
        g = self.db.create_group("matrix", {"flow": flow, "experiments": exps}, name=name)
        self.bus.publish({"type": "group.updated", "group_id": g.id, "status": g.status})
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            runs = list(ex.map(lambda e: self.synth(flow=flow, experiment=e, group_id=g.id, use_cache=use_cache),
                               exps))
        status = "done" if all(r.status == "done" for r in runs) else "failed"
        self.db.update_group(g.id, status=status, result={"run_ids": [r.id for r in runs]})
        self.bus.publish({"type": "group.updated", "group_id": g.id, "status": status})
        return g.id, runs
