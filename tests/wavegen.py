"""Synthetic VCD generator for waveform-backend performance tests (no simulator needed)."""
from __future__ import annotations

from pathlib import Path


def write_big_vcd(path: Path, *, target_mb: float = 50, n_buses: int = 64, n_scopes: int = 8) -> dict:
    """A clock toggling every tick, `n_buses` 32-bit counters (bus k changes every k+1 cycles)
    spread over `n_scopes` scopes, plus a rarely-changing `slow` signal. Returns stats."""
    ids = []
    for k in range(n_buses + 2):
        s, n = "", k
        while True:
            s += chr(33 + n % 94)
            n //= 94
            if n == 0:
                break
        ids.append(s)
    clk, slow, buses = ids[0], ids[1], ids[2:]
    hdr = ["$timescale 1ps $end", "$scope module top $end",
           f"$var wire 1 {clk} clk $end", f"$var wire 8 {slow} slow [7:0] $end"]
    per = max(1, n_buses // n_scopes)
    for sc in range(n_scopes):
        hdr.append(f"$scope module unit{sc} $end")
        for k in range(sc * per, min(n_buses, (sc + 1) * per)):
            hdr.append(f"$var reg 32 {buses[k]} bus{k} [31:0] $end")
        hdr.append("$upscope $end")
    hdr += ["$upscope $end", "$enddefinitions $end", "#0", f"0{clk}", f"b0 {slow}"]
    hdr += [f"b0 {b}" for b in buses]
    target = int(target_mb * 1e6)
    size, cyc = 0, 0
    with open(path, "w") as f:
        f.write("\n".join(hdr) + "\n")
        chunk: list[str] = []
        while size < target:
            cyc += 1
            t = cyc * 10
            chunk.append(f"#{t - 5}\n1{clk}\n#{t}\n0{clk}\n")
            for k in range(n_buses):
                if cyc % (k + 1) == 0:
                    chunk.append(f"b{cyc // (k + 1):b} {buses[k]}\n")
            if cyc % 100_000 == 0:
                chunk.append(f"b{(cyc // 100_000) & 255:b} {slow}\n")
            if len(chunk) > 20000:
                s = "".join(chunk)
                size += len(s)
                f.write(s)
                chunk = []
        s = "".join(chunk)
        f.write(s)
        size += len(s)
    return {"cycles": cyc, "t_end": cyc * 10, "bytes": path.stat().st_size}
