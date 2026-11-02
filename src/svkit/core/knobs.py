"""Knob mechanics: knob values -> KnobPlan, and applying a plan to a work dir.

Rules (decision #10): every knob is applied inside an isolated work dir. The
user's Makefile/TCL are never edited in place. TCL knobs are applied by a
generated wrapper script that sets variables, then `source`s the user's script
(tcl_var), or by editing a COPY of the script in the work dir
(tcl_replace / tcl_append) — text replacement is the last resort.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from svkit.adapters.base import KnobPlan
from svkit.config import RESERVED_EXPERIMENT_KEYS, Config, ConfigError, Knob, expand

_TIME_SCALE = {"fs": 1e-3, "ps": 1.0, "ns": 1e3, "us": 1e6}


def parse_set(items: list[str]) -> dict[str, Any]:
    """Parse CLI `--set knob=value` items. Values: int, float, true/false, or string."""
    out: dict[str, Any] = {}
    for it in items:
        if "=" not in it:
            raise ConfigError(f"--set expects knob=value, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = _coerce(v.strip())
    return out


def _coerce(v: str) -> Any:
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    for t in (int, float):
        try:
            return t(v)
        except ValueError:
            pass
    return v


def resolve(cfg: Config, *, experiment: str | None = None, sets: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge knob defaults < experiment < --set into a flat {knob: value} dict."""
    values: dict[str, Any] = {k: kn.default for k, kn in cfg.knobs.items() if kn.default is not None}
    if experiment:
        if experiment not in cfg.experiments:
            raise ConfigError(f"no experiment {experiment!r} (have: {', '.join(cfg.experiments) or 'none'})")
        values.update(cfg.experiments[experiment])
    for k, v in (sets or {}).items():
        if k not in cfg.knobs and k not in RESERVED_EXPERIMENT_KEYS:
            raise ConfigError(f"unknown knob {k!r} (define it under [knobs])")
        values[k] = v
    return values


def render_value(knob: Knob, value: Any, *, name: str = "") -> str:
    """Render a knob value. clock_period values are carried in ps internally and
    converted to the knob's `unit` (e.g. a file holding ns)."""
    if knob.unit and isinstance(value, (int, float)):
        v = float(value) / _TIME_SCALE[knob.unit]
        value = int(v) if v.is_integer() else v
    if isinstance(value, list):
        value = " ".join(str(x) for x in value)
    return expand(knob.format, value=value)


def plan(cfg: Config, values: dict[str, Any], *, stage: str = "synth") -> KnobPlan:
    """Turn knob values into a KnobPlan for `stage` ('sim' | 'synth' | 'sta')."""
    p = KnobPlan(values=dict(values))
    for name, value in values.items():
        if name == "tcl_append":
            for text in ([value] if isinstance(value, str) else value):
                p.tcl_append.append((None, text))
            continue
        if name == "env":
            p.env.update({k: str(v) for k, v in dict(value).items()})
            continue
        knob = cfg.knobs.get(name)
        if knob is None:
            raise ConfigError(f"unknown knob {name!r}")
        if stage not in knob.applies_to:
            continue
        s = render_value(knob, value, name=name)
        m = knob.mechanism
        if m == "file":
            p.files[knob.file] = s + "\n"
        elif m == "env":
            p.env[knob.env] = s
        elif m == "tcl_var":
            p.tcl_prelude.append(f"set {knob.tcl_var} {_tcl_quote(s)}")
        elif m == "tcl_replace":
            p.tcl_replace.append((knob.tcl_replace, expand(knob.with_, value=s)))
        elif m == "tcl_append":
            p.tcl_append.append((knob.before, s))
        elif m == "define":
            p.defines[knob.define] = s
    return p


def _tcl_quote(s: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.+\-/:]+", s):
        return s
    return "{" + s + "}"


def apply_files(p: KnobPlan, workdir: Path) -> None:
    for rel, content in p.files.items():
        f = workdir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)


def edit_script(text: str, p: KnobPlan) -> str:
    """Apply tcl_replace / tcl_append to the TEXT of a copy of the user's script."""
    for pattern, repl in p.tcl_replace:
        new, n = re.subn(pattern, lambda _m, r=repl: r, text, flags=re.MULTILINE)
        if n == 0:
            raise ConfigError(f"tcl_replace pattern {pattern!r} matched nothing in the script")
        text = new
    for anchor, add in p.tcl_append:
        if anchor:
            m = re.search(anchor, text, flags=re.MULTILINE)
            if not m:
                raise ConfigError(f"tcl_append anchor {anchor!r} matched nothing in the script")
            text = text[:m.start()] + add + "\n" + text[m.start():]
        else:
            text = text.rstrip("\n") + "\n" + add + "\n"
    return text


def prepare_script(p: KnobPlan, script_rel: str, workdir: Path) -> Optional[Path]:
    """Apply TCL knobs to the work-dir COPY of the user's script (never the original).

    Order: tcl_replace / tcl_append edits, then the tcl_var prelude is injected at the
    top of the copy. Plain top-level `set <var> ...` lines for knob variables are guarded
    with `if {![info exists var]}` so the user's defaults don't override the knob.
    Returns the edited copy's path, or None when no TCL knob applies.
    """
    if not (p.tcl_prelude or p.tcl_replace or p.tcl_append):
        return None
    script = workdir / script_rel
    body = edit_script(script.read_text(), p)
    if p.tcl_prelude:
        body = ("# --- svkit knob prelude (generated) ---\n" + "\n".join(p.tcl_prelude)
                + "\n# --- user script (unchanged except guarded knob defaults) ---\n"
                + _guard_defaults(body, p))
    script.write_text(body)
    return script


def _guard_defaults(body: str, p: KnobPlan) -> str:
    """If the user's script does `set corner TT`, a prelude `set corner SS` would be overwritten.
    Rewrite each plain top-level `set <var> ...` for a knob var into `if {![info exists var]} {set ...}`."""
    names = {line.split()[1] for line in p.tcl_prelude if line.startswith("set ")}
    def repl(m: re.Match) -> str:
        indent, var, rest = m.group(1), m.group(2), m.group(3)
        if var not in names:
            return m.group(0)
        return f"{indent}if {{![info exists {var}]}} {{set {var} {rest}}}"
    return re.sub(r"^([ \t]*)set[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]+(.+?)[ \t]*$", repl, body, flags=re.MULTILINE)
