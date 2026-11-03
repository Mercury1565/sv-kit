import { useCallback, useEffect, useRef, useState } from "react";
import { events } from "../api/ws";
import type { ServerEvent } from "../api/types";

export interface Async<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
  setData: (fn: (d: T | undefined) => T | undefined) => void;
}

/** Run an async loader when deps change; stale responses are dropped. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): Async<T> {
  const [data, setDataState] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  useEffect(() => {
    const my = ++seq.current;
    setLoading(true);
    setError(null);
    fn().then(
      (d) => {
        if (my !== seq.current) return;
        setDataState(d);
        setLoading(false);
      },
      (e) => {
        if (my !== seq.current) return;
        setError(e);
        setLoading(false);
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setData = useCallback((f: (d: T | undefined) => T | undefined) => setDataState((d) => f(d)), []);
  return { data, error, loading, reload, setData };
}

/** Subscribe to server events for the lifetime of the component. */
export function useServerEvents(fn: (ev: ServerEvent) => void): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => events.on((ev) => ref.current(ev)), []);
}

/** Element size via ResizeObserver (CSS px). */
export function useSize<T extends HTMLElement>(): [React.RefObject<T>, { width: number; height: number }] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setSize({ width: el.clientWidth, height: el.clientHeight });
    update();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
