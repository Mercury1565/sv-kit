import type { Project } from "./api/types";
import { Logo } from "./components/Logo";
import { Suspense, lazy, type ReactNode, useEffect, useRef, useState } from "react";
import { isMock } from "./api/client";
import { mockDataset } from "./api/dataset";
import { Loading, ErrorState, Empty } from "./components/States";
import { AppStateProvider, useApp } from "./lib/appState";
import { shortSha } from "./lib/format";
import { navigate, useRoute, type Route } from "./lib/router";
import { RunsView } from "./views/Runs";
import { RunDetail } from "./views/RunDetail";
import { TestsView } from "./views/Tests";
import { ThemeSwitch } from "./components/ThemeSwitch";
import { NavIcon } from "./components/NavIcon";

const WavesView = lazy(() => import("./views/waves/Waves"));
const DesignView = lazy(() => import("./views/Design"));
const XTraceView = lazy(() => import("./views/XTrace"));
const SynthView = lazy(() => import("./views/Synth"));
const CompareView = lazy(() => import("./views/Compare"));
const SweepView = lazy(() => import("./views/Sweep"));
const PerfView = lazy(() => import("./views/Perf"));

const NAV: [string, string][] = [
  ["runs", "Runs"],
  ["tests", "Tests"],
  ["waves", "Waves"],
  ["design", "Design"],
  ["xtrace", "X tracer"],
  ["synth", "Synthesis"],
  ["sweep", "Sweep"],
  ["perf", "Perf"],
];

function section(r: Route): string {
  const s = r.parts[0] ?? "runs";
  return s === "compare" ? "synth" : s;
}

function Header() {
  const { project, jobs, conn } = useApp();
  const running = jobs.filter((j) => j.status === "running").length;
  const connLabel = conn === "open" ? "live" : conn === "mock" ? "mock data" : conn === "connecting" ? "connecting" : "offline";
  return (
    <header className="header">
      <Logo />
      <div className="proj">
        <span className="proj-name" title={project?.root ?? undefined}>
          {project?.name ?? "…"}
        </span>
        {project && (
          <span className="git" title={project.git_sha ?? "not a git repository"}>
            {project.git_sha ? shortSha(project.git_sha) : "no git"}
            {project.git_dirty ? <span className="dirty"> (dirty)</span> : null}
          </span>
        )}
      </div>
      <div className="header-right">
        <ThemeSwitch />
        {project?.backends && <BackendChips backends={project.backends} />}
        <span className="live" title={running ? jobs.filter((j) => j.status === "running").map((j) => `${j.job_id} ${j.kind}`).join("\n") : "no running jobs"}>
          <span className={`dot ${running ? "busy" : ""}`} />
          <span className="lbl">{running ? `${running} job${running === 1 ? "" : "s"} running` : "idle"}</span>
        </span>
        <span className="live" title="event stream">
          <span className={`dot ${conn === "open" || conn === "mock" ? "on" : conn === "closed" ? "off" : ""}`} />
          <span className="lbl">{connLabel}</span>
        </span>
      </div>
    </header>
  );
}

/** "sim: verilator (vcs missing)" — shown only when a flow falls back to an open-source tool. */
export function BackendChips({ backends }: { backends: NonNullable<Project["backends"]> }) {
  const items = (["sim", "synth"] as const).flatMap((k) => {
    const b = backends[k];
    return b && b.note ? [{ k, b }] : [];
  });
  if (!items.length) return null;
  return (
    <>
      {items.map(({ k, b }) => (
        <span key={k} className="fallback-chip small" title={b.note ?? undefined}>
          {k}: {b.runs_on}&nbsp;
          <span className="muted">({b.missing?.length ? b.missing.join(", ") : b.configured ?? b.flow} not found)</span>
        </span>
      ))}
    </>
  );
}

