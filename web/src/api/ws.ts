// /ws/events client with auto-reconnect (exponential backoff, capped).
// In mock mode the mock layer pushes events through `events.emit` instead of a socket.
import type { ServerEvent } from "./types";

export type ConnState = "connecting" | "open" | "closed" | "mock";
type Listener = (ev: ServerEvent) => void;
type StateListener = (s: ConnState) => void;

export function parseEvent(raw: unknown): ServerEvent | null {
  let v = raw;
  if (typeof raw === "string") {
    try {
      v = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!v || typeof v !== "object" || typeof (v as { type?: unknown }).type !== "string") return null;
  const t = (v as { type: string }).type;
  if (t === "run.created" || t === "run.status" || t === "run.updated" || t === "run.log" || t === "group.updated" || t === "job.updated") {
    return v as ServerEvent;
  }
  return null;
}

class EventStream {
  private listeners = new Set<Listener>();
  private stateListeners = new Set<StateListener>();
  private ws: WebSocket | null = null;
  private retry = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  state: ConnState = "closed";

  on(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onState(fn: StateListener): () => void {
    this.stateListeners.add(fn);
    fn(this.state);
    return () => this.stateListeners.delete(fn);
  }

  emit(ev: ServerEvent): void {
    for (const fn of this.listeners) {
      try {
        fn(ev);
      } catch (e) {
        console.error("svkit event listener failed", e);
      }
    }
  }

  private setState(s: ConnState) {
    this.state = s;
    for (const fn of this.stateListeners) fn(s);
  }

  useMock(): void {
    this.stop();
    this.setState("mock");
  }

  connect(url?: string): void {
    this.stopped = false;
    const target = url ?? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/events`;
    this.setState("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(target);
    } catch {
      this.schedule(target);
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.setState("open");
    };
    ws.onmessage = (m) => {
      const ev = parseEvent(m.data);
      if (ev) this.emit(ev);
    };
    ws.onclose = () => {
      this.ws = null;
      if (!this.stopped) this.schedule(target);
    };
    ws.onerror = () => ws.close();
  }

  private schedule(target: string) {
    this.setState("closed");
    const delay = Math.min(10000, 500 * 2 ** this.retry++);
    this.timer = setTimeout(() => this.connect(target), delay);
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.ws?.close();
    this.ws = null;
    this.setState("closed");
  }
}

export const events = new EventStream();
