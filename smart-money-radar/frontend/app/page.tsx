"use client";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { ago, num, sol } from "@/lib/format";
import { Card, Empty, ErrorBox, LevelBadge, Loading, RiskBadge, Stat, VerifBadge } from "@/components/ui";

export default function Overview() {
  const { data, error, loading } = useApi<any>("/api/overview", 20000, ["signal", "alert", "rankings_updated"]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox msg={error} />;
  const d = data!;
  const st = d.stream;
  const streamOk = st?.status === "ok";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">Tổng quan</h1>
          <p className="text-xs text-slate-500">Dữ liệu on-chain Pump.fun · SOL ≈ {d.sol_usd ? `$${num(d.sol_usd, 2)}` : "—"} · Giao dịch tự động: TẮT</p>
        </div>
        <div className={`rounded-full px-3 py-1 text-xs font-medium ${streamOk ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300" : "bg-amber-100 text-amber-800"}`}>
          Stream {streamOk ? "đang chạy" : "gián đoạn"} · {num(st?.details?.trades || 0, 0)} lệnh đã giải mã
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <Stat label="Ví đang theo dõi" value={num(d.wallets.tracked, 0)} sub={`${num(d.wallets.backfilling, 0)} đang backfill`} />
        <Stat label="Ví đạt tiêu chí" value={num(d.wallets.smart, 0)} sub={`${num(d.official_ranked, 0)} đủ điều kiện xếp hạng`} />
        <Stat label="Token đang được gom" value={num(d.tokens_accumulating, 0)} sub={`${num(d.smart_tokens_24h, 0)} token có SM giao dịch 24h`} />
        <Stat label="SM mua 24h" value={sol(d.smart_buy_sol_24h, 1)} sub={`bán ${sol(d.smart_sell_sol_24h, 1)}`} />
        <Stat label="Cảnh báo mới" value={num(d.unseen_alerts, 0)} tone={d.unseen_alerts > 0 ? "neg" : undefined} />
        <Stat label="Lệnh quan sát 24h" value={num(d.observed_24h.trades, 0)} sub={`${num(d.observed_24h.wallets, 0)} ví`} />
      </div>

      <Card title="Tín hiệu mạnh nhất (24h)" right={<Link href="/alerts" className="text-xs text-brand underline dark:text-teal-300">Tất cả cảnh báo</Link>}>
        {d.strongest_signals.length === 0 ? (
          <Empty>Chưa có tín hiệu Smart Money cùng mua nào trong 24 giờ qua.</Empty>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {d.strongest_signals.map((s: any) => (
              <Link key={s.id} href={`/tokens/${s.mint}`} className="rounded-xl border border-slate-200 p-3 active:bg-slate-50 dark:border-slate-800">
                <div className="flex items-center justify-between gap-2">
                  <div className="truncate font-semibold">{s.name || "?"} <span className="text-xs text-slate-500">{s.symbol}</span></div>
                  <LevelBadge level={s.level} />
                </div>
                <div className="mt-2 grid grid-cols-3 gap-1 text-xs">
                  <div><div className="text-slate-500">Ví độc lập</div><div className="font-semibold">{s.independent_buyers}</div></div>
                  <div><div className="text-slate-500">SM mua</div><div className="font-semibold">{sol(s.total_buy_sol, 1)}</div></div>
                  <div><div className="text-slate-500">Net flow</div><div className="font-semibold">{sol(s.net_flow_sol, 1)}</div></div>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1 text-[11px] text-slate-500">
                  <RiskBadge level={s.risk_level} /><VerifBadge v={s.verification} /><span>{ago(s.detected_at)}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </Card>

      <p className="text-[11px] leading-relaxed text-slate-500">
        Mọi số liệu được tính từ giao dịch on-chain có chữ ký giao dịch (signature) để kiểm chứng trên explorer.
        Hệ thống chỉ phân tích và cảnh báo, không đưa ra lời khuyên đầu tư và không tự động mua bán.
      </p>
    </div>
  );
}
