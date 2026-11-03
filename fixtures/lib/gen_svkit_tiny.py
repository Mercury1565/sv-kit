#!/usr/bin/env python3
"""Generate the svkit toy Liberty libraries (svkit_tiny.lib, svkit_tiny_ss.lib).

This is ORIGINAL work written for svkit's test corpus. It is NOT a real process
and its numbers are invented: they are chosen only so that Yosys (dfflibmap/abc)
and OpenSTA (report_checks/report_power) produce small, deterministic,
easy-to-reason-about results.

Model (all times in ns because time_unit = 1ns; caps in fF):
    delay      = base * vt_k * corner_k + drive * C_load + 0.2 * slew_in
    transition = 0.010 + 0.004 * C_load (* corner_k)
Each cell exists in two Vt flavours:
    *_R  "regular" Vt: slower (vt_k = 1.0), leakage x1
    *_L  "low" Vt    : faster (vt_k = 0.75), leakage x6
Tables are 2x2 NLDM (index_1 = input slew, index_2 = output load) and are exactly
linear in both indices, so OpenSTA interpolation is exact.

Re-generate after editing:  python3 gen_svkit_tiny.py   (writes both corners here)
"""
from __future__ import annotations

from pathlib import Path

SLEW_IDX = (0.01, 0.2)      # ns
LOAD_IDX = (1.0, 20.0)      # fF

# name: (inputs, function, timing_sense per input, base delay ns, area um^2, leakage nW, in-cap fF)
COMB = {
    "INV":   (["A"], "!A", "negative_unate", 0.012, 1.0, 1.0, 1.0),
    "BUF":   (["A"], "A", "positive_unate", 0.020, 1.5, 1.5, 1.0),
    "NAND2": (["A", "B"], "!(A&B)", "negative_unate", 0.016, 1.5, 1.6, 1.2),
    "NOR2":  (["A", "B"], "!(A|B)", "negative_unate", 0.020, 1.5, 1.4, 1.2),
    "AND2":  (["A", "B"], "(A&B)", "positive_unate", 0.028, 2.0, 2.2, 1.1),
    "OR2":   (["A", "B"], "(A|B)", "positive_unate", 0.032, 2.0, 2.0, 1.1),
    "XOR2":  (["A", "B"], "(A^B)", "non_unate", 0.040, 3.0, 3.0, 1.6),
    "XNOR2": (["A", "B"], "!(A^B)", "non_unate", 0.040, 3.0, 3.0, 1.6),
    "AOI21": (["A1", "A2", "B"], "!((A1&A2)|B)", "negative_unate", 0.024, 2.0, 2.1, 1.3),
    "OAI21": (["A1", "A2", "B"], "!((A1|A2)&B)", "negative_unate", 0.024, 2.0, 2.1, 1.3),
    "MUX2":  (["A", "B", "S"], "((A&!S)|(B&S))", "non_unate", 0.045, 3.5, 3.2, 1.4),
}
DRIVE = 0.002               # ns per fF of output load
VT = {"_R": (1.0, 1.0), "_L": (0.75, 6.0)}   # suffix: (delay factor, leakage factor)
CORNERS = {"tt": ("svkit_tiny.lib", 1.0, 1.00), "ss": ("svkit_tiny_ss.lib", 1.3, 0.90)}


def f(x: float) -> str:
    return f"{x:.4f}"


def table(kind: str, fn) -> str:
    rows = ", ".join('"' + ", ".join(f(fn(s, c)) for c in LOAD_IDX) + '"' for s in SLEW_IDX)
    return f"        {kind} (delay_2x2) {{ values ({rows}); }}\n"


def ptable(kind: str, energy: float) -> str:
    # internal energy grows a little with load (fJ)
    rows = ", ".join('"' + ", ".join(f(energy * (1 + 0.02 * c)) for c in LOAD_IDX) + '"' for _ in SLEW_IDX)
    return f"        {kind} (power_2x2) {{ values ({rows}); }}\n"


