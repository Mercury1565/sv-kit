"""Waveform routes: /api/waves/{run_id}/meta|scope|search|data|next|decode (docs/ARCHITECTURE.md §6).

Values are binary strings over 01xz (real: decimal string). Downsampled data uses
the value "*" for "many transitions here" (see svkit.adapters.waves.wellen).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from svkit.adapters.waves.locate import wave_path
from svkit.adapters.waves.wellen import UnknownSignal, WaveError, WaveFile, open_wave

MAX_CHANGES_CAP = 100_000


def _enum_from_wave(v) -> Optional[dict[str, Any]]:
    """FST files (e.g. Verilator) can carry enum tables; use them when the DesignGraph can't."""
    et = getattr(v, "enum_type", None)
    if not et:
        return None
    try:
        _name, mapping = et          # pywellen: (type name, [(bit string, label), ...])
        enum = {}
        for k, label in (mapping.items() if isinstance(mapping, dict) else mapping):
            enum[str(k)] = str(label)
        return {"kind": "enum", "enum": enum} if enum else None
    except Exception:
        return None


def make_router(st) -> APIRouter:
    r = APIRouter(prefix="/api/waves")

    def _wave(rid: str) -> WaveFile:
        try:
            run = st.db.get_run(rid)
        except KeyError:
            raise HTTPException(404, f"no run {rid}")
        p = wave_path(st.db, run)
        if p is None:
            raise HTTPException(404, f"run {rid} has no waveform")
        try:
            return open_wave(p)
        except WaveError as e:
            raise HTTPException(422, str(e))

    @r.get("/{rid}/meta")
    def meta(rid: str) -> dict[str, Any]:
        return _wave(rid).meta()

    @r.get("/{rid}/scope")
    def scope(rid: str, path: str = "") -> dict[str, Any]:
        try:
            return _wave(rid).scope(path)
        except UnknownSignal:
            raise HTTPException(404, f"no scope {path!r}")

    @r.get("/{rid}/search")
    def search(rid: str, q: str = "", limit: int = Query(100, ge=1, le=5000)) -> list[dict[str, Any]]:
        return _wave(rid).search(q, limit)

    @r.post("/{rid}/data")
    def data(rid: str, body: dict = Body(...)) -> dict[str, Any]:
        sigs = body.get("signals") or []
        if not isinstance(sigs, list) or not all(isinstance(s, str) for s in sigs):
            raise HTTPException(400, "signals must be a list of hierarchical names")
        try:
            t0, t1 = int(body["t0"]), int(body["t1"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "t0 and t1 (integer ticks) are required")
        mc = min(int(body.get("max_changes") or 2000), MAX_CHANGES_CAP)
        try:
            return _wave(rid).data(sigs, t0, t1, mc)
        except UnknownSignal as e:
            raise HTTPException(404, f"unknown signal(s): {e.args[0]}")

    @r.get("/{rid}/next")
    def next_(rid: str, signal: str, t: int, what: Literal["change", "x"] = "change",
              dir: int = 1) -> dict[str, Any]:  # noqa: A002 (API name)
        try:
            return {"t": _wave(rid).next(signal, t, what, 1 if dir >= 0 else -1)}
        except UnknownSignal:
            raise HTTPException(404, f"unknown signal {signal!r}")

    @r.get("/{rid}/decode")
    def decode(rid: str, signal: str) -> dict[str, Any]:
        wf = _wave(rid)
        try:
            v = wf.var(signal)
        except UnknownSignal:
            raise HTTPException(404, f"unknown signal {signal!r}")
        try:
            from svkit.design.graph import lookup_type  # Track 4 (DesignGraph); optional
        except ImportError:
            lookup_type = None
        if lookup_type is not None:
            try:
                t = lookup_type(st.cfg, signal)
            except Exception:
                t = None
            if t:
                return t
        return _enum_from_wave(v) or {"kind": "plain"}

    return r
