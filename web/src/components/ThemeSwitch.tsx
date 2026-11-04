import { useState } from "react";
import { getTheme, setTheme, THEMES, type Theme } from "../lib/theme";

const LABEL: Record<Theme, string> = { system: "Auto", light: "Light", dark: "Dark" };
const ICON: Record<Theme, string> = { system: "◐", light: "☀", dark: "☾" };

/** Header control: cycles Auto (follow the OS) → Light → Dark. */
export function ThemeSwitch() {
  const [t, setT] = useState<Theme>(getTheme);
  const next = THEMES[(THEMES.indexOf(t) + 1) % THEMES.length];
  return (
    <button
      type="button"
      className="theme-switch"
      onClick={() => {
        setTheme(next);
        setT(next);
      }}
      title={`Theme: ${LABEL[t]}${t === "system" ? " (follows your system setting)" : ""}. Click for ${LABEL[next]}.`}
      aria-label={`Theme: ${LABEL[t]}. Switch to ${LABEL[next]}`}
    >
      <span aria-hidden="true">{ICON[t]}</span>
      <span className="lbl">{LABEL[t]}</span>
    </button>
  );
}