const COLLAPSE_KEY = "svkit.sidebar.collapsed";

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function Sidebar({ active, collapsed, onToggle }: { active: string; collapsed: boolean; onToggle: () => void }) {
  return (
    <nav className="sidebar" aria-label="Views">
      <div className="nav">
        {NAV.map(([k, label], i) => (
          <a
            key={k}
            href={`#/${k}`}
            className={active === k ? "active" : ""}
            aria-current={active === k ? "page" : undefined}
            title={collapsed ? `${label} (${i + 1})` : undefined}
            aria-label={collapsed ? label : undefined}
          >
            <NavIcon name={k} />
            <span className="nav-label">{label}</span>
            <span className="key">{i + 1}</span>
          </a>
        ))}
      </div>
      <button
        type="button"
        className="nav-collapse"
        onClick={onToggle}
        aria-expanded={!collapsed}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        title={collapsed ? "Expand sidebar ( [ )" : "Collapse sidebar ( [ )"}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" />
          <path d="M5.5 2.5v11" />
          <path d={collapsed ? "M8.5 6l2 2-2 2" : "M10.5 6l-2 2 2 2"} />
        </svg>
        <span className="nav-label">Collapse</span>
      </button>
    </nav>
  );
}

function DevBar() {
  if (!isMock()) return null;
  const cur = mockDataset();
  const link = (d: string) => {
    const sp = new URLSearchParams(location.search);
    sp.set("mock", "1");
    if (d === "demo") sp.delete("data");
    else sp.set("data", d);
    return `${location.pathname}?${sp.toString()}${location.hash}`;
  };
  return (
    <div className="devbar" aria-label="Mock dataset (dev only)">
      {[
        ["demo", "Demo data"],
        ["worst", "Worst case"],
        ["empty", "Empty"],
      ].map(([d, l]) => (
        <a key={d} href={link(d)} className={cur === d ? "on" : ""}>
          {l}
        </a>
      ))}
    </div>
  );
}

function Content({ route }: { route: Route }) {
  const { projectError, reloadProject } = useApp();
  const [s, id] = route.parts;
  let view: ReactNode;
  switch (s ?? "runs") {
    case "runs":
      view = id ? <RunDetail id={id} /> : <RunsView />;
      break;
    case "tests":
      view = <TestsView suite={id} />;
      break;
    case "waves":
      view = <WavesView runId={id} query={route.query} />;
      break;
    case "design":
      view = <DesignView query={route.query} />;
      break;
    case "xtrace":
      view = <XTraceView query={route.query} />;
      break;
    case "synth":
      view = <SynthView id={id} />;
      break;
    case "compare":
      view = <CompareView ids={(route.query.get("ids") ?? "").split(",").filter(Boolean)} />;
      break;
    case "sweep":
      view = <SweepView gid={id} />;
      break;
    case "perf":
      view = <PerfView />;
      break;
    default:
      view = <Empty title={`No view called “${s}”`}>Pick one from the sidebar.</Empty>;
  }
  return (
    <main className="main">
      {projectError ? <ErrorState error={projectError} onRetry={reloadProject} /> : null}
      <Suspense fallback={<Loading />}>{view}</Suspense>
    </main>
  );
}

export function App() {
  const route = useRoute();
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const toggleNav = () =>
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      } catch {
        /* storage blocked: lasts for this page */
      }
      return !c;
    });
  const toggleRef = useRef(toggleNav);
  toggleRef.current = toggleNav;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.key === "[") {
        toggleRef.current();
        return;
      }
      const n = Number(e.key);
      if (n >= 1 && n <= NAV.length) navigate(`/${NAV[n - 1][0]}`);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <AppStateProvider>
      <div className={`app ${collapsed ? "nav-collapsed" : ""}`}>
        <Header />
        <Sidebar active={section(route)} collapsed={collapsed} onToggle={toggleNav} />
        <Content route={route} />
      </div>
      <DevBar />
    </AppStateProvider>
  );
}
