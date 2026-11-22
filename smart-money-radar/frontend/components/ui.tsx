"use client";
import Link from "next/link";
import { ReactNode, useState } from "react";

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}>
      {(title || right) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "pos" | "neg" | "neutral" }) {
  const color = tone === "pos" ? "text-emerald-600 dark:text-emerald-400" : tone === "neg" ? "text-rose-600 dark:text-rose-400" : "";
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{sub}</div>}
    </div>
  );
}

const LEVEL_STYLE: Record<string, string> = {
  VERY_STRONG: "bg-rose-600 text-white",
  STRONG: "bg-orange-500 text-white",
  WATCH: "bg-amber-200 text-amber-900 dark:bg-amber-500/30 dark:text-amber-200",
  EXIT50: "bg-violet-200 text-violet-900 dark:bg-violet-500/30 dark:text-violet-200",
  EXIT90: "bg-violet-600 text-white",
};
const LEVEL_TEXT: Record<string, string> = { EXIT50: "EXIT ≥ 50%", EXIT90: "EXIT ≥ 90%" };
const RISK_STYLE: Record<string, string> = {
  LOW: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300",
  MEDIUM: "bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300",
  HIGH: "bg-rose-100 text-rose-800 dark:bg-rose-500/20 dark:text-rose-300",
  UNKNOWN: "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300",
};
const CONF_STYLE: Record<string, string> = {
  HIGH: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300",
  MEDIUM: "bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300",
  LOW: "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300",
};
const VERIF_STYLE: Record<string, string> = {
  verified: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300",
  partially_verified: "bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300",
  unverified: "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300",
};
const CATEGORY_LABEL: Record<string, string> = {
  EXCELLENT: "Excellent", STRONG: "Strong", WATCHLIST: "Watchlist", NOT_PRIORITY: "Không ưu tiên",
};
const VERIF_LABEL: Record<string, string> = {
  verified: "Verified", partially_verified: "Partially Verified", unverified: "Unverified",
};

export function Pill({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${className}`}>{children}</span>;
}
export const LevelBadge = ({ level }: { level?: string | null }) =>
  level ? <Pill className={LEVEL_STYLE[level] || ""}>{LEVEL_TEXT[level] || level.replace("_", " ")}</Pill> : <span className="text-slate-400">—</span>;
export const RiskBadge = ({ level }: { level?: string | null }) =>
  <Pill className={RISK_STYLE[level || "UNKNOWN"]}>{level ? `${level} RISK` : "RISK ?"}</Pill>;
export const ConfBadge = ({ c }: { c?: string | null }) =>
  c ? <Pill className={CONF_STYLE[c]}>{`${c[0]}${c.slice(1).toLowerCase()} Confidence`}</Pill> : null;
export const VerifBadge = ({ v }: { v?: string | null }) =>
  v ? <Pill className={VERIF_STYLE[v] || ""}>{VERIF_LABEL[v] || v}</Pill> : null;
export const CategoryBadge = ({ c }: { c?: string | null }) =>
  c ? <Pill className="bg-brand/10 text-brand dark:bg-brand-light/20 dark:text-teal-300">{CATEGORY_LABEL[c] || c}</Pill> : null;

const BEHAVIOR: Record<string, [string, string]> = {
  HUMAN: ["Người", "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300"],
  LIKELY_BOT: ["Có dấu hiệu bot", "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300"],
  BOT: ["Bot", "bg-rose-100 text-rose-800 dark:bg-rose-900/50 dark:text-rose-300"],
  UNKNOWN: ["Chưa rõ người/bot", "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"],
};
export const BehaviorBadge = ({ b }: { b?: string | null }) =>
  b ? <Pill className={(BEHAVIOR[b] || BEHAVIOR.UNKNOWN)[1]}>{(BEHAVIOR[b] || BEHAVIOR.UNKNOWN)[0]}</Pill> : null;

export function Copy({ text, label }: { text: string; label?: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      type="button"
      onClick={async (e) => {
        e.preventDefault();
        e.stopPropagation();
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          const ta = document.createElement("textarea");
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        setOk(true);
        setTimeout(() => setOk(false), 1200);
      }}
      className="ml-1 inline-flex shrink-0 items-center rounded-md border border-slate-300 px-1.5 py-0.5 text-[11px] text-slate-600 active:scale-95 dark:border-slate-600 dark:text-slate-300"
      aria-label="Sao chép"
    >
      {ok ? "Đã chép" : label || "Chép"}
    </button>
  );
}

/** Full address (never a nickname), monospace, wraps on phones, with copy button. */
export function Address({ a, href, full = true }: { a: string; href?: string; full?: boolean }) {
  const text = full ? a : `${a.slice(0, 6)}…${a.slice(-6)}`;
  return (
    <span className="inline-flex max-w-full items-center whitespace-normal">
      {href ? (
        <Link href={href} className="break-all font-mono text-[12px] text-brand underline-offset-2 hover:underline dark:text-teal-300">{text}</Link>
      ) : (
        <span className="break-all font-mono text-[12px]">{text}</span>
      )}
      <Copy text={a} />
    </span>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange }: { value: T; options: { v: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-xl bg-slate-100 p-0.5 dark:bg-slate-800">
      {options.map((o) => (
        <button key={String(o.v)} onClick={() => onChange(o.v)}
          className={`rounded-lg px-3 py-1.5 text-xs font-medium ${value === o.v ? "bg-white shadow dark:bg-slate-600" : "text-slate-500 dark:text-slate-400"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Loading() {
  return <div className="animate-pulse py-8 text-center text-sm text-slate-400">Đang tải dữ liệu…</div>;
}
export function ErrorBox({ msg }: { msg: string }) {
  return <div className="rounded-xl border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200">Lỗi: {msg}</div>;
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">{children}</div>;
}

export function Tone({ v, children }: { v: number | null | undefined; children: ReactNode }) {
  const c = v === null || v === undefined || v === 0 ? "" : v > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400";
  return <span className={`tabular-nums ${c}`}>{children}</span>;
}

export function Th({ children, onClick, active }: { children: ReactNode; onClick?: () => void; active?: boolean }) {
  return (
    <th onClick={onClick}
      className={`whitespace-nowrap px-2 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 ${onClick ? "cursor-pointer select-none" : ""} ${active ? "text-brand dark:text-teal-300" : ""}`}>
      {children}{active ? " ↓" : ""}
    </th>
  );
}
export function Td({ children, className = "" }: { children: ReactNode; className?: string }) {
  const ws = className.includes("whitespace-normal") ? "" : "whitespace-nowrap";
  return <td className={`${ws} px-2 py-2 align-top text-[13px] tabular-nums ${className}`}>{children}</td>;
}
