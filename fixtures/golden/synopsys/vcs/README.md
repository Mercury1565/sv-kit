# VCS log samples — hand-written, NOT tool output

Hand-written reproductions of the VCS compile / runtime log layout (`Error-[CODE]`, `Warning-[CODE]`,
`Lint-[CODE]`, `$error` / `$fatal` / `$warning` runtime messages, SVA failures, the simulation report
footer). Replace / extend with real VCS logs from CAEN runs (redistribution check required before the
repo goes public). Each `X.log` has `X.expected.json` = `{"messages": [...], "failures": [...],
"final_time_ps": ...}` from `svkit.adapters.sim.vcs.parse_vcs_log` / `final_time_ps`.

Regenerate: `SVKIT_REGEN_GOLDEN=1 python -m pytest tests/test_vcs_adapter.py -q`
