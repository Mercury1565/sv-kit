"""Analysis routes: compare, sweeps, perf, divergence (docs/ARCHITECTURE.md §6)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from svkit.analysis import compare as C
from svkit.analysis import divergence as D
from svkit.analysis import perf as PF
from svkit.schemas import SweepResult


def make_router(st) -> APIRouter:
    r = APIRouter(prefix="/api")

    def _run(rid: str):
        try:
            return st.db.get_run(rid)
        except KeyError:
            raise HTTPException(404, f"no run {rid}")

    @r.get("/compare")
    def compare(ids: str = Query(..., description="comma-separated run ids; the first is the baseline")
                ) -> dict[str, Any]:
        rids = [x.strip() for x in ids.split(",") if x.strip()]
        if not rids:
            raise HTTPException(400, "ids required")
        return C.compare([_run(i) for i in rids]).model_dump(mode="json")

    @r.get("/sweeps/{gid}")
    def sweep(gid: str) -> dict[str, Any]:
        try:
            g = st.db.get_group(gid)
        except KeyError:
            raise HTTPException(404, f"no group {gid}")
        if g.kind != "sweep":
            raise HTTPException(404, f"group {gid} is a {g.kind!r} group, not a sweep")
        if not g.result:
            tol = float((g.params or {}).get("tol", 50))
            status = "running" if g.status == "running" else "failed"
            return SweepResult(group_id=gid, tol=tol, status=status).model_dump(mode="json")
        return SweepResult.model_validate(g.result).model_dump(mode="json")

    @r.get("/perf")
    def perf(group: Optional[str] = None) -> list[dict[str, Any]]:
        return [p.model_dump(mode="json") for p in PF.perf_table(st.cfg, st.db, group_id=group)]

    @r.get("/perf/history")
    def perf_history(test: str, limit: int = Query(500, ge=1, le=5000)) -> list[dict[str, Any]]:
        """One test's perf over every run of it, oldest first."""
        return PF.perf_history(st.cfg, st.db, test, limit=limit)

    @r.get("/runs/{rid}/divergence")
    def divergence(rid: str) -> dict[str, Any]:
        run = _run(rid)
        d = D.run_divergence(st.cfg, st.db, run)
        if d is None:
            raise HTTPException(404, f"run {rid} has no divergence")
        return d.model_dump(mode="json")

    return r
