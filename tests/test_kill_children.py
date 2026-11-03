"""Tools run in their own session; kill_children() must still stop them (Ctrl-C / SIGTERM on svkit)."""
import threading
import time

from svkit import config as C
from svkit.adapters import base as B


def test_kill_children_stops_running_tools(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname = "k"\ntop = "t"\n')
    cfg = C.load(tmp_path)
    ctx = B.RunContext(cfg=cfg, root=tmp_path, workdir=tmp_path, run_dir=tmp_path, run_id="r_0001",
                       flow_name="x")
    rc: list[int] = []
    t = threading.Thread(target=lambda: rc.append(ctx.sh("sleep 300")))
    t.start()
    deadline = time.time() + 5
    while not B._LIVE and time.time() < deadline:
        time.sleep(0.01)
    t0 = time.time()
    assert B.kill_children() == 1
    t.join(10)
    assert not t.is_alive() and time.time() - t0 < 5
    assert rc and rc[0] != 0
    assert not B._LIVE and B.kill_children() == 0
