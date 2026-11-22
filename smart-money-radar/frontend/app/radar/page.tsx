"use client";
import Link from "next/link";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { ago, duration, num, pct, price, signedPct, sol, usd } from "@/lib/format";
import { flowLabel } from "@/lib/labels";
import { Address, Card, Empty, ErrorBox, LevelBadge, Loading, Pill, RiskBadge, Segmented, Td, Th, Tone } from "@/components/ui";

const FLOW_STYLE: Record<string, string> = {
  ACCUMULATING: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300",
  DISTRIBUTING: "bg-rose-100 text-rose-800 dark:bg-rose-500/20 dark:text-rose-300",
  BALANCED: "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200",
};

export default function Radar() {
  const [hours, setHours] = useState<number>(24);
  const { data, error, loading } = useApi<any>(`/api/radar?hours=${hours}`, 15000, ["signal"]);
  const rows = data?.rows || [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold">Token Radar</h1>
        <Segmented value={hours} onChange={setHours} options={[{ v: 1, label: "1 giờ" }, { v: 6, label: "6 giờ" }, { v: 24, label: "24 giờ" }, { v: 72, label: "3 ngày" }]} />
      </div>
      <p className="text-xs text-slate-500">Token đang được ví Smart Money giao dịch. Net Smart Money Flow = tổng SOL mua − tổng SOL bán của ví Smart Money; xét cả quy mô, không chỉ số ví.</p>
      {loading && !data ? <Loading /> : error ? <ErrorBox msg={error} /> : rows.length === 0 ? (
        <Card><Empty>Chưa có token nào được ví Smart Money giao dịch trong khoảng thời gian này.</Empty></Card>
      ) : (
        <>
          <div className="space-y-3 md:hidden">
            {rows.map((r: any) => (
              <Link key={r.mint} href={`/tokens/${r.mint}`} className="block rounded-2xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0"><div className="truncate font-semibold">{r.name || "?"} <span className="text-xs text-slate-500">{r.symbol}</span></div>
                    <div className="mt-0.5"><Address a={r.mint} full={false} /></div></div>
                  <div className="flex flex-col items-end gap-1"><LevelBadge level={r.signal_level} /><RiskBadge level={r.risk_level} /></div>
                </div>
                <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                  <K l="SM mua" v={`${r.smart_buyers} ví · ${sol(r.smart_buy_sol, 1)}`} /><K l="SM bán" v={sol(r.smart_sell_sol, 1)} />
                  <K l="Net flow" v={<Tone v={r.net_flow_sol}>{sol(r.net_flow_sol, 1)}</Tone>} />
                  <K l="Market Cap" v={r.market_cap_usd ? usd(r.market_cap_usd) : sol(r.market_cap_sol, 0)} /><K l="Liquidity" v={sol(r.liquidity_sol, 1)} />
                  <K l="Giá 5m / 1h" v={<><Tone v={r.price_change_5m}>{signedPct(r.price_change_5m, 0)}</Tone> / <Tone v={r.price_change_1h}>{signedPct(r.price_change_1h, 0)}</Tone></>} />
                  <K l="Tuổi token" v={duration(r.token_age_seconds)} /><K l="Bonding curve" v={r.migrated ? "Đã migrate" : pct(r.curve_progress, 0)} />
                  <K l="SM vào đầu tiên" v={ago(r.first_smart_entry)} />
                </div>
                <div className="mt-2"><Pill className={FLOW_STYLE[r.flow]}>{flowLabel(r.flow)}</Pill></div>
              </Link>
            ))}
          </div>
          <Card className="hidden md:block">
            <div className="table-wrap">
              <table>
                <thead><tr>
                  <Th>Token</Th><Th>Mint</Th><Th>Creator</Th><Th>Market Cap</Th><Th>Liquidity</Th><Th>Volume 1h</Th><Th>Holders</Th>
                  <Th>SM Buyers</Th><Th>SM Buy</Th><Th>SM Sell</Th><Th>Net Flow</Th><Th>Dòng tiền</Th><Th>SM đầu tiên</Th>
                  <Th>Giá</Th><Th>5m</Th><Th>15m</Th><Th>1h</Th><Th>Tuổi</Th><Th>Curve</Th><Th>Risk</Th><Th>Tín hiệu</Th>
                </tr></thead>
                <tbody>
                  {rows.map((r: any) => (
                    <tr key={r.mint}>
                      <Td><Link href={`/tokens/${r.mint}`} className="font-medium text-brand dark:text-teal-300">{r.name || "?"}</Link> <span className="text-slate-500">{r.symbol}</span></Td>
                      <Td><Address a={r.mint} full={false} /></Td>
                      <Td>{r.creator ? <Address a={r.creator} full={false} /> : "—"}</Td>
                      <Td>{r.market_cap_usd ? usd(r.market_cap_usd) : "—"}<div className="text-[10px] text-slate-500">{sol(r.market_cap_sol, 0)}</div></Td>
                      <Td>{sol(r.liquidity_sol, 1)}</Td><Td>{sol(r.volume_1h_sol, 1)}</Td><Td>{r.holder_count ?? "—"}</Td>
                      <Td>{r.smart_buyers}{r.independent_buyers !== r.smart_buyers ? ` (${r.independent_buyers} độc lập)` : ""}</Td>
                      <Td>{sol(r.smart_buy_sol, 1)}</Td><Td>{sol(r.smart_sell_sol, 1)}</Td>
                      <Td><Tone v={r.net_flow_sol}>{sol(r.net_flow_sol, 1)}</Tone></Td>
                      <Td><Pill className={FLOW_STYLE[r.flow]}>{flowLabel(r.flow)}</Pill></Td>
                      <Td>{ago(r.first_smart_entry)}</Td><Td>{price(r.current_price_sol)}</Td>
                      <Td><Tone v={r.price_change_5m}>{signedPct(r.price_change_5m, 0)}</Tone></Td>
                      <Td><Tone v={r.price_change_15m}>{signedPct(r.price_change_15m, 0)}</Tone></Td>
                      <Td><Tone v={r.price_change_1h}>{signedPct(r.price_change_1h, 0)}</Tone></Td>
                      <Td>{duration(r.token_age_seconds)}</Td><Td>{r.migrated ? "migrated" : pct(r.curve_progress, 0)}</Td>
                      <Td><RiskBadge level={r.risk_level} /></Td><Td><LevelBadge level={r.signal_level} /></Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
      <p className="text-[11px] text-slate-500">Volume tính bằng SOL từ giao dịch on-chain đã giải mã. Holders chỉ hiển thị khi có HELIUS_API_KEY. {num(rows.length, 0)} token.</p>
    </div>
  );
}

function K({ l, v }: { l: string; v: any }) {
  return <div><div className="text-[10px] text-slate-500">{l}</div><div className="font-medium tabular-nums">{v}</div></div>;
}
