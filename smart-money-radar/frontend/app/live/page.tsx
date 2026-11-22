"use client";
// Live Feed — newest trades stored by the system (/api/trades/recent), polled every 4 s.
// Each row shows the on-chain block time and the delay until the system stored it.
import Link from "next/link";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { num } from "@/lib/format";
import { EmptyState, Icon, Panel, Seg, ShortAddr, SidePill, SkelRows } from "@/components/terminal";

const VERIF: Record<string, [string, string]> = {
  verified: ["Đã xác minh", "t-buy"], partially_verified: ["Một phần", "t-warn"], unverified: ["Chưa xác minh", "t-mute"],
};

export default function LiveFeed() {
  const [scope, setScope] = useState("tracked");
  const [side, setSide] = useState("");
  const [minSol, setMinSol] = useState(0);
  const [paused, setPaused] = useState(false);
  const q = useApi<any>(`/api/trades/recent?scope=${scope}&limit=100&min_sol=${minSol}${side ? `&side=${side}` : ""}`, paused ? 0 : 4000);
  const rows = q.data?.rows || [];
  const solUsd = q.data?.sol_usd;
  const delays = rows.map((r: any) => Number(r.delay_s)).filter((x: number) => x >= 0).sort((a: number, b: number) => a - b);
  const med = delays.length ? delays[Math.floor(delays.length / 2)] : null;
  const newest = rows[0]?.block_time ? (Date.now() - new Date(rows[0].block_time).getTime()) / 1000 : null;
  const state = paused ? ["warn", "Tạm dừng"] : newest == null ? ["warn", "Chưa có lệnh"] : newest > 600 ? ["warn", `Lệnh mới nhất ${Math.round(newest / 60)} phút trước`] : ["ok", "Trực tiếp"];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Seg value={scope} onChange={setScope} options={[{ v: "smart", label: "Smart Money" }, { v: "tracked", label: "Đang theo dõi" }, { v: "kol", label: "KOL" }, { v: "all", label: "Tất cả ≥ 1 SOL" }]} />
        <Seg value={side} onChange={setSide} options={[{ v: "", label: "Mua + Bán" }, { v: "buy", label: "Mua" }, { v: "sell", label: "Bán" }]} />
        <label className="t-muted flex items-center gap-2 text-xs">Tối thiểu
          <select value={minSol} onChange={(e) => setMinSol(Number(e.target.value))} className="!py-1 text-xs">
            {[0, 0.5, 1, 2, 5, 10].map((v) => <option key={v} value={v}>{v} SOL</option>)}
          </select>
        </label>
      </div>

      <Panel icon="feed" title="Live Transaction Feed" sub="Giao dịch pump.fun / PumpSwap hệ thống tự giải mã từ blockchain"
        right={<>
          <button className="radar-icon-button" onClick={() => setPaused(!paused)} aria-label={paused ? "Tiếp tục" : "Tạm dừng"}><Icon name={paused ? "play" : "pause"} size={15} /></button>
          <span className={`radar-status ${state[0]}`}><span className="radar-dot" />{state[1]}</span>
        </>}>
        <div className="t-faint px-4 pb-2 text-[11px]">
          Độ trễ trung vị (khớp trên chain → hệ thống ghi): <b className="num" style={{ color: "var(--text)" }}>{med != null ? `${num(med, 1)} giây` : "—"}</b> trên {rows.length} lệnh đang hiển thị.
          {scope === "all" && " Nhóm \"Tất cả\" chỉ gồm lệnh ≥ 1 SOL mà stream lưu lại (lệnh nhỏ hơn của ví không theo dõi không được lưu)."}
        </div>
        {!q.data && !q.error ? <SkelRows rows={10} cols={7} /> : q.error ? <EmptyState tone="error" title="Không tải được" reason={q.error} /> : !rows.length ? (
          <EmptyState title="Chưa có giao dịch phù hợp bộ lọc" reason={scope === "smart" ? "Chưa có ví đạt chuẩn Smart Money; chọn \"Đang theo dõi\" hoặc \"KOL\"." : "Thử giảm số SOL tối thiểu hoặc đổi nhóm ví."} />
        ) : (
          <div className="overflow-x-auto">
            <table className="t-table">
              <thead><tr><th>Thời gian</th><th>Wallet</th><th>Token</th><th>Loại</th><th className="text-right">SOL</th><th className="text-right">Giá (USD)</th><th className="text-right">MC (SOL)</th><th>Sàn</th><th>Xác minh</th><th className="text-right">Trễ</th><th>Tx</th></tr></thead>
              <tbody>
                {rows.map((t: any) => {
                  const [vl, vc] = VERIF[t.verification_status] || [t.verification_status, "t-mute"];
                  return (
                    <tr key={t.signature + t.wallet + t.tx_type + t.mint}>
                      <td className="num t-muted">{new Date(t.block_time).toLocaleTimeString("vi-VN", { hour12: false })}</td>
                      <td>{t.kol_label ? <Link href={`/wallets/${t.wallet}`} className="font-semibold" style={{ color: "var(--text)" }}>{t.kol_label}</Link> : <ShortAddr a={t.wallet} href={`/wallets/${t.wallet}`} />}</td>
                      <td><Link href={`/tokens/${t.mint}`} className="font-semibold" style={{ color: "var(--text)" }}>{t.symbol ? `$${t.symbol}` : `${t.mint.slice(0, 4)}…${t.mint.slice(-4)}`}</Link></td>
                      <td><SidePill side={t.tx_type} /></td>
                      <td className="num text-right">{num(t.sol_amount, 3)}</td>
                      <td className="num t-muted text-right">{t.effective_price_sol && solUsd ? `$${Number(t.effective_price_sol * solUsd).toPrecision(3)}` : "—"}</td>
                      <td className="num t-muted text-right">{t.market_cap_sol ? num(t.market_cap_sol, 0) : "—"}</td>
                      <td className="t-muted">{t.venue === "pumpswap" ? "PumpSwap" : t.venue?.startsWith("pump_curve") ? "Pump.fun" : t.venue}</td>
                      <td><span className={`t-pill ${vc}`}>{vl}</span></td>
                      <td className="num t-muted text-right">{Number(t.delay_s) >= 0 ? `${num(t.delay_s, 1)}s` : "—"}</td>
                      <td><a href={`https://solscan.io/tx/${t.signature}`} target="_blank" rel="noreferrer" className="t-faint" aria-label="Solscan"><Icon name="external" size={14} /></a></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
