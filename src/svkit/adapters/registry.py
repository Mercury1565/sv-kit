"""Adapter registry: built-ins + `svkit.adapters` entry points."""
from __future__ import annotations

import importlib
from importlib.metadata import entry_points

from svkit.adapters.base import Adapter, AdapterNotFound
from svkit.config import Config

# name -> "module:Class". Tracks fill these in; a missing module just means "not built yet".
BUILTIN: dict[str, str] = {
    "verilator": "svkit.adapters.sim.verilator:VerilatorAdapter",
    "vcs": "svkit.adapters.sim.vcs:VcsAdapter",
    "yosys": "svkit.adapters.synth.yosys:YosysAdapter",
    "dc": "svkit.adapters.synth.dc:DcAdapter",
    "opensta": "svkit.adapters.sta.opensta:OpenStaAdapter",
}


def _targets() -> dict[str, str]:
    out = dict(BUILTIN)
    try:
        for ep in entry_points(group="svkit.adapters"):
            out[ep.name] = ep.value
    except Exception:  # pragma: no cover
        pass
    return out


def adapter_class(name: str) -> type[Adapter]:
    target = _targets().get(name)
    if target is None:
        raise AdapterNotFound(f"no adapter named {name!r} (known: {', '.join(sorted(_targets()))})")
    mod, _, cls = target.partition(":")
    try:
        return getattr(importlib.import_module(mod), cls)
    except (ImportError, AttributeError) as e:
        raise AdapterNotFound(f"adapter {name!r} ({target}) failed to load: {e}") from e


def get(name: str, cfg: Config) -> Adapter:
    return adapter_class(name)(cfg)


def known() -> list[str]:
    return sorted(_targets())
