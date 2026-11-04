// Colour theme: "system" follows the OS (prefers-color-scheme); "light"/"dark" override it via
// <html data-theme>. The choice is a per-browser convenience kept in localStorage.
export type Theme = "system" | "light" | "dark";
export const THEMES: Theme[] = ["system", "light", "dark"];
const KEY = "svkit.theme";
const EVENT = "svkit-theme";

export function getTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(t: Theme = getTheme()) {
  const root = document.documentElement;
  if (t === "system") delete root.dataset.theme;
  else root.dataset.theme = t;
}

export function setTheme(t: Theme) {
  try {
    if (t === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, t);
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
  applyTheme(t);
  window.dispatchEvent(new Event(EVENT));
}

/** The theme in effect right now. */
export function isDark(): boolean {
  const t = document.documentElement.dataset.theme;
  if (t === "dark") return true;
  if (t === "light") return false;
  return !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

/** Call `cb` whenever the effective theme may have changed (OS setting or the in-app switch). */
export function onThemeChange(cb: () => void): () => void {
  const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
  mq?.addEventListener?.("change", cb);
  window.addEventListener(EVENT, cb);
  return () => {
    mq?.removeEventListener?.("change", cb);
    window.removeEventListener(EVENT, cb);
  };
}
