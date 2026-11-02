import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"

# Make user-local EDA installs visible to tests (OSS CAD Suite, OpenSTA in ~/tools/bin).
_EXTRA = [Path.home() / "tools" / "bin", Path.home() / "tools" / "oss-cad-suite" / "bin"]
os.environ["PATH"] = os.pathsep.join([str(p) for p in _EXTRA if p.is_dir()] + [os.environ.get("PATH", "")])

HAVE_OSS = all(shutil.which(t) for t in ("verilator", "yosys", "sta"))
HAVE_SYNOPSYS = all(shutil.which(t) for t in ("vcs", "dc_shell-t"))


def pytest_collection_modifyitems(config, items):
    skip_oss = pytest.mark.skip(reason="OSS CAD Suite tools (verilator, yosys, sta) not on PATH")
    skip_snps = pytest.mark.skip(reason="Synopsys vcs / dc_shell-t not on PATH")
    for it in items:
        if "oss" in it.keywords and not HAVE_OSS:
            it.add_marker(skip_oss)
        if "synopsys" in it.keywords and not HAVE_SYNOPSYS:
            it.add_marker(skip_snps)


@pytest.fixture
def fixture_copy(tmp_path):
    """Copy a fixture project into tmp so runs never write into the repo."""
    def _copy(name: str) -> Path:
        dst = tmp_path / name
        shutil.copytree(FIXTURES / name, dst, ignore=shutil.ignore_patterns(".svkit", "obj_dir", "build"))
        lib = FIXTURES / "lib"
        if lib.is_dir() and not (tmp_path / "lib").exists():
            shutil.copytree(lib, tmp_path / "lib")
        return dst
    return _copy
