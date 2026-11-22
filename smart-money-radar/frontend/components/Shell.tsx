"use client";
// App frame from the Smart Money Radar UI Kit (dark trading terminal), extended with the
// Live Feed / Data Health routes, a real connection status, last-update time, refresh button
// and a mobile drawer. Every status shown comes from /api/overview and /api/system/status.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { api, getLastUpdated, refreshAll, useApi } from "@/lib/api";
import { Icon, IconName } from "@/components/terminal";

const NAV: { href: string; label: string; short: string; icon: IconName; sub: string }[] = [
  { href: "/", label: "Overview", short: "Home", icon: "overview", sub: "Tổng quan thị trường và dòng tiền Smart Money" },
  { href: "/wallets", label: "Top Wallets", short: "Wallets", icon: "wallets", sub: "Xếp hạng ví theo dữ liệu on-chain đã xác minh" },
  { href: "/kol", label: "KOL Tracker", short: "KOL", icon: "kol", sub: "Ví KOL / người gọi kèo đang theo dõi" },
  { href: "/radar", label: "Token Radar", short: "Radar", icon: "radar", sub: "Token đang được các ví theo dõi giao dịch" },
  { href: "/live", label: "Live Feed", short: "Live", icon: "feed", sub: "Giao dịch mới nhất hệ thống ghi nhận" },
  { href: "/alerts", label: "Cảnh báo", short: "Alerts", icon: "alerts", sub: "Cảnh báo trong web và Telegram" },
  { href: "/backtest", label: "Backtest / Research", short: "Backtest", icon: "lab", sub: "Kiểm định chiến lược trên dữ liệu lịch sử" },
  { href: "/health", label: "Data Health", short: "Health", icon: "health", sub: "Worker, nguồn dữ liệu, backfill và độ phủ" },
  { href: "/settings", label: "Cài đặt", short: "Settings", icon: "settings", sub: "Tiêu chí, cảnh báo, Telegram" },
];
const MOBILE = ["/", "/wallets", "/kol", "/live", "/alerts"];

function useTheme() {
  const [dark, setDark] = useState(true);
  useEffect(() => {
    let saved: string | null = null;
    try { saved = localStorage.getItem("theme"); } catch {}
    const on = saved ? saved === "dark" : true;
    setDark(on);
    document.documentElement.classList.toggle("dark", on);
  }, []);
  const toggle = () => {
    const on = !dark;
    setDark(on);
    document.documentElement.classList.toggle("dark", on);
    try { localStorage.setItem("theme", on ? "dark" : "light"); } catch {}
  };
  return { dark, toggle };
}

function useLastUpdated() {
  const [t, setT] = useState(0);
  useEffect(() => {
    const h = () => setT(getLastUpdated());
    h();
    window.addEventListener("radar:updated", h);
    return () => window.removeEventListener("radar:updated", h);
  }, []);
  return t;
}

/** Stream status from the worker's own report (api_sync_status 'solana_ws'). */
function streamState(ov: any): { cls: "ok" | "warn" | "bad"; text: string } {
  const st = ov?.stream;
  if (!st) return { cls: "warn", text: "Stream: chưa có báo cáo" };
  const age = st.details?.last_message_age_s;
  const updatedAge = st.updated_at ? (Date.now() - new Date(st.updated_at).getTime()) / 1000 : 1e9;
  const ep = String(st.details?.endpoint || "");
  const src = ep.includes("helius") ? "Helius" : ep ? "RPC công khai" : "RPC";
  if (updatedAge > 120 || ["error", "failed", "down"].includes(st.status)) return { cls: "bad", text: `${src}: mất kết nối` };
  if (st.status !== "ok") return { cls: "warn", text: `${src}: chập chờn (${st.status})` };
  if (age != null && age > 30) return { cls: "warn", text: `${src}: trễ ${Math.round(age)} giây` };
  return { cls: "ok", text: `${src}: Connected` };
}

const WORKERS: [string, string][] = [["stream", "Stream"], ["verifier", "Verifier"], ["sync", "Wallet sync"], ["discovery", "Discovery"], ["analytics", "Analytics"], ["signals", "Signals"]];

