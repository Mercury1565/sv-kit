import { useEffect, useRef, useState, type MutableRefObject } from "react";
import type { ECharts, EChartsCoreOption } from "echarts/core";
import { onThemeChange } from "../lib/theme";

export interface ChartTheme {
  fg: string;
  muted: string;
  faint: string;
  border: string;
  bg: string;
  accent: string;
  good: string;
  bad: string;
  warn: string;
  mono: string;
}

export function readTheme(): ChartTheme {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d;
  return {
    fg: v("--fg", "#1b1f24"),
    muted: v("--fg-muted", "#5b6573"),
    faint: v("--fg-faint", "#8a94a3"),
    border: v("--border", "#e1e4e8"),
    bg: v("--bg-raised", "#fff"),
    accent: v("--accent", "#2f6fde"),
    good: v("--good", "#1a7f37"),
    bad: v("--bad", "#c62828"),
    warn: v("--warn", "#9a6700"),
    mono: v("--mono", "monospace"),
  };
}

/** Shared axis/tooltip styling so every chart reads as one system. */
export function baseOption(t: ChartTheme): EChartsCoreOption {
  return {
    animation: false,
    textStyle: { color: t.muted, fontSize: 11 },
    tooltip: { backgroundColor: t.bg, borderColor: t.border, textStyle: { color: t.fg, fontSize: 12 }, confine: true },
    grid: { left: 64, right: 24, top: 28, bottom: 36, containLabel: false },
  };
}

export function axisStyle(t: ChartTheme) {
  return {
    axisLine: { lineStyle: { color: t.border } },
    axisTick: { lineStyle: { color: t.border } },
    axisLabel: { color: t.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: t.border, type: "dashed" as const } },
    nameTextStyle: { color: t.muted, fontSize: 11 },
  };
}

interface Props {
  option: (t: ChartTheme) => EChartsCoreOption;
  height?: number;
  onClick?: (params: { dataIndex: number; seriesIndex: number; data: unknown; seriesName?: string }) => void;
  ariaLabel: string;
  /** receives the live chart instance (null once disposed), e.g. to save it as an image */
  chartRef?: MutableRefObject<ECharts | null>;
}

/**
 * The chart as a PNG data URL: every data point (any zoom window is opened to 0–100% for the shot,
 * then put back), no zoom slider, on the page's background color, at 2× for sharp text.
 */
export function chartPng(chart: ECharts): string {
  const opt = chart.getOption() as { dataZoom?: { start?: number; end?: number }[] };
  const zooms = (opt.dataZoom ?? []).map((z) => ({ start: z.start ?? 0, end: z.end ?? 100 }));
  if (zooms.length) chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
  try {
    return chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: readTheme().bg, excludeComponents: ["dataZoom", "toolbox"] });
  } finally {
    zooms.forEach((z, i) => chart.dispatchAction({ type: "dataZoom", dataZoomIndex: i, start: z.start, end: z.end }));
  }
}

/** Thin ECharts wrapper: lazy-loads echarts, follows the color scheme, resizes with its box. */
export function EChart({ option, height = 300, onClick, ariaLabel, chartRef }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<ECharts | null>(null);
  const [ready, setReady] = useState(false);
  const [scheme, setScheme] = useState(0);
  const clickRef = useRef(onClick);
  clickRef.current = onClick;

  useEffect(() => {
    let disposed = false;
    let ro: ResizeObserver | null = null;
    import("./charts").then(({ echarts }) => {
      if (disposed || !el.current) return;
      chart.current = echarts.init(el.current, undefined, { renderer: "canvas" });
      if (chartRef) chartRef.current = chart.current;
      chart.current.on("click", (p: any) => clickRef.current?.(p));
      if (typeof ResizeObserver !== "undefined") {
        ro = new ResizeObserver(() => chart.current?.resize());
        ro.observe(el.current);
      }
      setReady(true);
    });
    const offTheme = onThemeChange(() => setScheme((s) => s + 1));
    return () => {
      disposed = true;
      ro?.disconnect();
      offTheme();
      chart.current?.dispose();
      chart.current = null;
      if (chartRef) chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!ready || !chart.current) return;
    chart.current.setOption(option(readTheme()), { notMerge: true });
  }, [ready, option, scheme]);

  return <div ref={el} role="img" aria-label={ariaLabel} style={{ width: "100%", height }} />;
}
