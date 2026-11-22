"use client";
import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { dateTime, sol } from "@/lib/format";
import { Card, Empty, ErrorBox, LevelBadge, Loading, Pill, RiskBadge, Segmented } from "@/components/ui";

const STATUS: Record<string, string> = {
  sent: "Đã gửi Telegram", failed: "Gửi lỗi", dashboard_only: "Chỉ dashboard", suppressed: "Đã chặn trùng",
};
const REASON: Record<string, string> = { new: "Mới", upgrade: "Tăng cấp", important_change: "Biến động quan trọng", exit: "Smart Money đang thoát" };

export default function Alerts() {
  const [filter, setFilter] = useState<"all" | "unseen">("all");
  const { data, error, loading, reload } = useApi<any>(`/api/alerts?unseen_only=${filter === "unseen"}`, 20000, ["alert"]);
  const [open, setOpen] = useState<number | null>(null);
  const rows = data?.rows || [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold">Alert Center</h1>
        <div className="flex gap-2">
          <Segmented value={filter} onChange={setFilter} options={[{ v: "all", label: "Tất cả" }, { v: "unseen", label: "Chưa xem" }]} />
          <button className="btn-ghost" onClick={async () => { await api("/api/alerts/seen-all", { method: "POST" }); reload(); }}>Đánh dấu đã xem hết</button>
        </div>
      </div>
      {loading && !data ? <Loading /> : error ? <ErrorBox msg={error} /> : rows.length === 0 ? (
        <Card><Empty>Chưa có cảnh báo. Cảnh báo được tạo khi ≥ 2 ví Smart Money độc lập cùng mua một token trong 5 phút (cấu hình ở Cài đặt).</Empty></Card>
      ) : (
        <div className="space-y-2">
          {rows.map((a: any) => (
            <div key={a.id} className={`rounded-2xl border p-3 ${a.seen ? "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900" : "border-brand bg-teal-50/60 dark:border-teal-700 dark:bg-teal-950/40"}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <LevelBadge level={a.level} />
                  <Link href={`/tokens/${a.mint}`} className="font-semibold text-brand dark:text-teal-300">{a.name || a.mint.slice(0, 8)} {a.symbol ? `(${a.symbol})` : ""}</Link>
                  <Pill className="bg-slate-100 dark:bg-slate-800">{REASON[a.reason] || a.reason}</Pill>
                  <RiskBadge level={a.risk_level} />
                </div>
                <div className="text-xs text-slate-500">{dateTime(a.created_at)}</div>
              </div>
              <div className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                {a.independent_buyers ?? "?"} ví độc lập · SM mua {sol(a.total_buy_sol, 1)} · Net {sol(a.net_flow_sol, 1)} · {STATUS[a.status] || a.status}
                {a.error ? <span className="text-rose-600"> ({a.error})</span> : null}
              </div>
              <div className="mt-2 flex gap-2">
                <button className="btn-ghost" onClick={() => setOpen(open === a.id ? null : a.id)}>{open === a.id ? "Ẩn nội dung" : "Xem nội dung"}</button>
                <button className="btn-ghost" onClick={async () => { await api(`/api/alerts/${a.id}/seen?seen=${!a.seen}`, { method: "POST" }); reload(); }}>{a.seen ? "Đánh dấu chưa xem" : "Đã xem"}</button>
              </div>
              {open === a.id && <pre className="mt-2 whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-2 text-[11px] dark:bg-slate-800" dangerouslySetInnerHTML={{ __html: a.message }} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
