"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "content-type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
    ...init,
  });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth")) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = `/login?next=${next}`;
    throw new ApiError(401, "authentication required");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const b = await res.json();
      msg = b.detail || msg;
    } catch {}
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

type Listener = (ev: { type: string; data: any }) => void;
const listeners = new Set<Listener>();
let socket: WebSocket | null = null;

function ensureSocket() {
  if (typeof window === "undefined" || socket) return;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = process.env.NEXT_PUBLIC_WS_URL || `${proto}://${window.location.host}/api/ws`;
  try {
    socket = new WebSocket(url);
  } catch {
    socket = null;
    return;
  }
  socket.onmessage = (m) => {
    try {
      const ev = JSON.parse(m.data);
      if (ev.type !== "ping") listeners.forEach((l) => l(ev));
    } catch {}
  };
  socket.onclose = () => {
    socket = null;
    setTimeout(ensureSocket, 5000);
  };
}

export function useLiveEvents(fn: Listener) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    ensureSocket();
    const l: Listener = (e) => ref.current(e);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
}

/** Fetch + periodic refresh + refresh on live events (signals, alerts, rankings). */
export function useApi<T = any>(path: string | null, refreshMs = 30000, liveTypes: string[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      setData(d);
      setError(null);
    } catch (e: any) {
      if (e?.status !== 401) setError(e?.message || "error");
    } finally {
      setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    setLoading(true);
    load();
    if (!refreshMs) return;
    const t = setInterval(load, refreshMs);
    return () => clearInterval(t);
  }, [load, refreshMs]);
  useLiveEvents((ev) => {
    if (liveTypes.includes(ev.type)) load();
  });
  return { data, error, loading, reload: load };
}
