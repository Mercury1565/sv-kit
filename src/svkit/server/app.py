"""FastAPI server (contract: docs/ARCHITECTURE.md §6).

Feature routers (waves, design, xtrace, perf, sweeps, compare) are mounted from
`svkit.server.routes_*` modules when present, so tracks can add them without
editing this file's core routes.
"""
from __future__ import annotations

import asyncio
import importlib
import mimetypes
import queue
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from svkit import __version__
from svkit.adapters import registry
from svkit.config import Config, ConfigError
from svkit.core import knobs as K
from svkit.core import project as P
from svkit.core.events import EventBus
from svkit.core.rundb import RunDB
from svkit.core.runner import Runner, run_summary, backends

STATIC = Path(__file__).parent / "static"
FEATURE_ROUTERS = ("routes_analysis", "routes_waves", "routes_design")


class State:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bus = EventBus()
        self.db = RunDB.for_project(cfg.root)
        self.runner = Runner(cfg, self.db, self.bus)
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="svkit", version=__version__)
    st = State(cfg)
    app.state.svkit = st
    api = APIRouter(prefix="/api")

    # ------------------------------------------------------------ project
    @api.get("/project")
    def project() -> dict[str, Any]:
        sha, dirty = P.git_info(cfg.root)
        try:
            tests = st.runner.tests()
        except ConfigError:
            tests = []
        adapters = {}
        for n in {*cfg.flows.sim, *cfg.flows.synth, *cfg.flows.sta}:
            try:
                adapters[n] = registry.get(cfg.sim_adapter(n) if n in cfg.flows.sim else n, cfg).available()
            except Exception:
                adapters[n] = False
        return {"name": cfg.project.name, "root": str(cfg.root), "top": cfg.project.top, "version": __version__,
                "git_sha": sha, "git_dirty": dirty,
                "flows": {"sim": list(cfg.flows.sim), "synth": list(cfg.flows.synth), "sta": list(cfg.flows.sta)},
                "knobs": list(cfg.knobs), "experiments": list(cfg.experiments), "tests": tests,
                "adapters": adapters, "backends": backends(cfg), "suites": st.runner.suites()}

    # ------------------------------------------------------------ runs
    def _run(rid: str):
        try:
            return st.db.get_run(rid)
        except KeyError:
            raise HTTPException(404, f"no run {rid}")

    @api.get("/runs")
    def runs(response: Response, kind: Optional[str] = None, group: Optional[str] = None,
             status: Optional[str] = None, test: Optional[str] = None, tag: Optional[str] = None,
             limit: int = Query(200, le=5000), offset: int = 0) -> list[dict[str, Any]]:
        flt = dict(kind=kind, group_id=group, status=status, test=test, tag=tag)
        response.headers["X-Total-Count"] = str(st.db.count_runs(**flt))
        return [run_summary(r) for r in st.db.list_runs(**flt, limit=limit, offset=offset)]

    @api.get("/runs/{rid}")
    def run(rid: str) -> dict[str, Any]:
        return _run(rid).to_dict()

    @api.get("/runs/{rid}/artifacts/{name:path}")
    def artifact(rid: str, name: str):
        r = _run(rid)
        if name not in r.artifacts:
            raise HTTPException(404, f"run {rid} has no artifact {name!r}")
        base = st.db.runs_dir / rid
        f = (base / r.artifacts[name]).resolve()
        if not f.is_relative_to(st.db.runs_dir.resolve()) or not f.is_file():
            raise HTTPException(404, "artifact missing")
        mt = mimetypes.guess_type(f.name)[0] or "text/plain"
        return FileResponse(f, media_type=mt)

    @api.delete("/runs/{rid}")
    def delete_run(rid: str) -> dict[str, Any]:
        """Remove a run (row, tags, files). Runs that reused its result keep working (see RunDB.delete_run)."""
        try:
            changed = st.db.delete_run(rid)
        except KeyError:
            raise HTTPException(404, f"no run {rid}")
        except ValueError as e:
            raise HTTPException(409, str(e))
        st.bus.publish({"type": "run.deleted", "run_id": rid})
        for cid in changed:
            st.bus.publish({"type": "run.updated", "run": run_summary(st.db.get_run(cid))})
        return {"deleted": rid, "changed": changed}

    @api.get("/runs/{rid}/reports.txt")
    def synth_reports(rid: str):
        """A synthesis run's summary and text reports as one downloadable .txt file."""
        from svkit.analysis.report_export import SYNTH_KINDS, download_name, reports_text
        r = _run(rid)
        if r.kind not in SYNTH_KINDS:
            raise HTTPException(400, f"run {rid} is a {r.kind} run; reports are exported for synthesis runs")
        text = reports_text(r, st.db.runs_dir / rid, st.db.runs_dir)
        return Response(text, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{download_name(r)}"'})

    @api.get("/runs/{rid}/log")
    def run_log(rid: str, tail: int = Query(500, le=100000)) -> dict[str, Any]:
        run = _run(rid)
        f = st.db.runs_dir / rid / "svkit.log"
        head: list[str] = []
        seen = {rid}
        while not f.exists() and run.cached_from and run.cached_from not in seen:   # cache hit: the original's log
            seen.add(run.cached_from)
            head = [f"[svkit] {rid} reused the result of {run.cached_from}; this is the log of {run.cached_from}"]
            run = st.db.get_run(run.cached_from)
            if run is None:
                break
            f = st.db.runs_dir / run.id / "svkit.log"
        lines = f.read_text(errors="replace").splitlines()[-tail:] if f.exists() else []
        return {"lines": head + lines}

    @api.post("/runs/{rid}/tags")
    def add_tag(rid: str, body: dict = Body(...)) -> dict[str, Any]:
        _run(rid)
        tag = str(body.get("tag", "")).strip()
        if not tag:
            raise HTTPException(400, "tag required")
        st.db.add_tag(rid, tag)
        return _run(rid).to_dict()

    @api.patch("/runs/{rid}")
    def rename_run(rid: str, body: dict = Body(...)) -> dict[str, Any]:
        """Body {"name": "..."}; an empty or null name removes it."""
        _run(rid)
        st.db.set_name(rid, _name_arg(body))
        r = _run(rid)
        st.bus.publish({"type": "run.updated", "run": run_summary(r)})
        return r.to_dict()

    @api.delete("/runs/{rid}/tags/{tag}")
    def del_tag(rid: str, tag: str) -> dict[str, Any]:
        _run(rid)
        st.db.remove_tag(rid, tag)
        return _run(rid).to_dict()

    # ------------------------------------------------------------ groups
    @api.get("/groups")
    def groups(kind: Optional[str] = None) -> list[dict[str, Any]]:
        return [g.to_dict() for g in st.db.list_groups(kind)]

    @api.get("/groups/{gid}")
    def group(gid: str) -> dict[str, Any]:
        try:
            g = st.db.get_group(gid).to_dict()
        except KeyError:
            raise HTTPException(404, f"no group {gid}")
        g["runs"] = [run_summary(r) for r in st.db.list_runs(group_id=gid, limit=5000)]
        return g

    @api.patch("/groups/{gid}")
    def rename_group(gid: str, body: dict = Body(...)) -> dict[str, Any]:
        """Body {"name": "..."}; an empty or null name removes it."""
        try:
            st.db.set_name(gid, _name_arg(body))
            g = st.db.get_group(gid)
        except KeyError:
            raise HTTPException(404, f"no group {gid}")
        st.bus.publish({"type": "group.updated", "group_id": gid, "status": g.status, "name": g.name})
        return g.to_dict()

    @api.delete("/groups/{gid}")
    def delete_group(gid: str) -> dict[str, Any]:
        """Remove a batch and all its runs (see RunDB.delete_group); 409 while any of its runs is busy."""
        try:
            removed, changed = st.db.delete_group(gid)
        except KeyError:
            raise HTTPException(404, f"no group {gid}")
        except ValueError as e:
            raise HTTPException(409, str(e))
        for rid in removed:
            st.bus.publish({"type": "run.deleted", "run_id": rid})
        for cid in changed:
            st.bus.publish({"type": "run.updated", "run": run_summary(st.db.get_run(cid))})
        st.bus.publish({"type": "group.deleted", "group_id": gid})
        return {"deleted": gid, "runs": removed, "changed": changed}

    def _name_arg(body: dict) -> Optional[str]:
        if "name" not in body:
            raise HTTPException(400, "name required (empty or null removes it)")
        v = body["name"]
        if v is not None and not isinstance(v, str):
            raise HTTPException(400, "name must be a string")
        return v

    # ------------------------------------------------------------ jobs
    def _job_target(kind: str, args: dict[str, Any]):
        r = st.runner
        if kind == "sim":
            return lambda: {"run_ids": [r.sim(args["test"], flow=args.get("flow"), name=args.get("name")).id]}
        if kind == "sim-all":
            tests = args.get("tests")
            if tests is not None and (not isinstance(tests, list) or not all(isinstance(x, str) for x in tests)):
                raise HTTPException(400, "tests must be a list of test names")
            def f():
                gid, runs = r.sim_all(flow=args.get("flow"), jobs=int(args.get("jobs", 4)), tests=tests or None,
                                      name=args.get("name"))
                return {"group_id": gid, "run_ids": [x.id for x in runs]}
            return f
        if kind == "synth":
            sets = K.parse_set(args.get("set", [])) if isinstance(args.get("set"), list) else args.get("set")
            return lambda: {"run_ids": [r.synth(flow=args.get("flow"), experiment=args.get("exp"), sets=sets,
                                                name=args.get("name")).id]}
        if kind == "matrix":
            def f():
                gid, runs = r.matrix(flow=args.get("flow"), jobs=int(args.get("jobs", 2)), name=args.get("name"))
                return {"group_id": gid, "run_ids": [x.id for x in runs]}
            return f
        if kind == "sweep":
            from svkit.analysis import sweep  # provided by the analysis track
            def f():
                res = sweep.sweep_clock(r, tol=float(args.get("tol", 50)), jobs=int(args.get("jobs", 2)),
                                        flow=args.get("flow"),
                                        start=float(args["start"]) if args.get("start") is not None else None)
                return {"group_id": res.group_id}
            return f
        raise HTTPException(400, f"unknown job kind {kind!r}")

    @api.post("/jobs")
    def submit(body: dict = Body(...)) -> dict[str, Any]:
        kind, args = body.get("kind"), body.get("args", {}) or {}
        target = _job_target(kind, args)
        jid = "j_" + uuid.uuid4().hex[:8]
        job = {"job_id": jid, "kind": kind, "args": args, "status": "running", "run_ids": [], "group_id": None,
               "error": None}
        with st.lock:
            st.jobs[jid] = job

        def work():
            try:
                job.update(target())
                job["status"] = "done"
            except Exception as e:
                job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"
            st.bus.publish({"type": "job.updated", "job": dict(job)})

        threading.Thread(target=work, daemon=True, name=f"job-{jid}").start()
        st.bus.publish({"type": "job.updated", "job": dict(job)})
        return {"job_id": jid}

    @api.get("/jobs")
    def jobs() -> list[dict[str, Any]]:
        with st.lock:
            return [dict(j) for j in st.jobs.values()]

    # ------------------------------------------------------------ source
    @api.get("/source")
    def source(file: str, line: int = 1, context: int = 20) -> dict[str, Any]:
        f = Path(file)
        f = (f if f.is_absolute() else cfg.root / f).resolve()
        if not f.is_relative_to(cfg.root.resolve()) or not f.is_file():
            raise HTTPException(404, "file not in project")
        lines = f.read_text(errors="replace").splitlines()
        start = max(1, line - context)
        return {"file": str(f.relative_to(cfg.root.resolve())), "start": start,
                "lines": lines[start - 1:line + context]}

    app.include_router(api)

    # feature routers from tracks
    for mod in FEATURE_ROUTERS:
        try:
            m = importlib.import_module(f"svkit.server.{mod}")
        except ModuleNotFoundError as e:
            if e.name == f"svkit.server.{mod}":
                continue
            raise
        app.include_router(m.make_router(st))

    # ------------------------------------------------------------ websocket
    @app.websocket("/ws/events")
    async def events(ws: WebSocket):
        await ws.accept()
        q = st.bus.subscribe()
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    ev = await loop.run_in_executor(None, q.get, True, 1.0)
                except queue.Empty:
                    continue
                await ws.send_json(ev)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            st.bus.unsubscribe(q)

    # ------------------------------------------------------------ static SPA
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (STATIC / path).resolve()
        if path and f.is_file() and f.is_relative_to(STATIC.resolve()):
            return FileResponse(f)
        index = STATIC / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"detail": "web UI not built (run `npm run build` in web/)"}, status_code=404)

    return app
