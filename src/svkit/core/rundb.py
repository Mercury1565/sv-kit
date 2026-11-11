"""Run DB: `.svkit/runs.sqlite` + `.svkit/runs/<id>/` artifacts (shared contract).

A Run is one execution of one flow. Runs are grouped (sim --all, synth --matrix,
sweep) by `group_id`. `metrics` holds the normalized result (SynthResult /
SimResult JSON). Cache key = sha256(kind, adapter, source_hash, knobs, test).
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

DB_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,          -- 'r_0001'
    kind        TEXT NOT NULL,             -- 'sim' | 'synth' | 'sweep-point'
    adapter     TEXT NOT NULL,             -- 'verilator', 'vcs', 'dc', 'yosys', ...
    test        TEXT,                      -- sim only
    status      TEXT NOT NULL,             -- queued|running|passed|failed|done|error|cancelled
    created_at  REAL NOT NULL,
    started_at  REAL,
    finished_at REAL,
    duration_s  REAL,
    git_sha     TEXT,
    git_dirty   INTEGER,
    source_hash TEXT,
    knobs       TEXT NOT NULL DEFAULT '{}',   -- JSON {knob: value}
    experiment  TEXT,                          -- experiment name, if any
    cache_key   TEXT,
    cached_from TEXT,                          -- run id this result was copied from (cache hit)
    exit_code   INTEGER,
    metrics     TEXT,                          -- JSON: normalized SynthResult / SimResult
    artifacts   TEXT NOT NULL DEFAULT '{}',    -- JSON {name: relative path under runs/<id>/}
    group_id    TEXT,
    message     TEXT,                          -- short status / error text
    flow        TEXT,                          -- [flows.<kind>.<name>] key that ran it (v2; NULL = default)
    owner       TEXT,                          -- 'host:pid' of the svkit process running it (v3)
    name        TEXT                           -- the user's own name for the run (v4)
);
CREATE INDEX IF NOT EXISTS runs_kind ON runs(kind, created_at);
CREATE INDEX IF NOT EXISTS runs_cache ON runs(cache_key);
CREATE INDEX IF NOT EXISTS runs_group ON runs(group_id);
CREATE TABLE IF NOT EXISTS tags (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    tag    TEXT NOT NULL,
    PRIMARY KEY (run_id, tag)
);
CREATE TABLE IF NOT EXISTS groups (
    id         TEXT PRIMARY KEY,           -- 'g_0001'
    kind       TEXT NOT NULL,              -- 'sim-all' | 'matrix' | 'sweep'
    created_at REAL NOT NULL,
    status     TEXT NOT NULL,              -- running|done|failed|cancelled
    params     TEXT NOT NULL DEFAULT '{}', -- JSON
    result     TEXT,                       -- JSON (e.g. SweepResult)
    name       TEXT                        -- the user's own name for the batch (v4)
);
"""

TERMINAL = {"passed", "failed", "done", "error", "cancelled"}
NAME_MAX = 80


def clean_name(name: Optional[str]) -> Optional[str]:
    """A user-given run/batch name: one line, trimmed, at most NAME_MAX chars; empty means no name."""
    if name is None:
        return None
    s = " ".join(str(name).split())[:NAME_MAX].strip()
    return s or None


@dataclass
class Run:
    id: str
    kind: str
    adapter: str
    status: str
    created_at: float
    test: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_s: Optional[float] = None
    git_sha: Optional[str] = None
    git_dirty: Optional[bool] = None
    source_hash: Optional[str] = None
    knobs: dict[str, Any] = field(default_factory=dict)
    experiment: Optional[str] = None
    cache_key: Optional[str] = None
    cached_from: Optional[str] = None
    exit_code: Optional[int] = None
    metrics: Optional[dict[str, Any]] = None
    artifacts: dict[str, str] = field(default_factory=dict)
    group_id: Optional[str] = None
    message: Optional[str] = None
    flow: Optional[str] = None
    owner: Optional[str] = None
    name: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Group:
    id: str
    kind: str
    created_at: float
    status: str
    params: dict[str, Any] = field(default_factory=dict)
    result: Optional[dict[str, Any]] = None
    name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


_JSON_COLS = {"knobs", "metrics", "artifacts", "params", "result"}


def _owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _owner_alive(owner: Optional[str]) -> bool:
    """Is the process that owns a run still alive? Unknown owners (rows from before v3) count as dead;
    processes on other hosts count as alive (they cannot be checked from here)."""
    if not owner:
        return False
    host, _, pid = owner.rpartition(":")
    if host != socket.gethostname() or not pid.isdigit():
        return True
    if int(pid) == os.getpid():
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:      # alive, owned by another user
        return True
    return True


