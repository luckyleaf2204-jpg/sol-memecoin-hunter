"use client";
// Dark Trading Terminal building blocks (UI Kit style). Pure presentation: every value shown
// comes from the caller (backend API data) — nothing here invents numbers.
import Link from "next/link";
import { ReactNode, useState } from "react";

export type IconName =
  | "overview" | "wallets" | "kol" | "radar" | "feed" | "alerts" | "backtest" | "settings" | "sun" | "moon"
  | "logout" | "activity" | "health" | "refresh" | "menu" | "close" | "copy" | "check" | "external" | "trophy"
  | "flow" | "coins" | "bell" | "pause" | "play" | "lab" | "search";

export function Icon({ name, size = 18, className = "" }: { name: IconName; size?: number; className?: string }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8,
    strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const, className };
  const p: Record<IconName, ReactNode> = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/></>,
    wallets: <><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H20v14H5.5A2.5 2.5 0 0 1 3 16.5z"/><path d="M3 8h14a3 3 0 0 1 3 3v2h-5a2 2 0 0 1 0-4h5"/></>,
    kol: <><circle cx="9" cy="8" r="3"/><path d="M3.5 20a5.5 5.5 0 0 1 11 0"/><path d="M16 5.5a3 3 0 0 1 0 5.8M17 14a5 5 0 0 1 3.5 4.8"/></>,
    radar: <><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/><path d="m12 12 6.5-6.5"/></>,
    feed: <><path d="M3 12h4l3-8 4 16 3-8h4"/></>,
    alerts: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></>,
    backtest: <><path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/></>,
    lab: <><path d="M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3"/><path d="M7 14h10"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M4.9 4.9l2.1 2.1m10 10 2.1 2.1M2 12h3m14 0h3M4.9 19.1 7 17m10-10 2.1-2.1"/></>,
    sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></>,
    moon: <path d="M20.5 14A8.5 8.5 0 0 1 10 3.5 8.5 8.5 0 1 0 20.5 14Z"/>,
    logout: <><path d="M10 17l5-5-5-5"/><path d="M15 12H3"/><path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6"/></>,
    activity: <><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/></>,
    health: <><path d="M20.8 11.2A8.8 8.8 0 1 1 12 3"/><path d="M3 12h4l2-4 3 8 2-4h3"/></>,
    refresh: <><path d="M20 11A8 8 0 0 0 5.3 6.3L3 8.5"/><path d="M3 3.5v5h5"/><path d="M4 13a8 8 0 0 0 14.7 4.7l2.3-2.2"/><path d="M21 20.5v-5h-5"/></>,
    menu: <><path d="M4 6h16M4 12h16M4 18h16"/></>,
    close: <><path d="M6 6l12 12M18 6 6 18"/></>,
    copy: <><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></>,
    check: <path d="m5 12 5 5L20 7"/>,
    external: <><path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></>,
    trophy: <><path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/></>,
    flow: <><path d="M4 7h13l-3-3M20 17H7l3 3"/></>,
    coins: <><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></>,
    pause: <><path d="M9 5v14M15 5v14"/></>,
    play: <path d="M7 5v14l12-7z"/>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
  };
  return <svg {...common}>{p[name]}</svg>;
}

