"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";

const NAV = [
  { href: "/", label: "Tổng quan", icon: "◎" },
  { href: "/wallets", label: "Top Wallets", icon: "★" },
  { href: "/radar", label: "Token Radar", icon: "◉" },
  { href: "/alerts", label: "Cảnh báo", icon: "!" },
  { href: "/settings", label: "Cài đặt", icon: "⚙" },
];

function useTheme() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    let saved: string | null = null;
    try { saved = localStorage.getItem("theme"); } catch {}
    const d = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
  }, []);
  const toggle = () => {
    const d = !dark;
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
    try { localStorage.setItem("theme", d ? "dark" : "light"); } catch {}
  };
  return { dark, toggle };
}

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { dark, toggle } = useTheme();
  const unseen = useApi<{ unseen_alerts: number }>(path === "/login" ? null : "/api/overview", 60000, ["alert"]);
  if (path === "/login") return <>{children}</>;
  const active = (h: string) => (h === "/" ? path === "/" : path.startsWith(h));
  const badge = unseen.data?.unseen_alerts || 0;
  return (
    <div className="min-h-screen md:flex">
      <aside className="hidden w-56 shrink-0 border-r border-slate-200 bg-white px-3 py-5 md:block dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-6 px-2">
          <div className="text-base font-bold text-brand dark:text-teal-300">SMART MONEY RADAR</div>
          <div className="text-[11px] text-slate-500">Pump.fun · Solana · chỉ đọc dữ liệu</div>
        </div>
        <nav className="space-y-1">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href}
              className={`flex items-center justify-between rounded-lg px-3 py-2 text-sm ${active(n.href) ? "bg-brand text-white" : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
              <span><span className="mr-2 inline-block w-4 text-center">{n.icon}</span>{n.label}</span>
              {n.href === "/alerts" && badge > 0 && <span className="rounded-full bg-rose-600 px-1.5 text-[10px] text-white">{badge}</span>}
            </Link>
          ))}
          <Link href="/backtest" className={`block rounded-lg px-3 py-2 text-sm ${active("/backtest") ? "bg-brand text-white" : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
            <span className="mr-2 inline-block w-4 text-center">↺</span>Backtest
          </Link>
        </nav>
        <div className="mt-8 space-y-2 px-2 text-xs">
          <button onClick={toggle} className="text-slate-500 underline">{dark ? "Giao diện sáng" : "Dark Mode"}</button>
          <br />
          <button onClick={async () => { await api("/api/auth/logout", { method: "POST" }); location.href = "/login"; }} className="text-slate-500 underline">Đăng xuất</button>
        </div>
      </aside>

      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-slate-200 bg-white/90 px-4 pb-2 pt-[max(0.5rem,env(safe-area-inset-top))] backdrop-blur md:hidden dark:border-slate-800 dark:bg-slate-900/90">
        <div className="text-sm font-bold text-brand dark:text-teal-300">SMART MONEY RADAR</div>
        <div className="flex gap-3 text-xs text-slate-500">
          <Link href="/backtest" className="underline">Backtest</Link>
          <button onClick={toggle} aria-label="Dark mode">{dark ? "☀" : "☾"}</button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 px-3 pb-28 pt-3 md:px-6 md:pb-10 md:pt-6">{children}</main>

      <nav className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-slate-200 bg-white/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden dark:border-slate-800 dark:bg-slate-900/95">
        {NAV.map((n) => (
          <Link key={n.href} href={n.href} className={`relative flex flex-col items-center py-2 text-[10px] ${active(n.href) ? "text-brand dark:text-teal-300" : "text-slate-500"}`}>
            <span className="text-lg leading-5">{n.icon}</span>
            {n.label}
            {n.href === "/alerts" && badge > 0 && <span className="absolute right-4 top-1 rounded-full bg-rose-600 px-1 text-[9px] text-white">{badge}</span>}
          </Link>
        ))}
      </nav>
    </div>
  );
}
