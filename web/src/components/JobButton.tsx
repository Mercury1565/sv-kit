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
  const error = job?.status === "error" ? job.error ?? "error" : err;
  return (
    <span className="row job-status" style={{ gap: 6 }}>
      <button className={primary ? "primary" : ""} onClick={go} disabled={disabled || running}>
        {children}
      </button>
      {job && job.status !== "error" && (
        <span className="small muted ellipsis" style={{ maxWidth: 280 }}>
          {job.status === "running" ? `running ${job.job_id}…` : `done${job.run_ids.length ? ` → ${job.run_ids.join(", ")}` : ""}`}
        </span>
      )}
      {error && <JobError text={error} />}
    </span>
  );
}

/** One-line summary of a job error; the full multi-line text (tool hint, log tail) opens below it. */
export function JobError({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const first = text.split("\n", 1)[0];
  const more = text.includes("\n") || first.length > 60;
  return (
    <span className="job-error">
      <span className="small worse ellipsis" style={{ maxWidth: 360 }} title={first}>
        failed: {first}
      </span>
      {more && (
        <button className="link small" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "hide" : "details"}
        </button>
      )}
      {open && (
        <pre className="job-error-full mono small" role="status">
          {text}
        </pre>
      )}
    </span>
  );
}