def _wal(c: sqlite3.Connection, tries: int = 300) -> None:
    """Switch to WAL once (the mode persists in the file). SQLite can refuse the switch with
    "database is locked" while another connection opens the same new DB, without honouring the
    busy timeout, so retry; overlapping `svkit` processes hit this."""
    for _ in range(tries):
        try:
            if c.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                c.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) and "busy" not in str(e):
                raise
            time.sleep(0.1)
    raise sqlite3.OperationalError(f"could not enable WAL on the Run DB after {tries} tries (database is locked)")


class RunDB:
    """Thread-safe (one connection per thread) access to the Run DB."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.runs_dir = self.state_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "runs.sqlite"
        self._local = threading.local()
        self._id_lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)
            # v1 -> v2: which flow (suite) a run belongs to; v2 -> v3: which process runs it;
            # v3 -> v4: the user's own name for a run or a batch
            for table, added in (("runs", ("flow", "owner", "name")), ("groups", ("name",))):
                cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                for col in added:
                    if col not in cols:
                        try:
                            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                        except sqlite3.OperationalError as e:   # another process migrated it first
                            if "duplicate column" not in str(e):
                                raise
            c.execute("INSERT OR IGNORE INTO meta VALUES ('version', ?)", (str(DB_VERSION),))
            c.execute("UPDATE meta SET value = ? WHERE key = 'version' AND CAST(value AS INTEGER) < ?",
                      (str(DB_VERSION), DB_VERSION))

    @classmethod
    def for_project(cls, root: Path) -> "RunDB":
        return cls(Path(root) / ".svkit")

    # -------------------------------------------------------------- plumbing
    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            _wal(c)
            c.execute("PRAGMA foreign_keys=ON")
            self._local.c = c
        yield c

    def _next_id(self, c: sqlite3.Connection, table: str, prefix: str) -> str:
        row = c.execute(f"SELECT id FROM {table} ORDER BY CAST(substr(id, 3) AS INTEGER) DESC LIMIT 1").fetchone()
        n = int(row["id"][2:]) + 1 if row else 1
        return f"{prefix}_{n:04d}"

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for k in _JSON_COLS & d.keys():
            if d[k] is not None:
                d[k] = json.loads(d[k])
        if "git_dirty" in d and d["git_dirty"] is not None:
            d["git_dirty"] = bool(d["git_dirty"])
        return d

    def run_dir(self, run_id: str) -> Path:
        d = self.runs_dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -------------------------------------------------------------- runs
    def create_run(self, kind: str, adapter: str, *, test: str | None = None,
                   knobs: dict | None = None, experiment: str | None = None,
                   group_id: str | None = None, git_sha: str | None = None,
                   git_dirty: bool | None = None, source_hash: str | None = None,
                   cache_key: str | None = None, status: str = "queued", flow: str | None = None,
                   name: str | None = None) -> Run:
        with self._id_lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                rid = self._next_id(c, "runs", "r")
                c.execute(
                    "INSERT INTO runs (id, kind, adapter, test, status, created_at, git_sha, git_dirty,"
                    " source_hash, knobs, experiment, cache_key, group_id, flow, owner, name)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, kind, adapter, test, status, time.time(), git_sha,
                     None if git_dirty is None else int(git_dirty), source_hash,
                     json.dumps(knobs or {}, sort_keys=True), experiment, cache_key, group_id, flow, _owner(), clean_name(name)))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        self.run_dir(rid)
        return self.get_run(rid)

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            if k == "tags":
                raise ValueError("use add_tag")
            if k in _JSON_COLS and v is not None:
                v = json.dumps(v, sort_keys=True)
            if k == "git_dirty" and v is not None:
                v = int(v)
            cols.append(f"{k} = ?")
            vals.append(v)
        with self._conn() as c:
            c.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id = ?", (*vals, run_id))

    def reap_orphans(self) -> list[str]:
        """Mark queued/running runs whose svkit process is gone (server restarted, Ctrl-C, crash) as
        cancelled, so they do not stay pending forever. Runs owned by a live process, or by a process
        on another host sharing this project directory, are left alone. Returns the reaped run ids."""
        with self._conn() as c:
            rows = c.execute("SELECT id, owner FROM runs WHERE status IN ('queued', 'running')").fetchall()
        dead = [r["id"] for r in rows if not _owner_alive(r["owner"])]
        now = time.time()
        for rid in dead:
            self.update_run(rid, status="cancelled", finished_at=now,
                            message="interrupted: the svkit process running it exited before it finished")
        return dead

    def get_run(self, run_id: str) -> Run:
        with self._conn() as c:
            row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            tags = [r["tag"] for r in c.execute("SELECT tag FROM tags WHERE run_id = ? ORDER BY tag", (run_id,))]
        return Run(**self._row(row), tags=tags)

    @staticmethod
    def _run_filter(kind, group_id, status, test, tag) -> tuple[list[str], list]:
        q, args = ["WHERE 1=1"], []
        for col, val in (("kind", kind), ("group_id", group_id), ("status", status), ("test", test)):
            if val is not None:
                q.append(f"AND {col} = ?")
                args.append(val)
        if tag is not None:
            q.append("AND id IN (SELECT run_id FROM tags WHERE tag = ?)")
            args.append(tag)
        return q, args

    def count_runs(self, *, kind: str | None = None, group_id: str | None = None,
                   status: str | None = None, test: str | None = None, tag: str | None = None) -> int:
        q, args = self._run_filter(kind, group_id, status, test, tag)
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM runs " + " ".join(q), args).fetchone()[0]

    def list_runs(self, *, kind: str | None = None, group_id: str | None = None,
                  status: str | None = None, test: str | None = None, tag: str | None = None,
                  limit: int = 200, offset: int = 0) -> list[Run]:
        q, args = self._run_filter(kind, group_id, status, test, tag)
        q.insert(0, "SELECT * FROM runs")
        q.append("ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?")
        args += [limit, offset]
        with self._conn() as c:
            rows = c.execute(" ".join(q), args).fetchall()
            out = []
            for row in rows:
                tags = [r["tag"] for r in c.execute("SELECT tag FROM tags WHERE run_id = ?", (row["id"],))]
                out.append(Run(**self._row(row), tags=tags))
        return out

    def find_cached(self, cache_key: str) -> Optional[Run]:
        """Latest reusable run with this cache key.

        Passed / done runs are reusable. A failed run is reused only when the failure is a result
        of the design (its output was compared and diverged): failures before any output exists
        (build error, a race in the user's build, a missing tool) may not repeat, so they rerun."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, status, metrics FROM runs WHERE cache_key = ? AND status IN ('passed','failed','done')"
                " AND metrics IS NOT NULL ORDER BY created_at DESC LIMIT 20", (cache_key,)).fetchall()
        for row in rows:
            if row["status"] != "failed":
                return self.get_run(row["id"])
            try:
                m = json.loads(row["metrics"])
            except (TypeError, ValueError):
                continue
            if isinstance(m, dict) and m.get("divergence"):
                return self.get_run(row["id"])
        return None

    def add_tag(self, run_id: str, tag: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO tags VALUES (?, ?)", (run_id, tag))

    def remove_tag(self, run_id: str, tag: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM tags WHERE run_id = ? AND tag = ?", (run_id, tag))

    def set_name(self, item_id: str, name: Optional[str]) -> None:
        """Name (or, with an empty name, unname) a run `r_…` or a batch `g_…`."""
        table = "groups" if item_id.startswith("g_") else "runs"
        with self._conn() as c:
            n = c.execute(f"UPDATE {table} SET name = ? WHERE id = ?", (clean_name(name), item_id)).rowcount
        if not n:
            raise KeyError(item_id)

    # -------------------------------------------------------------- groups
    def create_group(self, kind: str, params: dict | None = None, name: str | None = None) -> Group:
        with self._id_lock, self._conn() as c:
            gid = self._next_id(c, "groups", "g")
            c.execute("INSERT INTO groups (id, kind, created_at, status, params, name) VALUES (?,?,?,?,?,?)",
                      (gid, kind, time.time(), "running", json.dumps(params or {}, sort_keys=True), clean_name(name)))
        return self.get_group(gid)

    def update_group(self, gid: str, **fields: Any) -> None:
        cols, vals = [], []
        for k, v in fields.items():
            if k in _JSON_COLS and v is not None:
                v = json.dumps(v, sort_keys=True)
            cols.append(f"{k} = ?")
            vals.append(v)
        with self._conn() as c:
            c.execute(f"UPDATE groups SET {', '.join(cols)} WHERE id = ?", (*vals, gid))

    def get_group(self, gid: str) -> Group:
        with self._conn() as c:
            row = c.execute("SELECT * FROM groups WHERE id = ?", (gid,)).fetchone()
        if row is None:
            raise KeyError(gid)
        return Group(**self._row(row))

    def list_groups(self, kind: str | None = None, limit: int = 100) -> list[Group]:
        q, args = "SELECT * FROM groups", []
        if kind:
            q += " WHERE kind = ?"
            args.append(kind)
        q += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [Group(**self._row(r)) for r in c.execute(q, args).fetchall()]
