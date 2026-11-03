import { useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import type { JobKind } from "../api/types";
import { useApp } from "../lib/appState";

/** Launches a job via POST /api/jobs and shows its live status (from job.updated events). */
export function JobButton({ kind, args, children, primary, disabled }: { kind: JobKind; args?: Record<string, unknown>; children: ReactNode; primary?: boolean; disabled?: boolean }) {
  const { jobs } = useApp();
  const [jobId, setJobId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const job = jobs.find((j) => j.job_id === jobId);

  const go = async () => {
    setErr(null);
    setBusy(true);
    try {
      const r = await api.submitJob(kind, args ?? {});
      setJobId(r.job_id);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  };

  const running = busy || job?.status === "running";
  return (
    <span className="row" style={{ gap: 6 }}>
      <button className={primary ? "primary" : ""} onClick={go} disabled={disabled || running}>
        {children}
      </button>
      {job && (
        <span className={`small ${job.status === "error" ? "worse" : "muted"} ellipsis`} style={{ maxWidth: 280 }} title={job.error ?? undefined}>
          {job.status === "running" ? `running ${job.job_id}…` : job.status === "error" ? `failed: ${job.error ?? "error"}` : `done${job.run_ids.length ? ` → ${job.run_ids.join(", ")}` : ""}`}
        </span>
      )}
      {err && (
        <span className="small worse ellipsis" style={{ maxWidth: 280 }} title={err}>
          {err}
        </span>
      )}
    </span>
  );
}
