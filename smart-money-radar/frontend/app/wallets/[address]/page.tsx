"use client";
import Link from "next/link";
import { useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, useApi } from "@/lib/api";
import { ago, dateTime, duration, multiple, num, pct, price, signedPct, sol } from "@/lib/format";
import { RELATION_LABEL, statusLabel } from "@/lib/labels";
import { Address, Card, CategoryBadge, ConfBadge, Copy, Empty, ErrorBox, Loading, Pill, Segmented, Stat, Td, Th, Tone, VerifBadge } from "@/components/ui";

const COMP_LABEL: Record<string, [string, number]> = {
  win_rate: ["Win Rate", 20], median_buy: ["Median Buy SOL", 20], realized_pnl: ["Realized PnL", 15], roi: ["ROI thực tế", 15],
  early_entry: ["Mua trước khi tăng", 20], stability: ["Ổn định & độc lập", 10],
};

export default function WalletPage({ params }: { params: { address: string } }) {
  const a = params.address;
  const [period, setPeriod] = useState<number>(30);
  const [tab, setTab] = useState<"positions" | "trades">("positions");
  const d = useApi<any>(`/api/wallets/${a}`, 60000, ["rankings_updated"]);
  const pos = useApi<any>(`/api/wallets/${a}/positions?period=${period}`, 60000, ["rankings_updated"]);
  const trs = useApi<any>(tab === "trades" ? `/api/wallets/${a}/transactions?limit=500` : null, 60000);
  if (d.loading && !d.data) return <Loading />;
  if (d.error) return <ErrorBox msg={d.error} />;
  const w = d.data.wallet;
  const perf = (d.data.performance as any[]).find((p) => p.period_days === period);
  const sc = (d.data.scores as any[]).find((p) => p.period_days === period);
  const positions = pos.data?.rows || [];
  const q = positions.filter((p: any) => p.qualifying);
  const winners = q.filter((p: any) => p.status !== "open" && Number(p.realized_pnl_sol) > 0);
  const losers = q.filter((p: any) => p.status !== "open" && Number(p.realized_pnl_sol) <= 0);
  return (
    <div className="space-y-4">
      <div>
        <div className="text-xs text-slate-500">Wallet Analysis</div>
        <div className="mt-1 text-base font-semibold"><Address a={a} /></div>
        <div className="mt-2 flex flex-wrap items-center gap-1 text-xs">
          <Pill className="bg-slate-100 dark:bg-slate-800">{statusLabel(w.status)}</Pill>
          {sc && <CategoryBadge c={sc.category} />}{sc && <ConfBadge c={sc.confidence} />}
          {sc?.official_rank && <Pill className="bg-brand text-white">Top #{sc.official_rank}</Pill>}
          {w.cluster_size > 1 && <Pill className="bg-rose-100 text-rose-800 dark:bg-rose-500/20 dark:text-rose-300">Cụm {w.cluster_size} ví liên quan</Pill>}
          <a href={d.data.solscan} target="_blank" rel="noreferrer" className="btn-ghost">Solscan ↗</a>
        </div>
        <div className="mt-1 text-[11px] text-slate-500">
          Backfill: {w.backfill_status}{w.backfill_truncated ? " (bị giới hạn số giao dịch — ví tần suất cao)" : ""} · {num(w.backfill_sigs_seen, 0)} chữ ký ·
          Funding: {w.funding_source ? <span className="font-mono">{w.funding_source.slice(0, 8)}…</span> : "—"} · Giao dịch lỗi bị loại: {d.data.failed_transactions}
        </div>
      </div>

      <Segmented value={period} onChange={setPeriod} options={[{ v: 7, label: "7 ngày" }, { v: 30, label: "30 ngày" }, { v: 90, label: "90 ngày" }]} />

      {!perf ? <Card><Empty>Chưa có số liệu hiệu suất (đang backfill).</Empty></Card> : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
            <Stat label="Smart Money Score" value={sc ? num(sc.score, 1) : "—"} sub="/ 100" />
            <Stat label="Win Rate" value={pct(perf.win_rate, 0)} sub={`${perf.wins} thắng / ${perf.losses} thua (đã đóng)`} />
            <Stat label="Realized PnL" value={sol(perf.realized_pnl_sol, 2)} tone={perf.realized_pnl_sol >= 0 ? "pos" : "neg"} sub="sau phí" />
            <Stat label="Unrealized PnL" value={sol(perf.unrealized_pnl_sol, 2)} sub={`Tổng ${sol(perf.total_pnl_sol, 2)}`} />
            <Stat label="ROI" value={pct(perf.roi, 1)} tone={perf.roi >= 0 ? "pos" : "neg"} />
            <Stat label="Profit Factor" value={perf.profit_factor === null ? "—" : num(perf.profit_factor, 2)} />
            <Stat label="Median Buy SOL" value={sol(perf.median_buy_sol)} sub="theo vị thế ≥ 2 SOL" />
            <Stat label="Average Buy SOL" value={sol(perf.avg_buy_sol)} />
            <Stat label="Largest Buy SOL" value={sol(perf.largest_buy_sol)} />
            <Stat label="Total Buy SOL" value={sol(perf.total_buy_sol, 1)} sub={`${perf.positions_qualifying} vị thế đạt chuẩn`} />
            <Stat label="Average position" value={sol(perf.avg_position_sol)} sub={`${perf.positions_total} vị thế (mọi cỡ)`} />
            <Stat label="Max Drawdown" value={sol(perf.max_drawdown_sol, 2)} />
            <Stat label="Average Win" value={sol(perf.avg_win_sol, 2)} />
            <Stat label="Average Loss" value={sol(perf.avg_loss_sol, 2)} />
            <Stat label="Lệnh thắng lớn nhất" value={sol(perf.largest_win_sol, 2)} />
            <Stat label="Lệnh thua lớn nhất" value={sol(perf.largest_loss_sol, 2)} />
            <Stat label="2x / 3x / 5x / 10x Hit" value={`${pct(perf.hit_rate_2x, 0)} · ${pct(perf.hit_rate_3x, 0)} · ${pct(perf.hit_rate_5x, 0)} · ${pct(perf.hit_rate_10x, 0)}`} sub="token tăng sau entry (đã xác nhận thanh khoản)" />
            <Stat label="Average Sell SOL" value={sol(perf.metrics?.avg_sell_tx_sol)} sub={`mỗi lệnh bán · ${sol(perf.metrics?.avg_sell_position_sol)} / vị thế`} />
            <Stat label="Lợi nhuận đã chốt" value={pct(perf.metrics?.realized_share, 0)} sub="|đã chốt| / (|đã chốt| + |chưa chốt|)" />
            <Stat label="Lặp lại hiệu suất" value={({ pass: "Đạt", fail: "Không đạt", insufficient: "Chưa đủ dữ liệu" } as any)[perf.metrics?.repeatability?.status] || "—"}
              sub={perf.metrics?.repeatability?.first_half ? `ROI nửa đầu ${pct(perf.metrics.repeatability.first_half.roi, 0)} · nửa sau ${pct(perf.metrics.repeatability.second_half.roi, 0)}` : "cần ≥ 6 vị thế đóng"} />
            <Stat label="Avg / Median realized ROI" value={`${pct(perf.avg_realized_roi, 0)} · ${pct(perf.median_realized_roi, 0)}`} />
            <Stat label="Avg Max Favorable Excursion" value={multiple(perf.avg_mfe)} />
            <Stat label="Avg Holding Time" value={duration(perf.avg_holding_seconds)} />
            <Stat label="Tỷ lệ Verified" value={pct(perf.verified_share, 0)} sub={perf.coverage_complete ? "lịch sử đầy đủ" : "lịch sử chưa đủ cho kỳ này"} />
            <Stat label="Last Active" value={ago(perf.last_active_at)} />
          </div>

          {sc && (
            <Card title="Cấu thành Smart Money Score">
              <div className="space-y-2">
                {Object.entries(COMP_LABEL).map(([k, [label, wgt]]) => {
                  const f = sc.components?.[k]?.factor ?? 0;
                  return (
                    <div key={k}>
                      <div className="flex justify-between text-xs"><span>{label}</span><span className="tabular-nums">{num(f * wgt, 1)} / {wgt}</span></div>
                      <div className="mt-1 h-2 rounded-full bg-slate-100 dark:bg-slate-800"><div className="h-2 rounded-full bg-brand" style={{ width: `${Math.min(100, f * 100)}%` }} /></div>
                    </div>
                  );
                })}
                <div className="text-[11px] text-slate-500">Hệ số tần suất: ×{num(sc.components?.multiplier, 2)}.{" "}
                  {sc.qualifies ? "Đạt mọi tiêu chí bắt buộc." : "Chưa đạt tiêu chí bắt buộc."}</div>
                {sc.reasons?.length > 0 && <ul className="list-disc pl-5 text-[11px] text-slate-500">{sc.reasons.map((r: string) => <li key={r}>{r}</li>)}</ul>}
              </div>
            </Card>
          )}
        </>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Realized PnL tích lũy (vị thế ≥ 2 SOL)">
          {d.data.pnl_curve.length === 0 ? <Empty>Chưa có vị thế đóng.</Empty> : (
            <div className="h-56">
              <ResponsiveContainer>
                <AreaChart data={d.data.pnl_curve.map((p: any) => ({ ...p, d: dateTime(p.t).slice(0, 5) }))}>
                  <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.2} />
                  <XAxis dataKey="d" fontSize={10} /><YAxis fontSize={10} width={40} />
                  <Tooltip formatter={(v: any) => sol(v, 2)} />
                  <Area type="monotone" dataKey="cum" stroke="#006b68" fill="#006b68" fillOpacity={0.2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
        <Card title="PnL từng vị thế đã đóng">
          {d.data.pnl_curve.length === 0 ? <Empty>—</Empty> : (
            <div className="h-56">
              <ResponsiveContainer>
                <BarChart data={d.data.pnl_curve}>
                  <XAxis dataKey="t" hide /><YAxis fontSize={10} width={40} />
                  <Tooltip formatter={(v: any) => sol(v, 2)} labelFormatter={(l) => dateTime(l)} />
                  <Bar dataKey="pnl">{d.data.pnl_curve.map((p: any, i: number) => <Cell key={i} fill={p.pnl > 0 ? "#059669" : "#e11d48"} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title={`Token thắng (${winners.length})`}><TokenList ps={winners} /></Card>
        <Card title={`Token thua (${losers.length})`}><TokenList ps={losers} /></Card>
      </div>

      <Card title="Lịch sử" right={<Segmented value={tab} onChange={setTab} options={[{ v: "positions", label: "Vị thế" }, { v: "trades", label: "Từng lệnh" }]} />}>
        {tab === "positions" ? <Positions rows={positions} /> : <Trades rows={trs.data?.rows || []} loading={trs.loading} />}
      </Card>

      {(d.data.relationships.length > 0 || d.data.cluster_members.length > 0) && (
        <Card title="Liên hệ với ví khác">
          <ul className="space-y-1 text-xs">
            {d.data.relationships.map((r: any) => {
              const other = r.wallet_a === a ? r.wallet_b : r.wallet_a;
              return (
                <li key={`${other}-${r.relation_type}`} className="flex flex-wrap items-center gap-2">
                  <Pill className="bg-slate-100 dark:bg-slate-800">{RELATION_LABEL[r.relation_type] || r.relation_type}</Pill>
                  <Link href={`/wallets/${other}`} className="font-mono text-brand dark:text-teal-300">{other.slice(0, 10)}…</Link>
                  <span className="text-slate-500">độ mạnh {num(r.strength, 2)}</span>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
      <ExcludeBox address={a} excluded={w.is_excluded} />
    </div>
  );
}

function TokenList({ ps }: { ps: any[] }) {
  if (!ps.length) return <Empty>—</Empty>;
  return (
    <ul className="space-y-1 text-xs">
      {ps.slice(0, 15).map((p) => (
        <li key={`${p.mint}-${p.cycle_no}`} className="flex justify-between gap-2">
          <Link href={`/tokens/${p.mint}`} className="truncate text-brand dark:text-teal-300">{p.name || p.mint.slice(0, 8)} {p.symbol ? `(${p.symbol})` : ""}</Link>
          <span className="shrink-0 tabular-nums"><Tone v={p.realized_pnl_sol}>{sol(p.realized_pnl_sol, 2)}</Tone> · {pct(p.roi, 0)}</span>
        </li>
      ))}
    </ul>
  );
}

function Positions({ rows }: { rows: any[] }) {
  if (!rows.length) return <Empty>Chưa có vị thế.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>
          <Th>Token</Th><Th>Trạng thái</Th><Th>Mua đầu</Th><Th>Mua cuối</Th><Th>Bắt đầu bán</Th><Th>Thoát hết</Th>
          <Th>Vốn (SOL)</Th><Th>Số lệnh</Th><Th>Giá mua TB</Th><Th>MC khi mua</Th><Th>Giá bán TB</Th><Th>MC khi bán</Th>
          <Th>Realized PnL</Th><Th>ROI</Th><Th>Đỉnh sau entry</Th><Th>Lúc đạt đỉnh</Th><Th>2x/3x/5x/10x</Th><Th>Ví chốt</Th>
          <Th>Giá +5m / 1h / 24h</Th><Th>Giữ</Th><Th>Xác minh</Th><Th>Ghi chú</Th>
        </tr></thead>
        <tbody>
          {rows.map((p) => (
            <tr key={`${p.mint}-${p.cycle_no}`} className={p.qualifying ? "" : "opacity-60"}>
              <Td className="max-w-[180px] whitespace-normal">
                <Link href={`/tokens/${p.mint}`} className="font-medium text-brand dark:text-teal-300">{p.name || "?"}</Link> <span className="text-slate-500">{p.symbol}</span>
                <div className="font-mono text-[10px] text-slate-500">{p.mint.slice(0, 6)}…{p.mint.slice(-4)}<Copy text={p.mint} /></div>
              </Td>
              <Td>{({ open: "Đang mở", closed: "Đã đóng", written_off: "Bỏ rơi (lỗ)" } as any)[p.status]}</Td>
              <Td>{dateTime(p.first_buy_at)}</Td><Td>{dateTime(p.last_buy_at)}</Td><Td>{dateTime(p.first_sell_at)}</Td><Td>{dateTime(p.closed_at)}</Td>
              <Td className="font-semibold">{num(p.buy_sol, 3)}</Td><Td>{p.n_buys}M / {p.n_sells}B</Td>
              <Td>{price(p.avg_entry_price_sol)}</Td><Td>{sol(p.entry_mc_sol, 0)}</Td><Td>{price(p.avg_exit_price_sol)}</Td><Td>{sol(p.exit_mc_sol, 0)}</Td>
              <Td><Tone v={p.realized_pnl_sol}>{sol(p.realized_pnl_sol, 3)}</Tone></Td><Td><Tone v={p.roi}>{pct(p.roi, 0)}</Tone></Td>
              <Td>{p.peak_validated === null ? "chưa có giá" : multiple(p.peak_multiple)}</Td><Td>{dateTime(p.peak_at)}</Td>
              <Td>{[p.hit_2x, p.hit_3x, p.hit_5x, p.hit_10x].map((h: any) => (h === null ? "·" : h ? "✓" : "✗")).join(" ")}</Td>
              <Td>{multiple(p.realized_multiple)}</Td>
              <Td>{signedPct(p.price_roi?.["5m"], 0)} / {signedPct(p.price_roi?.["1h"], 0)} / {signedPct(p.price_roi?.["24h"], 0)}</Td>
              <Td>{duration(p.holding_seconds)}</Td><Td><VerifBadge v={p.verification} /></Td>
              <Td className="max-w-[160px] whitespace-normal text-[11px] text-slate-500">{(p.flags || []).join(", ")}</Td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-slate-500">Dòng mờ: vị thế dưới 2 SOL (không tính vào chỉ số Smart Money). “Đỉnh sau entry” là mức token tăng sau khi ví mua (đã lọc nến mỏng thanh khoản); “Ví chốt” là bội số ví thực sự chốt được — hai số này khác nhau.</p>
    </div>
  );
}

function Trades({ rows, loading }: { rows: any[]; loading: boolean }) {
  if (loading && !rows.length) return <Loading />;
  if (!rows.length) return <Empty>Chưa có giao dịch.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><Th>Thời gian</Th><Th>Loại</Th><Th>Token</Th><Th>SOL</Th><Th>Token</Th><Th>Phí GD</Th><Th>Phí mạng</Th><Th>Tip/bot</Th><Th>Giá</Th><Th>MC</Th><Th>Nguồn</Th><Th>Xác minh</Th><Th>Signature</Th></tr></thead>
        <tbody>
          {rows.map((t) => (
            <tr key={`${t.signature}-${t.event_key}`}>
              <Td>{dateTime(t.block_time)}</Td>
              <Td className={t.tx_type === "buy" ? "text-emerald-600" : t.tx_type === "sell" ? "text-rose-600" : "text-slate-500"}>{({ buy: "Mua", sell: "Bán", transfer_in: "Nhận token", transfer_out: "Chuyển token" } as any)[t.tx_type]}</Td>
              <Td><Link href={`/tokens/${t.mint}`} className="text-brand dark:text-teal-300">{t.symbol || t.mint.slice(0, 6)}</Link></Td>
              <Td className="font-semibold">{num(t.sol_amount, 4)}</Td><Td>{num(t.token_amount, 0)}</Td>
              <Td>{num(t.fee_trading_sol, 5)}</Td><Td>{num(t.fee_network_sol, 6)}</Td><Td>{num(Number(t.tip_sol) + Number(t.other_cost_sol), 5)}</Td>
              <Td>{price(t.effective_price_sol)}</Td><Td>{sol(t.market_cap_sol, 0)}</Td><Td>{t.venue}</Td>
              <Td><VerifBadge v={t.verification_status} /></Td>
              <Td><a className="font-mono text-[11px] text-brand underline dark:text-teal-300" href={`https://solscan.io/tx/${t.signature}`} target="_blank" rel="noreferrer">{t.signature.slice(0, 10)}…</a><Copy text={t.signature} /></Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExcludeBox({ address, excluded }: { address: string; excluded: boolean }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="text-right">
      <button className="btn-ghost" disabled={busy} onClick={async () => {
        setBusy(true);
        await api(`/api/wallets/${address}/exclude`, { method: "POST", body: JSON.stringify({ excluded: !excluded }) });
        location.reload();
      }}>{excluded ? "Bỏ loại trừ ví này" : "Loại trừ ví này khỏi xếp hạng & tín hiệu"}</button>
    </div>
  );
}
