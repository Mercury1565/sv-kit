"""Large-waveform responsiveness: a ~50 MB synthetic VCD, narrow-window queries must be fast."""
import time

import pytest

from svkit.adapters.waves import wellen as W
from wavegen import write_big_vcd


@pytest.mark.slow
def test_big_vcd_narrow_window_fast(tmp_path):
    p = tmp_path / "big.vcd"
    st = write_big_vcd(p, target_mb=50)
    assert st["bytes"] > 45e6
    W.clear_cache()
    t = time.perf_counter()
    wf = W.open_wave(p)
    m = wf.meta()
    t_open = time.perf_counter() - t
    assert m["t_end"] == st["t_end"]
    sigs = ["top.clk", "top.slow", "top.unit0.bus0", "top.unit3.bus30", "top.unit7.bus63"]
    T = st["t_end"]
    t = time.perf_counter()
    d = wf.data(sigs, T // 2, T // 2 + 2000, 2000)
    t_first = time.perf_counter() - t
    t = time.perf_counter()
    for k in range(20):                                   # panning
        wf.data(sigs, T // 3 + k * 1000, T // 3 + k * 1000 + 2000, 2000)
    t_pan = (time.perf_counter() - t) / 20
    t = time.perf_counter()
    z = wf.data(sigs, 0, T, 2000)                         # fully zoomed out
    t_zoom = time.perf_counter() - t
    print(f"\n50MB VCD: open+meta {t_open:.3f}s, first narrow window {t_first:.3f}s, "
          f"pan {t_pan * 1000:.2f}ms/query, full zoom-out {t_zoom:.3f}s")
    assert len(d["signals"]["top.clk"]["changes"]) == 400
    assert z["signals"]["top.clk"]["truncated"]
    assert all(len(v["changes"]) <= 4000 for v in z["signals"].values())
    assert t_first < 0.5 and t_pan < 0.05 and t_zoom < 0.5
    assert W.open_wave(p) is wf                          # cached: opened once