function SystemBox() {
  const st = useApi<any>("/api/system/status", 30000);
  const beats = new Map<string, number>((st.data?.workers || []).map((w: any) => [w.worker, (Date.now() - new Date(w.last_beat).getTime()) / 1000]));
  const ws = (st.data?.sources || []).find((x: any) => x.source === "solana_ws");
  const wsAge = ws?.updated_at ? (Date.now() - new Date(ws.updated_at).getTime()) / 1000 : undefined;
  const state = (age: number | undefined) => (age == null ? "Unknown" : age < 180 ? "Online" : age < 900 ? "Delayed" : "Error");
  // Windows / single-process mode reports one heartbeat "all" for every role
  // the mode whose heartbeat is newest is the one running; a leftover "all" row from an earlier
  // single-process run must not show as an error once the per-role containers are up (and vice versa)
  const roleAges = WORKERS.map(([k]) => beats.get(k)).filter((x): x is number => x != null);
  const singleMode = beats.has("all") && (!roleAges.length || (beats.get("all") as number) < Math.min(...roleAges));
  const rows: { label: string; s: string }[] = singleMode
    ? [{ label: "Workers (1 tiến trình)", s: state(beats.get("all")) }, { label: "Stream on-chain", s: !ws ? "Unknown" : ws.status === "ok" ? state(wsAge) : (wsAge ?? 1e9) > 900 ? "Error" : "Delayed" }]
    : [...WORKERS.map(([k, label]) => ({ label, s: state(beats.get(k)) }))];
  rows.push({ label: "API · Database", s: st.error ? "Error" : st.data ? "Online" : "Unknown" });
  const allOk = rows.every((x) => x.s === "Online");
  return (
    <div className="radar-health">
      <div className="radar-health-title" style={allOk ? undefined : { color: "var(--warn)" }}>
        <span className="radar-dot" style={allOk ? undefined : { background: "var(--warn)", boxShadow: "none" }} />
        {st.data ? (allOk ? "Hệ thống đang chạy" : "Có thành phần cần kiểm tra") : "Đang kiểm tra…"}
      </div>
      {rows.map((x) => (
        <div key={x.label} className="radar-health-row"><span>{x.label}</span>
          <span style={{ color: x.s === "Online" ? "var(--buy)" : x.s === "Delayed" ? "var(--warn)" : x.s === "Error" ? "var(--sell)" : "var(--faint)" }}>{st.data ? x.s : "…"}</span></div>
      ))}
      <Link href="/health" className="mt-1 block text-[10px] font-bold" style={{ color: "var(--accent-2)" }}>Chi tiết Data Health →</Link>
    </div>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { dark, toggle } = useTheme();
  const [drawer, setDrawer] = useState(false);
  const ov = useApi<any>(path === "/login" ? null : "/api/overview", 30000, ["alert"]);
  const updated = useLastUpdated();
  useEffect(() => setDrawer(false), [path]);
  if (path === "/login") return <>{children}</>;
  const active = (h: string) => (h === "/" ? path === "/" : path.startsWith(h));
  const cur = NAV.find((n) => active(n.href)) || (path.startsWith("/tokens") ? { label: "Token", sub: "Chi tiết token" } : { label: "Smart Money Radar", sub: "" });
  const unseen = ov.data?.unseen_alerts ?? 0;
  const ss = streamState(ov.data);
  const logout = async () => { await api("/api/auth/logout", { method: "POST" }); location.href = "/login"; };
  const navList = (
    <nav className="radar-nav">
      {NAV.map((item) => (
        <Link key={item.href} href={item.href} className={`radar-nav-link ${active(item.href) ? "is-active" : ""}`}>
          <Icon name={item.icon} /><span>{item.label}</span>
          {item.href === "/alerts" && unseen > 0 && <b className="radar-nav-count">{unseen}</b>}
        </Link>
      ))}
    </nav>
  );

  return (
    <div className="radar-app">
      <aside className="radar-sidebar">
        <Link href="/" className="radar-brand">
          <span className="radar-brand-mark"><Icon name="activity" size={21} /></span>
          <span><strong>SMART MONEY <span style={{ color: "var(--buy)" }}>RADAR</span></strong><small>SOLANA · PUMP.FUN</small></span>
        </Link>
        <div className="radar-nav-label">WORKSPACE</div>
        {navList}
        <div className="radar-sidebar-bottom">
          <SystemBox />
          <div className="radar-readonly"><span className="radar-dot" /> ON-CHAIN MONITORING <small>READ ONLY · NO AUTO TRADING</small></div>
          <button className="radar-utility" onClick={toggle}><Icon name={dark ? "sun" : "moon"} />{dark ? "Giao diện sáng" : "Giao diện tối"}</button>
          <button className="radar-utility" onClick={logout}><Icon name="logout" />Đăng xuất</button>
        </div>
      </aside>

      <div className="radar-main">
        <header className="radar-topbar">
          <div className="min-w-0">
            <div className="truncate text-[17px] font-extrabold" style={{ color: "var(--text)" }}>{cur.label}</div>
            {cur.sub && <div className="radar-status-text truncate text-[11px]" style={{ color: "var(--muted)" }}>{cur.sub}</div>}
          </div>
          <div className="radar-topbar-right">
            <span className="radar-status radar-status-text" style={{ color: "var(--muted)" }}>Solana</span>
            <span className={`radar-status ${ss.cls}`} title={ov.data?.stream?.details ? `Nguồn: ${ov.data.stream.details.endpoint}` : undefined}>
              <span className="radar-dot" /><span className="radar-status-text">{ss.text}</span>
            </span>
            <span className="radar-status-text num text-[11px]" style={{ color: "var(--muted)" }}>
              Cập nhật: {updated ? new Date(updated).toLocaleTimeString("vi-VN", { hour12: false }) : "—"}
            </span>
            <button className="radar-icon-button" onClick={refreshAll} aria-label="Làm mới dữ liệu" title="Làm mới dữ liệu"><Icon name="refresh" /></button>
            <button className="radar-icon-button radar-status-text" onClick={toggle} aria-label="Đổi giao diện"><Icon name={dark ? "sun" : "moon"} /></button>
            <button className="radar-icon-button radar-menu-button" onClick={() => setDrawer(true)} aria-label="Mở menu"><Icon name="menu" /></button>
          </div>
        </header>
        <main className="radar-content">{children}</main>
        <footer className="radar-footer"><span>SMART MONEY RADAR</span><span>Solana · Pump.fun · Analytics only · không tự giao dịch</span></footer>
      </div>

      <nav className="radar-mobile-nav">
        {NAV.filter((n) => MOBILE.includes(n.href)).map((item) => (
          <Link key={item.href} href={item.href} className={active(item.href) ? "is-active" : ""}>
            <Icon name={item.icon} size={19} /><span>{item.short}</span>
            {item.href === "/alerts" && unseen > 0 && <b>{unseen}</b>}
          </Link>
        ))}
      </nav>

      {drawer && (
        <div className="radar-drawer" role="dialog" aria-modal="true">
          <div className="radar-drawer-back" onClick={() => setDrawer(false)} />
          <div className="radar-drawer-panel">
            <div className="mb-4 flex items-center justify-between">
              <span className="text-xs font-extrabold tracking-widest" style={{ color: "var(--faint)" }}>MENU</span>
              <button className="radar-icon-button" onClick={() => setDrawer(false)} aria-label="Đóng menu"><Icon name="close" /></button>
            </div>
            {navList}
            <div className="mt-4"><SystemBox /></div>
            <button className="radar-utility w-full" onClick={toggle}><Icon name={dark ? "sun" : "moon"} />{dark ? "Giao diện sáng" : "Giao diện tối"}</button>
            <button className="radar-utility w-full" onClick={logout}><Icon name="logout" />Đăng xuất</button>
          </div>
        </div>
      )}
    </div>
  );
}
