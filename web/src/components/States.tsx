import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="loading">{what}…</div>;
}

export function Empty({ title, cmd, children }: { title: string; cmd?: string | string[]; children?: ReactNode }) {
  const cmds = cmd === undefined ? [] : Array.isArray(cmd) ? cmd : [cmd];
  return (
    <div className="state" role="status">
      <h2>{title}</h2>
      {children}
      {cmds.length > 0 && <pre>{cmds.map((c) => `$ ${c}`).join("\n")}</pre>}
    </div>
  );
}

/**
 * Error state. 404/501 on a feature route means the backend build does not ship that
 * feature yet (several come from parallel tracks), so say that instead of "error".
 */
export function ErrorState({ error, feature, onRetry }: { error: unknown; feature?: string; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  if (e && e.notAvailable && feature) {
    return (
      <div className="state" role="status">
        <h2>{feature} isn’t available here yet</h2>
        <div className="small">
          The server answered <code>{e.status}</code> for <code>{e.path}</code>
          {e.detail && e.detail !== "Not Found" ? <>: {e.detail}</> : null}. This view lights up once the backend provides it.
        </div>
        {onRetry && <button onClick={onRetry}>Retry</button>}
      </div>
    );
  }
  const msg = e ? `${e.status}: ${e.detail}` : error instanceof Error ? error.message : String(error);
  return (
    <div className="state error" role="alert">
      <h2>Something went wrong</h2>
      <div className="small anywhere">{msg}</div>
      {onRetry && <button onClick={onRetry}>Retry</button>}
    </div>
  );
}
