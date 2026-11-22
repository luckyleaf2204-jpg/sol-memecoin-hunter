"use client";
// Overview — layout of the UI Kit reference (KPI row, Smart Money Radar + flow chart, Top Wallets,
// KOL Tracker, Live Feed, Token Radar). Every number comes from the backend API; when a group of
// wallets has no data the panel says so and why. Nothing is hard-coded.
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "@/lib/api";
import { ago, compact, num, pct, sol } from "@/lib/format";
import { EmptyState, Icon, Kpi, Panel, PanelLink, Seg, ShortAddr, SidePill, SkelRows, TokenCell } from "@/components/terminal";

type Scope = "smart" | "tracked" | "kol";
const SCOPE_LABEL: Record<Scope, string> = { smart: "Smart Money", tracked: "Ví đang theo dõi", kol: "Ví KOL" };
const FLOW_LABEL: Record<string, [string, string]> = {
  ACCUMULATING: ["Đang gom", "t-buy"], DISTRIBUTING: ["Đang xả", "t-sell"], BALANCED: ["Cân bằng", "t-mute"],
};

export default function Overview() {
  const ov = useApi<any>("/api/overview", 20000, ["signal", "alert", "rankings_updated"]);
  const [scope, setScope] = useState<Scope | null>(null);
  const [hours, setHours] = useState<number>(24);
  // no official Smart Money wallet yet -> start on the tracked wallets, and say so on screen
  useEffect(() => {
    if (scope === null && ov.data) setScope(ov.data.wallets.smart > 0 ? "smart" : "tracked");
  }, [ov.data, scope]);
  const sc: Scope = scope || "smart";
  const ready = scope !== null;
  const radar = useApi<any>(ready ? `/api/radar?hours=${hours}&scope=${sc}&limit=60` : null, 30000, ["signal"]);
  const flow = useApi<any>(ready ? `/api/flow?hours=24&scope=${sc}` : null, 30000);
  const d = ov.data;
  const smartCount = d?.wallets?.smart ?? 0;

  return (
    <div className="space-y-4">
      {ov.error && <div className="t-panel p-3 text-sm t-neg">Không tải được dữ liệu tổng quan: {ov.error}</div>}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="t-muted text-xs">
          Nhóm ví đang xem: <b style={{ color: "var(--text)" }}>{SCOPE_LABEL[sc]}</b>
          {d && smartCount === 0 && sc !== "smart" && (
            <span className="t-warn ml-2 rounded px-1.5 py-0.5 text-[11px]">Chưa có ví đạt chuẩn Smart Money (0 ví) — đang hiển thị nhóm khác, chưa phải Smart Money đã xác minh</span>
          )}
        </div>
        <Seg value={sc} onChange={(v) => setScope(v)} options={[{ v: "smart", label: "Smart Money" }, { v: "tracked", label: "Đang theo dõi" }, { v: "kol", label: "KOL" }]} />
      </div>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi icon="wallets" tone="teal" label="Smart Wallets (đạt chuẩn)" loading={!d} value={num(smartCount, 0)}
          sub={d ? `${num(d.wallets.tracked, 0)} ví theo dõi · ${num(d.kol_wallets, 0)} KOL` : undefined} />
        <Kpi icon="flow" tone="green" label={`Buy Flow 24H · ${SCOPE_LABEL[sc]}`} loading={!flow.data} value={flow.data ? `${compact(flow.data.buy_sol)} SOL` : "—"}
          sub={flow.data ? `bán ${compact(flow.data.sell_sol)} SOL · ${num(flow.data.trades, 0)} lệnh` : undefined} />
        <Kpi icon="coins" tone="teal" label="Active Tokens 24H" loading={!flow.data} value={flow.data ? num(flow.data.tokens, 0) : "—"}
          sub={flow.data ? `${num(flow.data.wallets, 0)} ví giao dịch` : undefined} />
        <Kpi icon="bell" tone="amber" label="Alerts chưa đọc" loading={!d} value={d ? num(d.unseen_alerts, 0) : "—"}
          sub={d ? `${num(d.alerts_24h, 0)} cảnh báo trong 24h` : undefined} />
      </div>

      <div className="grid gap-4 xl:grid-cols-12">
        <Panel className="xl:col-span-8" icon="radar" title="Smart Money Radar" sub={`Token ${SCOPE_LABEL[sc].toLowerCase()} đang giao dịch — xếp theo SOL mua`}
          right={<Seg value={hours} onChange={setHours} options={[{ v: 24, label: "24H" }, { v: 168, label: "7D" }]} />}>
          <RadarTable q={radar} scope={sc} limit={6} />
        </Panel>
        <Panel className="xl:col-span-4" icon="flow" title="Dòng tiền 24h" sub={`${SCOPE_LABEL[sc]} · SOL mua/bán theo giờ`}>
          <FlowChart q={flow} scope={sc} />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-12">
        <TopWalletsPanel className="xl:col-span-5" />
        <KolPanel className="xl:col-span-7" />
      </div>

      <div className="grid gap-4 xl:grid-cols-12">
        <LiveFeedPanel className="xl:col-span-7" scope={sc} ready={ready} />
        <TokenRadarPanel className="xl:col-span-5" q={radar} scope={sc} />
      </div>

      <p className="t-faint text-[11px]">
        Nguồn: giao dịch pump.fun / PumpSwap do hệ thống tự giải mã từ blockchain (log chương trình + giao dịch đầy đủ).
        SOL ≈ {d?.sol_usd ? `$${num(d.sol_usd, 2)}` : "—"} (Jupiter). Lệnh mới nhất hệ thống nhận: {d?.last_trade_at ? ago(d.last_trade_at) : "—"}.
        Chỉ phân tích, không tự giao dịch.
      </p>
    </div>
  );
}

