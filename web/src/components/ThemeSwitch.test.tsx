import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeSwitch } from "./ThemeSwitch";
import { applyTheme, getTheme, isDark, onThemeChange } from "../lib/theme";

afterEach(() => {
  localStorage.clear();
  applyTheme("system");
});

describe("theme switch", () => {
  it("cycles Auto -> Light -> Dark -> Auto, sets <html data-theme> and remembers the choice", () => {
    let changes = 0;
    const off = onThemeChange(() => changes++);
    render(<ThemeSwitch />);
    const b = screen.getByRole("button");
    expect(b.textContent).toContain("Auto");
    fireEvent.click(b);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(getTheme()).toBe("light");
    expect(isDark()).toBe(false);
    fireEvent.click(b);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(isDark()).toBe(true);
    expect(b.textContent).toContain("Dark");
    fireEvent.click(b);
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(getTheme()).toBe("system");
    expect(changes).toBe(3);
    off();
  });
});
