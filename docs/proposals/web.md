# Proposals from Track 5 (web frontend)

The UI is built strictly against `docs/ARCHITECTURE.md` §6. These are the places where the
contract is ambiguous or where a small addition would make a view exact instead of heuristic.
None of them blocks the UI: each has a fallback today.

## 1. Shapes the UI had to assume (please document)

| Endpoint | What the UI assumes | Fallback |
|---|---|---|
| `GET /api/design/block` | `edges[].src/dst` are `"<node id>.<port>"` or a bare node id; anything that is not a node id is a parent-module port and is drawn as a small I/O node. `ports` items are `{name, dir, width}` or plain strings; `dir` starting with `in` → west side. Node ids may contain dots (`gen[3].u_x`); the UI matches the longest node-id prefix. | — |
| `GET /api/design/cone` | `edges: [{src, dst, net?, on_path?}]` (cell ids, optional `.pin` suffix), `pins` as strings. Path endpoints that are not cells become I/O nodes. | — |
| `GET /api/waves/{run}/decode` | `enum` keys may be decimal (`"5"`), Verilog literals (`"3'b101"`, `"8'h1f"`), hex (`"0x1f"`), or a bit string exactly `width` long (what pywellen gives for FST enums). | unknown keys → value shown in hex |
| `POST /api/waves/{run}/data` | `t0`/`t1` must be integers (the UI floors/ceils its float view window). `width: null` = real signal; the UI then draws it as a bus with the numeric value. | — |

## 2. Small additions that would help

Status (iteration 3, lead):
- **1 and 3 are done in the backend** (`GET /api/design/signal`, the `X-Total-Count` header). The UI
  still uses its fallbacks until the next frontend build.
- **2 and 4 were already true:** failed sim runs carry `SimResult.reason` as `message`, and sweep
  `group.updated` events carry `result`.
- **5 is open:** the server cannot attribute runs to a job before it returns. Runs come from shared
  executor threads, and sweeps create their group inside the call. It needs a group-first job API.

1. **`GET /api/design/signal?path=<wave path>` → `{module, file, line}`** (Track 4). The waves view
   needs "signal → source line". Today it walks `/api/design/hierarchy` (best instance-name match
   from the wave scopes) and greps the module file returned by `/api/source` for a declaration of
   the leaf name. This works for plain declarations but not for signals in generate blocks or
   declared via macros; the DesignGraph knows the exact location.
2. **Failure reason in `RunSummary`.** The Tests view lists the latest run per test from
   `GET /api/runs` (no metrics). It shows `run.message` as the reason. Proposal: the runner sets
   `message = SimResult.reason` for failed sim runs (no schema change), so the reason shows without
   one `GET /api/runs/{id}` per test.
3. **`X-Total-Count` header on `GET /api/runs`.** The UI pages with `limit=5000` until a short page;
   a total would let it show "loading 10,000 runs" and stop early.
4. **`group.updated` for sweeps should carry `result` (the `SweepResult`)** whenever a point lands.
   The sweep view already uses it when present and otherwise re-fetches `/api/sweeps/{gid}`.
5. **Jobs: `job.updated` with `run_ids` filled as soon as runs are created** (not only at the end),
   so the "Run all tests" button can link to the runs while they are still going.

## 3. Notes for the lead

* The production bundle contains the mock backend as a separate lazy chunk (~27 kB, loaded only
  with `?mock=1`). It is kept on purpose: `?mock=1&data=worst|empty` reproduces the break-ui
  worst cases against the shipped build. Drop it by gating the import on `import.meta.env.DEV` if
  preferred.
* `elkjs` is ~1.4 MB (440 kB gzip) and ECharts ~570 kB (190 kB gzip); both are lazy chunks loaded
  only by Design / chart views. The entry chunk is ~180 kB (58 kB gzip).