function scopeEmpty(scope: Scope) {
  if (scope === "smart") return "Chưa có ví nào đạt đủ tiêu chí Smart Money (Win Rate ≥ 65%, ≥ 5 vị thế đã đóng, PnL dương, giống người, copy được…). Chọn \"Đang theo dõi\" để xem các ví ứng viên.";
  if (scope === "kol") return "Các ví KOL chưa có giao dịch trong khoảng thời gian này.";
  return "Các ví đang theo dõi chưa có giao dịch trong khoảng thời gian này.";
}

function RadarTable({ q, scope, limit }: { q: any; scope: Scope; limit: number }) {
  if (!q.data && !q.error) return <SkelRows rows={limit} cols={6} />;
  if (q.error) return <EmptyState tone="error" title="Không tải được Radar" reason={q.error} />;
  const rows = (q.data.rows || []).slice(0, limit);
  if (!rows.length) return <EmptyState title="Chưa có token nào" reason={scopeEmpty(scope)} />;
  return (
    <div className="overflow-x-auto">
      <table className="t-table">
        <thead><tr><th className="t-hide-m">#</th><th>Token</th><th className="text-right">Ví mua</th><th className="text-right">Tổng mua</th><th className="t-hide-m text-right">Net (SOL)</th><th className="text-right">Giá 1h</th><th className="t-hide-m">Signal</th><th className="t-hide-m"></th></tr></thead>
        <tbody>
          {rows.map((r: any, i: number) => {
            const [fl, cls] = FLOW_LABEL[r.flow] || [r.flow, "t-mute"];
            return (
              <tr key={r.mint}>
                <td className="t-faint t-hide-m">{i + 1}</td>
                <td><TokenCell mint={r.mint} symbol={r.symbol} name={r.name} /></td>
                <td className="num text-right">{num(r.smart_buyers, 0)}</td>
                <td className="num text-right">{compact(r.smart_buy_sol)}</td>
                <td className={`num t-hide-m text-right ${r.net_flow_sol >= 0 ? "t-pos" : "t-neg"}`}>{r.net_flow_sol >= 0 ? "+" : ""}{compact(r.net_flow_sol)}</td>
                <td className={`num text-right ${r.price_change_1h == null ? "t-faint" : r.price_change_1h >= 0 ? "t-pos" : "t-neg"}`}>
                  {r.price_change_1h == null ? "—" : `${r.price_change_1h >= 0 ? "+" : ""}${pct(r.price_change_1h, 1)}`}</td>
                <td className="t-hide-m"><span className={`t-pill ${cls}`}>{fl}</span>{r.signal_level && <span className="t-pill t-warn ml-1">{r.signal_level.replace("_", " ")}</span>}</td>
                <td className="t-hide-m"><a href={`https://dexscreener.com/solana/${r.mint}`} target="_blank" rel="noreferrer" className="t-faint" aria-label="DexScreener"><Icon name="external" size={15} /></a></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FlowChart({ q, scope }: { q: any; scope: Scope }) {
  const data = useMemo(() => (q.data?.points || []).map((p: any) => ({
    t: new Date(p.h).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", hour12: false }),
    buy: Number(p.buy), sell: Number(p.sell),
  })), [q.data]);
  if (!q.data && !q.error) return <div className="px-4 pb-4"><div className="t-skel h-48 w-full" /></div>;
  if (q.error) return <EmptyState tone="error" title="Không tải được dòng tiền" reason={q.error} />;
  if (!data.length) return <EmptyState title="Chưa có dòng tiền trong 24h" reason={scopeEmpty(scope)} />;
  const buy = Number(q.data.buy_sol), sell = Number(q.data.sell_sol), tot = buy + sell;
  const pie = [{ name: "Mua", v: buy, c: "#2fd08f" }, { name: "Bán", v: sell, c: "#f0556e" }];
  return (
    <div className="px-2 pb-4">
      <div className="h-44">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 8, left: -8, bottom: 0 }}>
            <defs>
              <linearGradient id="gb" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#2fd08f" stopOpacity={0.45} /><stop offset="100%" stopColor="#2fd08f" stopOpacity={0} /></linearGradient>
              <linearGradient id="gs" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#f0556e" stopOpacity={0.3} /><stop offset="100%" stopColor="#f0556e" stopOpacity={0} /></linearGradient>
            </defs>
            <CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="t" tick={{ fill: "var(--faint)", fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={24} />
            <YAxis tick={{ fill: "var(--faint)", fontSize: 10 }} tickLine={false} axisLine={false} width={46} tickFormatter={(v) => compact(v, 0)} />
            <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 }}
              formatter={(v: any, k: any) => [`${num(Number(v), 2)} SOL`, k === "buy" ? "Mua" : "Bán"]} />
            <Area type="monotone" dataKey="buy" stroke="#2fd08f" strokeWidth={2} fill="url(#gb)" />
            <Area type="monotone" dataKey="sell" stroke="#f0556e" strokeWidth={1.5} fill="url(#gs)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 flex items-center gap-4 px-2">
        <div className="relative h-24 w-24 flex-none">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart><Pie data={pie} dataKey="v" innerRadius={32} outerRadius={44} stroke="none" startAngle={90} endAngle={-270}>
              {pie.map((p) => <Cell key={p.name} fill={p.c} />)}</Pie></PieChart>
          </ResponsiveContainer>
          <div className="absolute inset-0 grid place-items-center text-center text-[10px] font-bold leading-tight" style={{ color: "var(--text)" }}>{compact(tot)}<br />SOL</div>
        </div>
        <div className="min-w-0 flex-1 space-y-1.5 text-xs">
          <div className="flex justify-between"><span><span className="mr-1.5 inline-block h-2 w-2 rounded-sm" style={{ background: "#2fd08f" }} />Mua ({tot ? pct(buy / tot, 1) : "—"})</span><b className="num">{compact(buy)} SOL</b></div>
          <div className="flex justify-between"><span><span className="mr-1.5 inline-block h-2 w-2 rounded-sm" style={{ background: "#f0556e" }} />Bán ({tot ? pct(sell / tot, 1) : "—"})</span><b className="num">{compact(sell)} SOL</b></div>
        </div>
      </div>
    </div>
  );
}

function TopWalletsPanel({ className }: { className: string }) {
  const off = useApi<any>("/api/wallets/top?period=30&official=true&limit=5", 60000, ["rankings_updated"]);
  const all = useApi<any>("/api/wallets/top?period=30&official=false&limit=5", 60000, ["rankings_updated"]);
  const official = (off.data?.rows || []).length > 0;
  const q = official ? off : all;
  const rows = q.data?.rows || [];
  return (
    <Panel className={className} icon="trophy" title="Top Wallets" sub={official ? "Top chính thức (30 ngày)" : "Chưa có ví chính thức — ứng viên điểm cao nhất (30 ngày)"} right={<PanelLink href="/wallets" />}>
      {!q.data && !q.error ? <SkelRows rows={5} cols={5} /> : q.error ? <EmptyState tone="error" title="Không tải được" reason={q.error} /> : !rows.length ? (
        <EmptyState title="Chưa có ví nào được chấm điểm" reason="Hệ thống đang đọc lịch sử các ví ứng viên; ví cần ≥ 1 vị thế đã đóng để có điểm." />
      ) : (
        <div className="overflow-x-auto">
          <table className="t-table">
            <thead><tr><th className="t-hide-m">#</th><th>Wallet</th><th className="text-right">Win rate</th><th className="text-right">PnL (SOL)</th><th className="text-right">Score</th><th className="t-hide-m">Trạng thái</th></tr></thead>
            <tbody>
              {rows.map((r: any, i: number) => (
                <tr key={r.wallet}>
                  <td className="t-faint t-hide-m">{official ? r.official_rank : i + 1}</td>
                  <td><ShortAddr a={r.wallet} href={`/wallets/${r.wallet}`} /></td>
                  <td className="num text-right">{pct(r.win_rate, 1)} <span className="t-faint text-[10px]">({r.closed_qualifying})</span></td>
                  <td className={`num text-right ${Number(r.realized_pnl_sol) >= 0 ? "t-pos" : "t-neg"}`}>{Number(r.realized_pnl_sol) >= 0 ? "+" : ""}{num(r.realized_pnl_sol, 2)}</td>
                  <td className="num text-right font-bold" style={{ color: "var(--buy)" }}>{num(r.score, 1)}</td>
                  <td className="t-hide-m">{r.official_rank ? <span className="t-pill t-buy">Official</span> : <span className="t-pill t-mute">{r.confidence === "LOW" ? "Candidate" : "Tracking"}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function KolPanel({ className }: { className: string }) {
  const q = useApi<any>("/api/watchlist/feed?limit=6", 15000, ["alert"]);
  const rows = q.data?.rows || [];
  return (
    <Panel className={className} icon="kol" title="KOL Tracker" sub="Ví KOL / người gọi kèo — nhãn lấy từ nguồn công khai, chưa phải xác nhận của chủ ví" right={<PanelLink href="/kol" />}>
      {!q.data && !q.error ? <SkelRows rows={5} cols={5} /> : q.error ? <EmptyState tone="error" title="Không tải được" reason={q.error} /> : !rows.length ? (
        <EmptyState title="Chưa có giao dịch của ví KOL trong 3 ngày" reason="Thêm ví ở trang KOL Tracker; hệ thống ghi nhận mua/bán khi ví giao dịch trên pump.fun." />
      ) : (
        <div className="overflow-x-auto">
          <table className="t-table">
            <thead><tr><th>KOL / Wallet</th><th>Hành động</th><th>Token</th><th className="text-right">Giá trị (SOL)</th><th className="text-right">Thời gian</th></tr></thead>
            <tbody>
              {rows.map((t: any) => (
                <tr key={t.signature + t.wallet + t.tx_type}>
                  <td><Link href={`/wallets/${t.wallet}`} className="font-semibold" style={{ color: "var(--text)" }}>{t.label}</Link><div className="t-faint mono text-[10px]">{t.wallet.slice(0, 4)}…{t.wallet.slice(-4)}</div></td>
                  <td><SidePill side={t.tx_type} /></td>
                  <td><Link href={`/tokens/${t.mint}`} className="font-semibold" style={{ color: "var(--text)" }}>{t.symbol ? `$${t.symbol}` : `${t.mint.slice(0, 4)}…`}</Link></td>
                  <td className="num text-right">{num(t.sol_amount, 2)}</td>
                  <td className="t-muted text-right">{ago(t.block_time)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function LiveFeedPanel({ className, scope, ready }: { className: string; scope: Scope; ready: boolean }) {
  const [paused, setPaused] = useState(false);
  const q = useApi<any>(ready ? `/api/trades/recent?scope=${scope}&limit=8` : null, paused ? 0 : 5000);
  const rows = q.data?.rows || [];
  const solUsd = q.data?.sol_usd;
  const lastDelay = rows.length ? Number(rows[0].delay_s) : null;
  return (
    <Panel className={className} icon="feed" title="Live Transaction Feed" sub={`Giao dịch pump.fun / PumpSwap · ${SCOPE_LABEL[scope]}`}
      right={<>
        <button className="radar-icon-button" onClick={() => setPaused(!paused)} aria-label={paused ? "Tiếp tục" : "Tạm dừng"}><Icon name={paused ? "play" : "pause"} size={15} /></button>
        <span className={`radar-status ${paused ? "warn" : "ok"}`}><span className="radar-dot" />{paused ? "Tạm dừng" : "Trực tiếp"}</span>
      </>}>
      {!q.data && !q.error ? <SkelRows rows={6} cols={6} /> : q.error ? <EmptyState tone="error" title="Không tải được" reason={q.error} /> : !rows.length ? (
        <EmptyState title="Chưa có giao dịch" reason={scopeEmpty(scope)} />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="t-table">
              <thead><tr><th>Thời gian</th><th>Wallet</th><th>Token</th><th>Loại</th><th className="text-right">SOL</th><th className="t-hide-m text-right">Giá (USD)</th><th className="t-hide-m">Tx</th></tr></thead>
              <tbody>
                {rows.map((t: any) => (
                  <tr key={t.signature + t.wallet + t.tx_type + t.mint}>
                    <td className="num t-muted">{new Date(t.block_time).toLocaleTimeString("vi-VN", { hour12: false })}</td>
                    <td>{t.kol_label ? <span className="font-semibold">{t.kol_label}</span> : <ShortAddr a={t.wallet} href={`/wallets/${t.wallet}`} />}</td>
                    <td><Link href={`/tokens/${t.mint}`} className="font-semibold" style={{ color: "var(--text)" }}>{t.symbol ? `$${t.symbol}` : `${t.mint.slice(0, 4)}…`}</Link></td>
                    <td><SidePill side={t.tx_type} /></td>
                    <td className="num text-right">{num(t.sol_amount, 2)}</td>
                    <td className="num t-hide-m text-right t-muted">{t.effective_price_sol && solUsd ? `$${Number(t.effective_price_sol * solUsd).toPrecision(3)}` : "—"}</td>
                    <td className="t-hide-m"><a href={`https://solscan.io/tx/${t.signature}`} target="_blank" rel="noreferrer" className="t-faint" aria-label="Solscan"><Icon name="external" size={14} /></a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="t-faint px-4 pb-3 pt-2 text-[11px]">Độ trễ lệnh mới nhất (từ lúc khớp trên chain đến lúc hệ thống ghi): {lastDelay != null ? `${num(lastDelay, 1)} giây` : "—"} · <Link href="/live" style={{ color: "var(--accent-2)" }}>Mở Live Feed đầy đủ →</Link></div>
        </>
      )}
    </Panel>
  );
}

function ageText(s: number | null | undefined) {
  if (s == null) return "—";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} phút`;
  if (s < 172800) return `${Math.round(s / 3600)} giờ`;
  return `${Math.round(s / 86400)} ngày`;
}

function TokenRadarPanel({ className, q, scope }: { className: string; q: any; scope: Scope }) {
  const rows = useMemo(() => [...(q.data?.rows || [])]
    .filter((r: any) => r.token_age_seconds != null)
    .sort((a: any, b: any) => a.token_age_seconds - b.token_age_seconds).slice(0, 5), [q.data]);
  return (
    <Panel className={className} icon="radar" title="Token Radar" sub="Token mới nhất có ví theo dõi tham gia" right={<PanelLink href="/radar" />}>
      {!q.data && !q.error ? <SkelRows rows={5} cols={5} /> : q.error ? <EmptyState tone="error" title="Không tải được" reason={q.error} /> : !rows.length ? (
        <EmptyState title="Chưa có token" reason={(q.data?.rows || []).length ? "Các token đang giao dịch chưa có thời điểm tạo (token tạo trước khi hệ thống chạy)." : scopeEmpty(scope)} />
      ) : (
        <div className="overflow-x-auto">
          <table className="t-table">
            <thead><tr><th>Token</th><th className="text-right">Tuổi</th><th className="text-right">Ví tham gia</th><th className="text-right">Volume (SOL)</th><th className="text-right">Giá 1h</th></tr></thead>
            <tbody>
              {rows.map((r: any) => (
                <tr key={r.mint}>
                  <td><TokenCell mint={r.mint} symbol={r.symbol} name={r.name} /></td>
                  <td className="num text-right">{ageText(r.token_age_seconds)}</td>
                  <td className="num text-right">{num(r.smart_buyers, 0)}</td>
                  <td className="num text-right">{compact(Number(r.smart_buy_sol) + Number(r.smart_sell_sol))}</td>
                  <td className={`num text-right ${r.price_change_1h == null ? "t-faint" : r.price_change_1h >= 0 ? "t-pos" : "t-neg"}`}>{r.price_change_1h == null ? "—" : `${r.price_change_1h >= 0 ? "+" : ""}${pct(r.price_change_1h, 1)}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
