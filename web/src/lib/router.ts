// Tiny hash router: #/runs, #/synth/r_0003, #/waves/r_0001?t=120&signals=a,b ...
import { useEffect, useState } from "react";

export interface Route {
  path: string; // "/waves/r_0001"
  parts: string[]; // ["waves", "r_0001"]
  query: URLSearchParams;
}

export function parseHash(hash: string): Route {
  const h = hash.replace(/^#/, "") || "/runs";
  const qi = h.indexOf("?");
  const path = qi >= 0 ? h.slice(0, qi) : h;
  const query = new URLSearchParams(qi >= 0 ? h.slice(qi + 1) : "");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { path: "/" + parts.join("/"), parts, query };
}

export function href(path: string, query?: Record<string, string | number | null | undefined | false>): string {
  let q = "";
  if (query) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== false && v !== "") p.set(k, String(v));
    const s = p.toString();
    if (s) q = "?" + s;
  }
  return "#" + path + q;
}

export function navigate(to: string, replace = false): void {
  const target = to.startsWith("#") ? to : "#" + to;
  if (replace) history.replaceState(null, "", location.pathname + location.search + target);
  else location.hash = target;
  if (replace) window.dispatchEvent(new HashChangeEvent("hashchange"));
}

/** Update the hash query without adding history entries or notifying listeners (e.g. wave cursor). */
export function replaceQuietly(to: string): void {
  history.replaceState(null, "", location.pathname + location.search + (to.startsWith("#") ? to : "#" + to));
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
