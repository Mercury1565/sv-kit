// Typed fetch wrappers for the svkit REST API (docs/ARCHITECTURE.md §6).
// When mock mode is on (?mock=1 or VITE_MOCK=1) requests are served by ./mock.ts instead.
import type {
  BlockGraph,
  CompareResult,
  ConeGraph,
  DesignHierarchy,
  Divergence,
  Group,
  Job,
  JobKind,
  PerfResult,
  Project,
  Run,
  RunSummary,
  SourceSlice,
  SweepResult,
  WaveData,
  WaveDecode,
  WaveMeta,
  WaveScope,
  WaveSearchHit,
  XTraceNode,
  Schematic,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public path: string,
  ) {
    super(`${status} ${detail}`);
  }
  /** 404/405/501 on a feature route usually means "this feature is not available in this build yet". */
  get notAvailable(): boolean {
    return this.status === 404 || this.status === 405 || this.status === 501;
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export interface Transport {
  request(method: string, path: string, query?: Query, body?: unknown): Promise<unknown>;
}

function qs(query?: Query): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const httpTransport: Transport = {
  async request(method, path, query, body) {
    const res = await fetch(path + qs(query), {
      method,
      headers: body !== undefined ? { "content-type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data: unknown = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
    if (!res.ok) {
      const detail =
        data && typeof data === "object" && "detail" in (data as object)
          ? String((data as { detail: unknown }).detail)
          : res.statusText || "request failed";
      throw new ApiError(res.status, detail, path);
    }
    return data;
  },
};

export function mockRequested(): boolean {
  try {
    if (import.meta.env?.VITE_MOCK === "1") return true;
    const sp = new URLSearchParams(window.location.search);
    return sp.get("mock") === "1";
  } catch {
    return false;
  }
}

let transport: Transport = httpTransport;
let mockOn = false;

export function setTransport(t: Transport, isMock = false): void {
  transport = t;
  mockOn = isMock;
}

export function isMock(): boolean {
  return mockOn;
}

function req<T>(method: string, path: string, query?: Query, body?: unknown): Promise<T> {
  return transport.request(method, path, query, body) as Promise<T>;
}

const enc = encodeURIComponent;

export const api = {
  project: () => req<Project>("GET", "/api/project"),

  runs: (q: { kind?: string; group?: string; status?: string; test?: string; tag?: string; limit?: number; offset?: number } = {}) =>
    req<RunSummary[]>("GET", "/api/runs", q),
  /** Loads every run page by page (5000 per page, the server maximum). */
  async allRuns(q: { kind?: string; group?: string; status?: string; test?: string; tag?: string } = {}, max = 50000) {
    const out: RunSummary[] = [];
    const page = 5000;
    for (let offset = 0; offset < max; offset += page) {
      const chunk = await api.runs({ ...q, limit: page, offset });
      out.push(...chunk);
      if (chunk.length < page) break;
    }
    return out;
  },
  run: (id: string) => req<Run>("GET", `/api/runs/${enc(id)}`),
  artifactUrl: (id: string, name: string) => `/api/runs/${enc(id)}/artifacts/${name.split("/").map(enc).join("/")}`,
  log: (id: string, tail = 500) => req<{ lines: string[] }>("GET", `/api/runs/${enc(id)}/log`, { tail }),
  addTag: (id: string, tag: string) => req<Run>("POST", `/api/runs/${enc(id)}/tags`, undefined, { tag }),
  removeTag: (id: string, tag: string) => req<Run>("DELETE", `/api/runs/${enc(id)}/tags/${enc(tag)}`),
  divergence: (id: string) => req<Divergence>("GET", `/api/runs/${enc(id)}/divergence`),

  groups: (kind?: string) => req<Group[]>("GET", "/api/groups", { kind }),
  group: (id: string) => req<Group>("GET", `/api/groups/${enc(id)}`),

  compare: (ids: string[]) => req<CompareResult>("GET", "/api/compare", { ids: ids.join(",") }),
  sweep: (gid: string) => req<SweepResult>("GET", `/api/sweeps/${enc(gid)}`),
  perf: (group?: string) => req<PerfResult[]>("GET", "/api/perf", { group }),

  submitJob: (kind: JobKind, args: Record<string, unknown> = {}) => req<{ job_id: string }>("POST", "/api/jobs", undefined, { kind, args }),
  jobs: () => req<Job[]>("GET", "/api/jobs"),

  waveMeta: (run: string) => req<WaveMeta>("GET", `/api/waves/${enc(run)}/meta`),
  waveScope: (run: string, path = "") => req<WaveScope>("GET", `/api/waves/${enc(run)}/scope`, { path }),
  waveSearch: (run: string, q: string, limit = 200) => req<WaveSearchHit[]>("GET", `/api/waves/${enc(run)}/search`, { q, limit }),
  waveData: (run: string, body: { signals: string[]; t0: number; t1: number; max_changes?: number }) =>
    req<WaveData>("POST", `/api/waves/${enc(run)}/data`, undefined, body),
  waveNext: (run: string, signal: string, t: number, what: "change" | "x", dir: 1 | -1) =>
    req<{ t: number | null }>("GET", `/api/waves/${enc(run)}/next`, { signal, t, what, dir }),
  waveDecode: (run: string, signal: string) => req<WaveDecode>("GET", `/api/waves/${enc(run)}/decode`, { signal }),

  hierarchy: () => req<DesignHierarchy>("GET", "/api/design/hierarchy"),
  block: (module: string) => req<BlockGraph>("GET", "/api/design/block", { module }),
  cone: (q: { run: string; net?: string; depth?: number; path?: number | string }) => req<ConeGraph>("GET", "/api/design/cone", q),
  /** Yosys-JSON netlists drawn by netlistsvg: one module level (Blocks) or a gate cone (Gates). */
  schematicBlock: (module: string) => req<Schematic>("GET", "/api/design/schematic/block", { module }),
  schematicCone: (q: { run: string; net?: string; depth?: number; path?: number | string }) =>
    req<Schematic>("GET", "/api/design/schematic/cone", q),

  xtrace: (q: { run: string; signal: string; time: number; depth?: number }) => req<XTraceNode>("GET", "/api/xtrace", q),
  source: (file: string, line = 1, context = 20) => req<SourceSlice>("GET", "/api/source", { file, line, context }),
};

export type Api = typeof api;
