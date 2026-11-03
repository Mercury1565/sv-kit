"""The generic Synopsys fixture templates (fixtures/synopsys_common): sanity + fixture copies in sync."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMMON = REPO / "fixtures" / "synopsys_common"
COPIES = {"syn_dc.tcl": "synopsys/syn.tcl", "syn_run.tcl": "synopsys/syn_run.tcl", "vcs.mk": "synopsys/vcs.mk"}


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth < 0:
            return False
    return depth == 0


@pytest.mark.parametrize("name", ["syn_dc.tcl", "syn_run.tcl"])
def test_tcl_braces_balanced(name):
    assert _balanced((COMMON / name).read_text())


def test_templates_are_generic():
    for f in COMMON.iterdir():
        t = f.read_text().lower()
        for bad in ("saed", "asap7", "/usr/caen", "eecs470", "/home/"):
            assert bad not in t, (f.name, bad)


def test_compile_line_matches_compile_ultra_knob():
    import re
    body = (COMMON / "syn_dc.tcl").read_text()
    assert len(re.findall(r"^compile\b.*$", body, flags=re.M)) == 1


def test_fixture_copies_in_sync():
    """Fixtures that use the Synopsys flow must carry verbatim copies (work dirs can't see ../)."""
    for fx in sorted((REPO / "fixtures").iterdir()):
        if not (fx / "synopsys").is_dir():
            continue
        for src, dst in COPIES.items():
            if (fx / dst).exists():
                assert (fx / dst).read_text() == (COMMON / src).read_text(), f"{fx.name}/{dst} differs from template"


@pytest.mark.skipif(not shutil.which("make"), reason="make not available")
def test_vcs_mk_dry_run(tmp_path):
    (tmp_path / "synopsys").mkdir()
    shutil.copy(COMMON / "vcs.mk", tmp_path / "synopsys" / "vcs.mk")
    (tmp_path / "rtl.sv").write_text("module t; endmodule\n")
    (tmp_path / "Makefile").write_text("TOP = t\nSOURCES = rtl.sv\nTB_SOURCES =\ninclude synopsys/vcs.mk\n")
    out = subprocess.run(["make", "-n", "syn-dc", "sim-vcs", "TEST=abc"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    s = out.stdout
    assert "dc_shell-t -f synopsys/syn_run.tcl" in s and 'SVKIT_TOP="t"' in s
    assert "vcs -sverilog" in s and "-top t" in s and "+TEST=abc" in s
