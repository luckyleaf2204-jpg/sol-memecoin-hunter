"use client";
import Link from "next/link";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "@/lib/api";
import { dateTime, num, pct, price, sol, usd } from "@/lib/format";
import { Address, Card, Empty, ErrorBox, LevelBadge, Loading, Pill, RiskBadge, Stat, Td, Th, VerifBadge } from "@/components/ui";

const CHECK_LABEL: Record<string, string> = {
  dev_holding: "Dev wallet nắm giữ", top10_concentration: "Top 10 holders", common_funding: "Ví mua sớm cùng nguồn funding",
  bundle_buying: "Bundle buying", liquidity: "Thanh khoản", volume_anomaly: "Volume bất thường", wash_trading: "Wash trading",
  dev_selling: "Dev đang bán", mint_authority: "Mint authority", freeze_authority: "Freeze authority", sellable: "Có thể bán",
  single_holder: "Một ví nắm > 5% supply", linked_holders: "Top holder có liên kết (cùng nguồn vốn)",
  fresh_wallets: "Ví mới tạo trong top holder", unnatural_volume: "Volume không tự nhiên", fees_generated: "Phí giao dịch đã tạo",
};
const STATUS_STYLE: Record<string, string> = {
  ok: "text-emerald-600", warn: "text-amber-600", fail: "text-rose-600", unknown: "text-slate-400", info: "text-slate-500",
};

