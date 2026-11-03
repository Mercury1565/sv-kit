"""Job runner: executes sim/synth Runs through adapters, with caching, isolated
work dirs, license-seat caps and live events. Used by the CLI and the server.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from svkit.adapters import registry
from svkit.adapters.base import RunContext, SimAdapter, SynthAdapter
from svkit.config import Config, ConfigError
from svkit.core import knobs as K
from svkit.core import project as P
from svkit.core.events import NULL_BUS, EventBus
from svkit.core.rundb import Run, RunDB

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

    def _cached(self, key: str, kind: str, adapter: str, **create) -> Optional[Run]:
        hit = self.db.find_cached(key)
        if hit is None:
            return None
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

    # ------------------------------------------------------------- sim
    def sim(self, test: str, *, flow: str | None = None, group_id: str | None = None,
            sets: dict[str, Any] | None = None, use_cache: bool = True) -> Run:
        name, fl = self.cfg.sim_flow(flow)
        adapter = registry.get(name, self.cfg)
        assert isinstance(adapter, SimAdapter)
        values = {k: v for k, v in (sets or {}).items()}
        plan = K.plan(self.cfg, values, stage="sim")
        sha, dirty = P.git_info(self.cfg.root)
        extra = P.flow_files(self.cfg)
        if fl.tests.glob:   # the per-test input (program) and its golden affect the result
            extra += [p for p in self.cfg.root.glob(fl.tests.glob) if p.stem == test]
        if fl.golden:
            extra.append(self.cfg.root / P.tmpl(fl.golden.expected, self.cfg, test=test))
        src = P.source_hash(self.cfg, extra)
        key = P.cache_key("sim", name, src, values, test)
        common = dict(test=test, knobs=values, group_id=group_id, git_sha=sha, git_dirty=dirty, source_hash=src)
        if use_cache and (hit := self._cached(key, "sim", name, **common)):
            return hit
        run = self.db.create_run("sim", name, cache_key=key, **common)
        self.bus.publish({"type": "run.created", "run": run_summary(run)})
        t0 = time.time()
        workdir = self.cfg.root
        if fl.isolate or plan.files:
            workdir = P.make_workdir(self.cfg, self.work_root / run.id)
            K.apply_files(plan, workdir)
        ctx = self._ctx(run, name, workdir, plan)
        ctx.env.update(fl.env)
        self.db.update_run(run.id, status="running", started_at=t0)
        self._emit_status(run.id, "running")
        try:
            res = adapter.run(ctx, fl, test)
            status = "passed" if res.passed else ("failed" if res.passed is False else "done")
            return self._finish(run, ctx, t0, status, res.model_dump(mode="json"), 0, res.reason)
        except Exception as e:  # adapter crash -> error run, never kills the caller
            ctx.log(traceback.format_exc())
            return self._finish(run, ctx, t0, "error", None, None, f"{type(e).__name__}: {e}")
        finally:
            if workdir != self.cfg.root:
                shutil.rmtree(workdir, ignore_errors=True)

    def tests(self, flow: str | None = None) -> list[str]:
        _, fl = self.cfg.sim_flow(flow)
        names = list(fl.tests.names)
        if fl.tests.glob:
            names += [p.stem for p in sorted(self.cfg.root.glob(fl.tests.glob))]
        return list(dict.fromkeys(names))

    def sim_all(self, *, flow: str | None = None, jobs: int = 4, tests: list[str] | None = None,
                use_cache: bool = True) -> tuple[str, list[Run]]:
        tests = tests or self.tests(flow)
        if not tests:
            raise ConfigError("no tests found (set [flows.sim.<x>].tests)")
        name, fl = self.cfg.sim_flow(flow)
        if not fl.parallel:
            jobs = 1
        g = self.db.create_group("sim-all", {"flow": flow, "tests": tests})
        if fl.build_cmd:
            from svkit.adapters.base import RunContext
            ctx = RunContext(cfg=self.cfg, root=self.cfg.root, workdir=self.cfg.root,
                             run_dir=self.db.state_dir, run_id=g.id, flow_name=name,
                             env={**os.environ, **fl.env})
            rc = ctx.sh(P.tmpl(fl.build_cmd, self.cfg), cwd=self.cfg.root / fl.cwd,
                        log_file=self.db.state_dir / f"build-{g.id}.log")
            if rc != 0:
                self.db.update_group(g.id, status="failed", result={"error": f"build_cmd exit {rc}"})
                raise ConfigError(f"build_cmd failed (exit {rc}); see .svkit/build-{g.id}.log")
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            runs = list(ex.map(lambda t: self.sim(t, flow=flow, group_id=g.id, use_cache=use_cache), tests))
        status = "done" if all(r.status in ("passed", "done") for r in runs) else "failed"
        self.db.update_group(g.id, status=status,
                             result={"passed": sum(r.status == "passed" for r in runs), "total": len(runs)})
        self.bus.publish({"type": "group.updated", "group_id": g.id, "status": status})
        return g.id, runs

    # ------------------------------------------------------------- synth
    def synth(self, *, flow: str | None = None, experiment: str | None = None,
              sets: dict[str, Any] | None = None, group_id: str | None = None,
              kind: str = "synth", use_cache: bool = True) -> Run:
        name, fl = self.cfg.synth_flow(flow)
        adapter = registry.get(name, self.cfg)
        assert isinstance(adapter, SynthAdapter)
        values = K.resolve(self.cfg, experiment=experiment, sets=sets)
        plan = K.plan(self.cfg, values, stage="synth", append_before=fl.append_before)
        sha, dirty = P.git_info(self.cfg.root)
        src = P.source_hash(self.cfg, P.flow_files(self.cfg, fl.script))
        key = P.cache_key("synth", name, src, values)
        common = dict(knobs=values, experiment=experiment, group_id=group_id, git_sha=sha, git_dirty=dirty,
                      source_hash=src)
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
            ctx.env.update(fl.env)
            t0 = time.time()
            self.db.update_run(run.id, status="running", started_at=t0)
            self._emit_status(run.id, "running")
            res = adapter.run(ctx, fl)
            return self._finish(run, ctx, t0, "done", res.model_dump(mode="json"), 0, None)
        except Exception as e:
            if ctx:
                ctx.log(traceback.format_exc())
            return self._finish(run, ctx, t0, "error", None, None, f"{type(e).__name__}: {e}")
        finally:
            if sem:
                sem.release()
            shutil.rmtree(workdir, ignore_errors=True)

    def matrix(self, *, flow: str | None = None, jobs: int = 2, experiments: list[str] | None = None,
               use_cache: bool = True) -> tuple[str, list[Run]]:
        exps = experiments or list(self.cfg.experiments)
        if not exps:
            raise ConfigError("no [experiments] defined in svkit.toml")
        g = self.db.create_group("matrix", {"flow": flow, "experiments": exps})
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            runs = list(ex.map(lambda e: self.synth(flow=flow, experiment=e, group_id=g.id, use_cache=use_cache),
                               exps))
        status = "done" if all(r.status == "done" for r in runs) else "failed"
        self.db.update_group(g.id, status=status, result={"run_ids": [r.id for r in runs]})
        self.bus.publish({"type": "group.updated", "group_id": g.id, "status": status})
        return g.id, runs