def arc(pin: str, sense: str, base: float, k: float, ttype: str | None = None,
        rise: bool = True, fall: bool = True) -> str:
    d = lambda s, c: base * k + DRIVE * k * c + 0.2 * s
    t = lambda s, c: (0.010 + 0.004 * c) * k
    out = f"      timing () {{\n        related_pin : \"{pin}\";\n        timing_sense : {sense};\n"
    if ttype:
        out += f"        timing_type : {ttype};\n"
    if rise:
        out += table("cell_rise", d) + table("rise_transition", t)
    if fall:
        out += table("cell_fall", d) + table("fall_transition", t)
    return out + "      }\n"


def ipower(pin: str, energy: float) -> str:
    return (f"      internal_power () {{\n        related_pin : \"{pin}\";\n"
            + ptable("rise_power", energy) + ptable("fall_power", energy) + "      }\n")


def constraint(pin: str, ttype: str, v: float) -> str:
    return (f"      timing () {{\n        related_pin : \"{pin}\";\n        timing_type : {ttype};\n"
            f"        rise_constraint (scalar) {{ values (\"{f(v)}\"); }}\n"
            f"        fall_constraint (scalar) {{ values (\"{f(v)}\"); }}\n      }}\n")


def comb_cell(name: str, spec, vt_k: float, leak_k: float, ck: float) -> str:
    ins, fn, sense, base, area, leak, cap = spec
    s = f"  cell ({name}) {{\n    area : {area};\n    cell_leakage_power : {f(leak * leak_k)};\n"
    for p in ins:
        s += f"    pin ({p}) {{ direction : input; capacitance : {cap}; }}\n"
    s += f"    pin (Y) {{\n      direction : output;\n      function : \"{fn}\";\n      max_capacitance : 60;\n"
    for p in ins:
        psense = "non_unate" if (name.startswith("MUX") and p == "S") else sense
        if name.startswith("MUX") and p != "S":
            psense = "positive_unate"
        s += arc(p, psense, base, vt_k * ck)
        s += ipower(p, 0.8 * area)
    return s + "    }\n  }\n"


def seq_cells(sfx: str, vt_k: float, leak_k: float, ck: float) -> str:
    k = vt_k * ck
    clkq = 0.050
    setup, hold = 0.030 * k, 0.010 * k
    out = []
    # positive-edge D flip-flop
    out.append(
        f"  cell (DFF{sfx}) {{\n    area : 5.0;\n    cell_leakage_power : {f(5.0 * leak_k)};\n"
        "    ff (IQ, IQN) { clocked_on : \"CLK\"; next_state : \"D\"; }\n"
        "    pin (CLK) { direction : input; clock : true; capacitance : 1.0;\n"
        + ipower_inline("CLK", 1.5) + "    }\n"
        "    pin (D) {\n      direction : input;\n      capacitance : 1.0;\n"
        + constraint("CLK", "setup_rising", setup) + constraint("CLK", "hold_rising", hold) + "    }\n"
        "    pin (Q) {\n      direction : output;\n      function : \"IQ\";\n      max_capacitance : 60;\n"
        + arc("CLK", "non_unate", clkq, k, "rising_edge") + "    }\n  }\n")
    # positive-edge D flip-flop with active-low asynchronous clear
    out.append(
        f"  cell (DFFR{sfx}) {{\n    area : 6.0;\n    cell_leakage_power : {f(6.0 * leak_k)};\n"
        "    ff (IQ, IQN) { clocked_on : \"CLK\"; next_state : \"D\"; clear : \"!RN\"; }\n"
        "    pin (CLK) { direction : input; clock : true; capacitance : 1.0;\n"
        + ipower_inline("CLK", 1.6) + "    }\n"
        "    pin (D) {\n      direction : input;\n      capacitance : 1.0;\n"
        + constraint("CLK", "setup_rising", setup) + constraint("CLK", "hold_rising", hold) + "    }\n"
        "    pin (RN) {\n      direction : input;\n      capacitance : 1.0;\n"
        + constraint("CLK", "recovery_rising", setup) + constraint("CLK", "removal_rising", hold) + "    }\n"
        "    pin (Q) {\n      direction : output;\n      function : \"IQ\";\n      max_capacitance : 60;\n"
        + arc("CLK", "non_unate", clkq, k, "rising_edge")
        + arc("RN", "positive_unate", 0.040, k, "clear", rise=False) + "    }\n  }\n")
    # positive-level transparent latch
    out.append(
        f"  cell (DLATCH{sfx}) {{\n    area : 4.0;\n    cell_leakage_power : {f(4.0 * leak_k)};\n"
        "    latch (IQ, IQN) { enable : \"G\"; data_in : \"D\"; }\n"
        "    pin (G) { direction : input; clock : true; capacitance : 1.0; }\n"
        "    pin (D) {\n      direction : input;\n      capacitance : 1.0;\n"
        + constraint("G", "setup_falling", setup) + constraint("G", "hold_falling", hold) + "    }\n"
        "    pin (Q) {\n      direction : output;\n      function : \"IQ\";\n      max_capacitance : 60;\n"
        + arc("D", "positive_unate", 0.035, k) + arc("G", "non_unate", 0.040, k, "rising_edge") + "    }\n  }\n")
    # tie cells
    for nm, val in (("TIEHI", "1"), ("TIELO", "0")):
        out.append(f"  cell ({nm}{sfx}) {{\n    area : 1.0;\n    cell_leakage_power : {f(0.5 * leak_k)};\n"
                   f"    pin (Y) {{ direction : output; function : \"{val}\"; }}\n  }}\n")
    return "".join(out)


