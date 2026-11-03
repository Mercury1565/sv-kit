import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { Job, Project } from "../api/types";
import { events, type ConnState } from "../api/ws";
import { useAsync, useServerEvents } from "./hooks";

interface AppState {
  project: Project | undefined;
  projectError: unknown;
  reloadProject: () => void;
  jobs: Job[];
  conn: ConnState;
}

const Ctx = createContext<AppState>({ project: undefined, projectError: null, reloadProject: () => {}, jobs: [], conn: "closed" });

export function AppStateProvider({ children }: { children: ReactNode }) {
  const proj = useAsync(() => api.project(), []);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [conn, setConn] = useState<ConnState>(events.state);

  useEffect(() => events.onState(setConn), []);
  useEffect(() => {
    api.jobs().then(setJobs, () => setJobs([]));
  }, []);
  useServerEvents((ev) => {
    if (ev.type === "job.updated") {
      setJobs((js) => {
        const i = js.findIndex((j) => j.job_id === ev.job.job_id);
        if (i < 0) return [...js, ev.job];
        const next = js.slice();
        next[i] = { ...next[i], ...ev.job };
        return next;
      });
    }
  });

  return (
    <Ctx.Provider value={{ project: proj.data, projectError: proj.error, reloadProject: proj.reload, jobs, conn }}>{children}</Ctx.Provider>
  );
}

export const useApp = () => useContext(Ctx);