export default function TokenPage({ params }: { params: { mint: string } }) {
  const { data, error, loading } = useApi<any>(`/api/tokens/${params.mint}`, 20000, ["signal"]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox msg={error} />;
  const t = data.token;
  const checks = data.risk?.checks?.checks || [];
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">{t.name || "?"} <span className="text-sm font-normal text-slate-500">{t.symbol}</span></h1>
        <div className="mt-1"><Address a={t.mint} /></div>
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          <a className="btn-ghost" href={data.links.pumpfun} target="_blank" rel="noreferrer">Pump.fun ↗</a>
          <a className="btn-ghost" href={data.links.dexscreener} target="_blank" rel="noreferrer">DexScreener ↗</a>
          <a className="btn-ghost" href={data.links.solscan} target="_blank" rel="noreferrer">Solscan ↗</a>
          {data.risk && <RiskBadge level={data.risk.risk_level} />}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Giá hiện tại" value={price(t.last_price_sol)} />
        <Stat label="Market Cap" value={sol(t.last_mc_sol, 0)} sub={t.market_cap_usd ? usd(t.market_cap_usd) : undefined} />
        <Stat label="Liquidity" value={sol(t.last_liquidity_sol, 1)} sub={t.migrated_at ? "PumpSwap pool (SOL)" : "SOL thật trong bonding curve"} />
        <Stat label="Bonding curve" value={t.migrated_at ? "Đã migrate" : pct(t.curve_progress, 1)} sub={t.migrated_at ? dateTime(t.migrated_at) : undefined} />
        <Stat label="Creator" value={t.creator ? <span className="font-mono text-xs">{t.creator.slice(0, 10)}…</span> : "—"} />
        <Stat label="Tạo lúc" value={<span className="text-sm">{dateTime(t.created_at)}</span>} />
        <Stat label="Mint authority" value={t.authorities_checked_at ? (t.mint_authority ? "CÒN" : "Đã thu hồi") : "—"} />
        <Stat label="Freeze authority" value={t.authorities_checked_at ? (t.freeze_authority ? "CÒN" : "Đã thu hồi") : "—"} />
      </div>

      <Card title="Giá 24h (nến 1 phút từ giao dịch on-chain)">
        {data.bars.length < 2 ? <Empty>Chưa đủ dữ liệu giá.</Empty> : (
          <div className="h-56">
            <ResponsiveContainer>
              <LineChart data={data.bars.map((b: any) => ({ t: dateTime(b.bucket_start).slice(-5), p: b.close_sol }))}>
                <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.2} />
                <XAxis dataKey="t" fontSize={10} minTickGap={30} /><YAxis fontSize={10} width={70} domain={["auto", "auto"]} tickFormatter={(v) => Number(v).toPrecision(3)} />
                <Tooltip formatter={(v: any) => price(v)} />
                <Line type="monotone" dataKey="p" stroke="#006b68" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      <Card title="Phân tích rủi ro" right={data.risk && <span className="text-[11px] text-slate-500">{dateTime(data.risk.computed_at)}</span>}>
        {!checks.length ? <Empty>Chưa đánh giá (chỉ đánh giá token có Smart Money mua).</Empty> : (
          <>
            <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
              {checks.map((c: any) => (
                <li key={c.check} className="flex items-start justify-between gap-3 py-2">
                  <span>{CHECK_LABEL[c.check] || c.check}</span>
                  <span className={`text-right text-xs ${STATUS_STYLE[c.status]}`}>{c.detail}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-slate-500">{data.risk.checks.disclaimer}</p>
          </>
        )}
      </Card>

      <Card title={`Phân bổ holder ${data.holder_estimate.source === "trade_estimate" ? "(ước tính từ lệnh mua/bán)" : "(RPC)"}`}>
        {!data.holder_estimate.complete && data.holder_estimate.source === "trade_estimate" ? (
          <Empty>Hệ thống không theo dõi token này liên tục từ lúc tạo nên không ước tính phân bổ (tránh số liệu thiếu). Thêm HELIUS_API_KEY để đọc holder trực tiếp.</Empty>
        ) : data.holder_estimate.rows.length === 0 ? <Empty>Chưa có dữ liệu.</Empty> : (
          <>
            <ul className="space-y-1 text-xs">
              {data.holder_estimate.rows.slice(0, 15).map((h: any) => (
                <li key={h.wallet} className="flex flex-wrap items-center justify-between gap-2">
                  <span className="flex flex-wrap items-center gap-1">
                    <Link href={`/wallets/${h.wallet}`} className="font-mono text-brand dark:text-teal-300">{h.wallet.slice(0, 6)}…{h.wallet.slice(-4)}</Link>
                    {h.is_creator && <Pill className="bg-slate-200 dark:bg-slate-700">creator</Pill>}
                    {h.over_limit && <Pill className="bg-rose-100 text-rose-800">&gt; 5%</Pill>}
                    {h.fresh && <Pill className="bg-amber-100 text-amber-800">ví mới</Pill>}
                    {h.linked && <Pill className="bg-violet-100 text-violet-800">cùng nguồn vốn</Pill>}
                  </span>
                  <span className="tabular-nums">{num(h.pct, 2)}% · mua {sol(h.buy_sol, 2)}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-slate-500">Ước tính = token mua − token bán của từng ví (không tính token chuyển giữa ví); bonding curve và pool được loại trừ.</p>
          </>
        )}
      </Card>

      {data.flow_metrics?.traders > 0 && (
        <Card title="Chất lượng dòng tiền">
          <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
            <div><div className="text-xs text-slate-500">Ví giao dịch</div><b>{num(data.flow_metrics.traders, 0)}</b></div>
            <div><div className="text-xs text-slate-500">Volume / Market Cap</div><b>{data.fees.volume_to_mc ? `${num(data.fees.volume_to_mc, 1)}x` : "—"}</b></div>
            <div><div className="text-xs text-slate-500">Phí giao dịch đã tạo</div><b>{sol(data.fees.fees_sol, 2)}</b></div>
            <div><div className="text-xs text-slate-500">Volume từ ví đã bán hết</div><b>{pct(data.flow_metrics.round_trip_volume_share, 0)}</b></div>
            <div><div className="text-xs text-slate-500">Cỡ lệnh mua trung vị</div><b>{sol(data.flow_metrics.median_buy_size_sol, 2)}</b></div>
            <div><div className="text-xs text-slate-500">Ví dùng cùng cỡ lệnh</div><b>{pct(data.flow_metrics.size_uniformity, 0)}</b></div>
            <div><div className="text-xs text-slate-500">Vị thế ròng trung vị</div><b>{sol(data.flow_metrics.median_net_position_sol, 2)}</b></div>
            <div><div className="text-xs text-slate-500">Volume / ví</div><b>{sol(data.flow_metrics.volume_per_trader_sol, 2)}</b></div>
          </div>
        </Card>
      )}

      {data.signals.length > 0 && (
        <Card title="Tín hiệu Smart Money">
          <ul className="space-y-1 text-sm">
            {data.signals.map((s: any) => (
              <li key={s.id} className="flex flex-wrap items-center gap-2">
                <LevelBadge level={s.level} /><span>{s.independent_buyers} ví độc lập · {sol(s.total_buy_sol, 1)}</span>
                <span className="text-xs text-slate-500">{dateTime(s.detected_at)} · {s.status}</span>
                <ul className="w-full pl-2 text-xs text-slate-600 dark:text-slate-300">
                  {(s.buyers || []).map((b: any) => (
                    <li key={b.wallet} className="flex flex-wrap gap-x-2">
                      <Link href={`/wallets/${b.wallet}`} className="font-mono text-brand dark:text-teal-300">{b.wallet.slice(0, 8)}…</Link>
                      <span>{sol(b.position_sol)}</span>
                      <span>vào {b.first_ts ? dateTime(new Date(b.first_ts * 1000).toISOString()) : "—"}</span>
                      <span>giá {price(b.entry_price_sol)}</span><span>MC {sol(b.entry_mc_sol, 1)}</span>
                      <span>score {num(b.score, 0)}</span>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Giao dịch của ví được theo dõi">
        {!data.smart_trades.length ? <Empty>—</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><Th>Thời gian</Th><Th>Ví</Th><Th>Loại</Th><Th>SOL</Th><Th>MC</Th><Th>Score</Th><Th>Xác minh</Th><Th>Tx</Th></tr></thead>
              <tbody>
                {data.smart_trades.map((x: any) => (
                  <tr key={`${x.signature}-${x.event_key}`}>
                    <Td>{dateTime(x.block_time)}</Td>
                    <Td><Link href={`/wallets/${x.wallet}`} className="font-mono text-[12px] text-brand dark:text-teal-300">{x.wallet.slice(0, 8)}…</Link>
                      {x.wallet_status === "smart" && <Pill className="ml-1 bg-brand text-white">SM</Pill>}</Td>
                    <Td className={x.tx_type === "buy" ? "text-emerald-600" : "text-rose-600"}>{x.tx_type === "buy" ? "Mua" : x.tx_type === "sell" ? "Bán" : x.tx_type}</Td>
                    <Td className="font-semibold">{num(x.sol_amount, 3)}</Td><Td>{sol(x.market_cap_sol, 0)}</Td>
                    <Td>{x.score ? num(x.score, 1) : "—"}</Td><Td><VerifBadge v={x.verification_status} /></Td>
                    <Td><a className="font-mono text-[11px] underline" href={`https://solscan.io/tx/${x.signature}`} target="_blank" rel="noreferrer">{x.signature.slice(0, 8)}…</a></Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {data.holders.length > 0 && (
        <Card title="Top holders (RPC getTokenLargestAccounts)">
          <ul className="space-y-1 text-xs">
            {data.holders.map((h: any) => (
              <li key={h.token_account} className="flex justify-between gap-2">
                <span className="font-mono">{(h.owner || h.token_account).slice(0, 12)}… {h.is_curve_or_pool ? "(curve/pool)" : ""}{h.is_creator ? " (creator)" : ""}</span>
                <span>{num(h.pct_supply, 2)}%</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