export function Panel({ title, sub, icon, right, children, className = "", bodyClass = "" }:
  { title: ReactNode; sub?: ReactNode; icon?: IconName; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={`t-panel min-w-0 ${className}`}>
      <div className="t-panel-head">
        <div className="min-w-0">
          <div className="t-panel-title">{icon && <Icon name={icon} size={20} />}<span className="truncate">{title}</span></div>
          {sub && <div className="t-panel-sub mt-0.5 pl-[30px]">{sub}</div>}
        </div>
        {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
      </div>
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

export function Kpi({ icon, label, value, sub, tone = "teal", loading }:
  { icon: IconName; label: string; value: ReactNode; sub?: ReactNode; tone?: "teal" | "green" | "amber" | "rose"; loading?: boolean }) {
  const bg = { teal: "#0f3a3c", green: "#0f3b2a", amber: "#3d2f10", rose: "#40182a" }[tone];
  const fg = { teal: "#5fe0d1", green: "#39d6a2", amber: "#f2b84b", rose: "#f0556e" }[tone];
  return (
    <div className="t-panel t-kpi">
      <div className="t-kpi-icon" style={{ background: bg, color: fg }}><Icon name={icon} size={22} /></div>
      <div className="min-w-0">
        <div className="t-kpi-label truncate">{label}</div>
        {loading ? <div className="t-skel mt-1 h-7 w-24" /> : <div className="t-kpi-value truncate">{value}</div>}
        {sub && <div className="t-kpi-sub truncate">{sub}</div>}
      </div>
    </div>
  );
}

export function Seg<T extends string | number>({ value, options, onChange }: { value: T; options: { v: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="t-seg" role="tablist">
      {options.map((o) => (
        <button key={String(o.v)} role="tab" aria-selected={o.v === value} className={o.v === value ? "is-on" : ""} onClick={() => onChange(o.v)}>{o.label}</button>
      ))}
    </div>
  );
}

export function SkelRows({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2 px-4 pb-4">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-3">{Array.from({ length: cols }).map((__, j) => <div key={j} className="t-skel h-4 flex-1" />)}</div>
      ))}
    </div>
  );
}

/** Empty / error state with the REASON (no data is never silent). */
export function EmptyState({ title, reason, tone = "muted" }: { title: string; reason?: ReactNode; tone?: "muted" | "error" }) {
  return (
    <div className="px-4 pb-6 pt-3 text-center">
      <div className={`text-sm font-semibold ${tone === "error" ? "t-neg" : ""}`} style={tone === "error" ? undefined : { color: "var(--text)" }}>{title}</div>
      {reason && <div className="t-muted mx-auto mt-1 max-w-md text-xs leading-relaxed">{reason}</div>}
    </div>
  );
}

export function SidePill({ side }: { side: string }) {
  return side === "buy" ? <span className="t-pill t-buy">Mua</span> : <span className="t-pill t-sell">Bán</span>;
}

export function ShortAddr({ a, href, n = 4 }: { a: string; href?: string; n?: number }) {
  const [ok, setOk] = useState(false);
  const text = `${a.slice(0, n)}…${a.slice(-n)}`;
  async function copy(e: React.MouseEvent) {
    e.preventDefault(); e.stopPropagation();
    try { await navigator.clipboard.writeText(a); setOk(true); setTimeout(() => setOk(false), 1200); } catch {}
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      {href ? <Link href={href} className="mono text-[12px] hover:underline" style={{ color: "var(--text)" }}>{text}</Link>
        : <span className="mono text-[12px]">{text}</span>}
      <button onClick={copy} aria-label="Chép địa chỉ" className="t-faint hover:opacity-80"><Icon name={ok ? "check" : "copy"} size={13} /></button>
    </span>
  );
}

const AV = ["#0f766e", "#7c3aed", "#b45309", "#be185d", "#1d4ed8", "#15803d", "#9333ea", "#c2410c"];
export function TokenAvatar({ seed, label }: { seed: string; label?: string | null }) {
  let h = 0;
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const t = (label || seed).replace(/^\$/, "").slice(0, 2).toUpperCase();
  return <span className="t-avatar" style={{ background: AV[h % AV.length] }}>{t}</span>;
}

export function TokenCell({ mint, symbol, name }: { mint: string; symbol?: string | null; name?: string | null }) {
  return (
    <Link href={`/tokens/${mint}`} className="flex min-w-0 items-center gap-2.5">
      <TokenAvatar seed={mint} label={symbol || name} />
      <span className="min-w-0">
        <span className="block truncate font-bold" style={{ color: "var(--text)" }}>{symbol ? `$${symbol}` : `${mint.slice(0, 4)}…${mint.slice(-4)}`}</span>
        <span className="t-faint block max-w-[140px] truncate text-[11px]">{name || "chưa có metadata"}</span>
      </span>
    </Link>
  );
}

export function PanelLink({ href, children = "Xem tất cả" }: { href: string; children?: ReactNode }) {
  return <Link href={href} className="text-[11px] font-bold" style={{ color: "var(--accent-2)" }}>{children} →</Link>;
}
