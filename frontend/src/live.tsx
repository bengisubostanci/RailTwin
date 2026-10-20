import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Snapshot, type Topology } from "./api";

const HISTORY_POINTS = 600; // saniyede bir anlık görüntüyle ~10 dakika

export interface HistoryPoint {
  t: number;
  health: number;
  metrics: Record<string, number>;
}

interface LiveState {
  snapshot: Snapshot | null;
  topology: Topology | null;
  connected: boolean;
  /** Gösterge paneli açıkken toplanan, varlık başına geçmiş. */
  history: Record<string, HistoryPoint[]>;
}

const LiveContext = createContext<LiveState>({ snapshot: null, topology: null, connected: false, history: {} });

export function LiveProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [topology, setTopology] = useState<Topology | null>(null);
  const [connected, setConnected] = useState(false);
  const historyRef = useRef<Record<string, HistoryPoint[]>>({});
  const [history, setHistory] = useState<Record<string, HistoryPoint[]>>({});

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api.topology().then(
        (t) => !cancelled && setTopology(t),
        () => !cancelled && setTimeout(load, 2000),
      );
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout>;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 2000);
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type !== "snapshot") return;
        const snap: Snapshot = msg.data;
        setSnapshot(snap);
        const next = { ...historyRef.current };
        for (const a of Object.values(snap.assets)) {
          const points = next[a.id] ? next[a.id].slice(-HISTORY_POINTS + 1) : [];
          points.push({ t: snap.ts * 1000, health: a.health, metrics: a.metrics });
          next[a.id] = points;
        }
        historyRef.current = next;
        setHistory(next);
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, []);

  return <LiveContext.Provider value={{ snapshot, topology, connected, history }}>{children}</LiveContext.Provider>;
}

export const useLive = () => useContext(LiveContext);