def ipower_inline(pin: str, energy: float) -> str:
    return ("      internal_power () {\n"
            + ptable("rise_power", energy) + ptable("fall_power", energy) + "      }\n")


def library(corner: str) -> str:
    fname, ck, volt = CORNERS[corner]
    hdr = f"""/*
 * svkit_tiny ({corner} corner) -- an ORIGINAL toy Liberty library for svkit tests.
 * Not a real process: every number is invented. Generated by gen_svkit_tiny.py; do not edit.
 * Cells: INV BUF NAND2 NOR2 AND2 OR2 XOR2 XNOR2 AOI21 OAI21 MUX2 DFF DFFR DLATCH TIEHI TIELO,
 * each as *_R (regular Vt: slower, low leakage) and *_L (low Vt: faster, ~6x leakage).
 * Delay = base*vt*corner + {DRIVE} ns/fF * load + 0.2*input slew   (corner factor {ck})
 */
library (svkit_tiny_{corner}) {{
  delay_model : table_lookup;
  time_unit : "1ns";
  voltage_unit : "1V";
  current_unit : "1mA";
  pulling_resistance_unit : "1kohm";
  leakage_power_unit : "1nW";
  capacitive_load_unit (1, ff);
  nom_process : 1.0;
  nom_temperature : 25.0;
  nom_voltage : {volt};
  operating_conditions ({corner}) {{ process : 1.0; temperature : 25.0; voltage : {volt}; tree_type : balanced_tree; }}
  default_operating_conditions : {corner};
  input_threshold_pct_rise : 50; input_threshold_pct_fall : 50;
  output_threshold_pct_rise : 50; output_threshold_pct_fall : 50;
  slew_lower_threshold_pct_rise : 20; slew_lower_threshold_pct_fall : 20;
  slew_upper_threshold_pct_rise : 80; slew_upper_threshold_pct_fall : 80;
  default_max_transition : 0.5;
  default_inout_pin_cap : 1.0;
  default_input_pin_cap : 1.0;
  default_output_pin_cap : 0.0;
  default_fanout_load : 1.0;
  default_cell_leakage_power : 0.0;
  default_leakage_power_density : 0.0;
  lu_table_template (delay_2x2) {{
    variable_1 : input_net_transition;
    variable_2 : total_output_net_capacitance;
    index_1 ("{SLEW_IDX[0]}, {SLEW_IDX[1]}");
    index_2 ("{LOAD_IDX[0]}, {LOAD_IDX[1]}");
  }}
  power_lut_template (power_2x2) {{
    variable_1 : input_transition_time;
    variable_2 : total_output_net_capacitance;
    index_1 ("{SLEW_IDX[0]}, {SLEW_IDX[1]}");
    index_2 ("{LOAD_IDX[0]}, {LOAD_IDX[1]}");
  }}
"""
    body = ""
    for sfx, (vt_k, leak_k) in VT.items():
        for name, spec in COMB.items():
            body += comb_cell(name + sfx, spec, vt_k, leak_k, ck)
        body += seq_cells(sfx, vt_k, leak_k, ck)
    return hdr + body + "}\n"


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    for c, (fname, _, _) in CORNERS.items():
        (here / fname).write_text(library(c))
        print("wrote", here / fname)
