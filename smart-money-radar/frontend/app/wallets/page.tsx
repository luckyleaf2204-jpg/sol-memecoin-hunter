"use client";
// Top Wallets — /api/wallets/top (ranking), /api/wallets/candidates (review checklist), /api/wallets
// (every tracked wallet, address search). Status: Official (in the official Top), Verified (passes
// every Smart Money rule), Tracking, Candidate. Sample size (closed positions) and data confidence
// are always shown next to the numbers.
import Link from "next/link";
import { useMemo, useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago, compact, num, pct, sol } from "@/lib/format";
import { BehaviorBadge } from "@/components/ui";
import { EmptyState, Icon, Panel, Seg, ShortAddr, SkelRows } from "@/components/terminal";

const SORTS: { v: string; label: string }[] = [
  { v: "score", label: "Score" }, { v: "win_rate", label: "Win Rate" }, { v: "realized_pnl", label: "Realized PnL" },
  { v: "roi", label: "ROI" }, { v: "copy_win_rate", label: "Copyability" }, { v: "median_buy", label: "Median Buy" },
  { v: "hit_5x", label: "5x" }, { v: "hit_10x", label: "10x" }, { v: "last_active", label: "Last Active" },
];

function statusOf(r: any): [string, string] {
  if (r.official_rank) return [`Official #${r.official_rank}`, "t-buy"];
  if (r.status === "smart") return ["Verified", "t-info"];
  if (r.status === "observed") return ["Không đạt", "t-mute"];
  if (r.status === "tracked") return ["Tracking", "t-mute"];
  return ["Candidate", "t-warn"];
}

function ConfPill({ c }: { c?: string | null }) {
  if (!c) return null;
  const cls = c === "HIGH" ? "t-buy" : c === "MEDIUM" ? "t-info" : "t-mute";
  return <span className={`t-pill ${cls}`}>{c === "HIGH" ? "Tin cậy cao" : c === "MEDIUM" ? "Tin cậy TB" : "Tin cậy thấp"}</span>;
}

function copyText(r: any) {
  const c = r.copy;
  if (!c || !c.positions) return "chưa đủ giá";
  return `${pct(c.win_rate, 0)} (${c.positions})`;
}

export default function Wallets() {
  const [period, setPeriod] = useState<number>(30);
  const [sort, setSort] = useState("score");
  const [official, setOfficial] = useState(true);
  const [human, setHuman] = useState(false);
  const [q, setQ] = useState("");
  const limit = official ? 10 : 200;
  const top = useApi<any>(`/api/wallets/top?period=${period}&sort=${sort}&limit=${limit}&official=${official}&human_only=${human}`, 60000, ["rankings_updated"]);
  const cand = useApi<any>(`/api/wallets/candidates?period=${period}&human_only=${human}`, 60000, ["rankings_updated"]);
  const ov = useApi<any>("/api/overview", 60000);
  const needle = q.trim();
  const rows = useMemo(() => (top.data?.rows || []).filter((r: any) => !needle || r.wallet.toLowerCase().includes(needle.toLowerCase())), [top.data, needle]);

  return (
    <div className="space-y-4">
      <div className="t-panel flex flex-wrap items-center gap-2 p-3">
        <Seg value={official ? "o" : "a"} onChange={(v) => setOfficial(v === "o")} options={[{ v: "o", label: "Official Top 10" }, { v: "a", label: "Tất cả đã chấm điểm" }]} />
        <Seg value={period} onChange={setPeriod} options={[{ v: 7, label: "7D" }, { v: 30, label: "30D" }, { v: 90, label: "90D" }]} />
        <Seg value={human ? "h" : "x"} onChange={(v) => setHuman(v === "h")} options={[{ v: "x", label: "Mọi ví" }, { v: "h", label: "Chỉ ví người" }]} />
        <label className="relative ml-auto flex min-w-[220px] flex-1 items-center sm:flex-none">
          <span className="t-faint pointer-events-none absolute left-3"><Icon name="search" size={15} /></span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm theo địa chỉ ví" className="w-full !pl-9 text-xs" />
        </label>
      </div>
      <div className="flex items-center gap-1.5 overflow-x-auto pb-1 text-xs">
        <span className="t-faint shrink-0">Sắp xếp:</span>
        {SORTS.map((s) => (
          <button key={s.v} onClick={() => setSort(s.v)} className={`t-pill shrink-0 ${sort === s.v ? "t-buy" : "t-mute"}`}>{s.label}</button>
        ))}
      </div>

      <Panel icon="trophy" title={official ? "Official Top 10 Smart Money" : "Bảng xếp hạng mọi ví đã chấm điểm"}
        sub={official ? "Chỉ ví đạt mọi tiêu chí: Win Rate ≥ 65%, Median Buy ≥ 2 SOL, PnL & ROI > 0, ≥ 5 vị thế đóng, độ tin cậy High/Medium, Score ≥ 65, giống người, copy trễ 1 phút vẫn thắng ≥ 60%"
          : "Gồm cả ví chưa đủ tiêu chí hoặc độ tin cậy thấp — xem cột Trạng thái"}>
        {!top.data && !top.error ? <SkelRows rows={8} cols={8} /> : top.error ? <EmptyState tone="error" title="Không tải được bảng xếp hạng" reason={top.error} /> : !rows.length ? (
          <EmptyState title={needle ? "Không có ví nào khớp địa chỉ đã nhập" : official ? `Chưa có ví nào đạt Official Top trong ${period} ngày` : "Chưa có ví nào được chấm điểm"}
            reason={needle ? "Thử nhập ít ký tự hơn, hoặc tìm trong danh sách \"Mọi ví đang theo dõi\" bên dưới." : official ? (
              <>Hiện có {num(ov.data?.wallets?.tracked ?? 0, 0)} ví đang theo dõi, {num(ov.data?.wallets?.backfilling ?? 0, 0)} ví đang chờ đọc lịch sử. Ví chưa vào Top vì
                thiếu vị thế đã đóng, lịch sử chưa đọc đủ, hoặc là bot / copy không có lời. Xem bảng kiểm &quot;Ứng viên Top 20&quot; bên dưới để biết từng ví thiếu mục nào, hoặc chọn &quot;Tất cả đã chấm điểm&quot;.</>
            ) : "Hệ thống chấm điểm sau khi đọc xong lịch sử ví và có ít nhất 1 vị thế đã đóng."} />
        ) : (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="t-table">
                <thead><tr><th>#</th><th>Wallet</th><th>Trạng thái</th><th className="text-right">Score</th><th className="text-right">Win Rate</th><th className="text-right">Realized PnL</th>
                  <th className="text-right">ROI</th><th className="text-right">Vị thế đóng</th><th className="text-right">Median Buy</th><th className="text-right">Copy trễ 1 phút</th><th>Kiểu</th><th>Tin cậy</th><th className="text-right">Last Active</th></tr></thead>
                <tbody>
                  {rows.map((r: any, i: number) => {
                    const [sl, sc] = statusOf(r);
                    return (
                      <tr key={r.wallet}>
                        <td className="t-faint">{official ? r.official_rank : i + 1}</td>
                        <td><ShortAddr a={r.wallet} href={`/wallets/${r.wallet}`} n={5} /></td>
                        <td><span className={`t-pill ${sc}`}>{sl}</span></td>
                        <td className="num text-right font-bold" style={{ color: "var(--buy)" }}>{num(r.score, 1)}</td>
                        <td className="num text-right">{pct(r.win_rate, 0)}</td>
                        <td className={`num text-right ${Number(r.realized_pnl_sol) >= 0 ? "t-pos" : "t-neg"}`}>{Number(r.realized_pnl_sol) >= 0 ? "+" : ""}{num(r.realized_pnl_sol, 2)}</td>
                        <td className={`num text-right ${Number(r.roi) >= 0 ? "t-pos" : "t-neg"}`}>{pct(r.roi, 0)}</td>
                        <td className="num text-right">{num(r.closed_qualifying, 0)} <span className="t-faint text-[10px]">({r.wins}W/{r.losses}L)</span></td>
                        <td className="num text-right">{sol(r.median_buy_sol)}</td>
                        <td className="num text-right">{copyText(r)}</td>
                        <td><BehaviorBadge b={r.behavior} /></td>
                        <td><ConfPill c={r.confidence} /></td>
                        <td className="t-muted text-right">{ago(r.last_active_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="space-y-2 px-3 pb-3 md:hidden">
              {rows.map((r: any, i: number) => {
                const [sl, sc] = statusOf(r);
                return (
                  <Link key={r.wallet} href={`/wallets/${r.wallet}`} className="block rounded-xl border p-3" style={{ borderColor: "var(--line)", background: "var(--bg)" }}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2"><b className="t-faint">#{official ? r.official_rank : i + 1}</b><ShortAddr a={r.wallet} n={5} /></div>
                        <div className="mt-1.5 flex flex-wrap gap-1"><span className={`t-pill ${sc}`}>{sl}</span><ConfPill c={r.confidence} /><BehaviorBadge b={r.behavior} /></div>
                      </div>
                      <div className="text-right">
                        <div className="num text-2xl font-extrabold" style={{ color: "var(--buy)" }}>{num(r.score, 1)}</div>
                        <div className={`num text-sm font-bold ${Number(r.realized_pnl_sol) >= 0 ? "t-pos" : "t-neg"}`}>{Number(r.realized_pnl_sol) >= 0 ? "+" : ""}{num(r.realized_pnl_sol, 2)} SOL</div>
                      </div>
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
                      <M l="Win Rate" v={`${pct(r.win_rate, 0)} (${r.closed_qualifying})`} /><M l="ROI" v={pct(r.roi, 0)} /><M l="Median Buy" v={sol(r.median_buy_sol)} />
                      <M l="Copy trễ 1 phút" v={copyText(r)} /><M l="5x / 10x" v={`${pct(r.hit_rate_5x, 0)} · ${pct(r.hit_rate_10x, 0)}`} /><M l="Last Active" v={ago(r.last_active_at)} />
                    </div>
                  </Link>
                );
              })}
            </div>
          </>
        )}
      </Panel>

      <CandidatePanel q={cand} />
      <AllWalletsPanel period={period} />
    </div>
  );
}

function M({ l, v }: { l: string; v: any }) {
  return <div><div className="t-faint text-[10px]">{l}</div><div className="num font-semibold" style={{ color: "var(--text)" }}>{v}</div></div>;
}

function CandidatePanel({ q }: { q: any }) {
  return (
    <Panel icon="wallets" title="Ứng viên Top 20 — bảng kiểm trước khi vào Top 10" sub="20 ví điểm cao nhất đã đọc lịch sử on-chain; chỉ ví qua MỌI mục (và tốt nhất trong cụm ví cùng chủ) mới vào Official Top">
      {!q.data && !q.error ? <SkelRows rows={6} cols={4} /> : q.error ? <EmptyState tone="error" title="Không tải được" reason={q.error} /> : !q.data.rows.length ? (
        <EmptyState title="Chưa có ví nào đọc xong lịch sử" reason="Xem tiến độ ở trang Data Health." />
      ) : (
        <div className="space-y-2 px-3 pb-3">
          {q.data.rows.map((r: any, i: number) => {
            const passed = r.checks.filter((c: any) => c.ok).length;
            const all = r.checks.length;
            return (
              <details key={r.wallet} className="rounded-xl border p-2.5" style={{ borderColor: "var(--line)", background: "var(--bg)" }}>
                <summary className="flex cursor-pointer flex-wrap items-center gap-2 text-sm">
                  <span className="t-faint w-6 font-bold">{i + 1}</span>
                  <ShortAddr a={r.wallet} href={`/wallets/${r.wallet}`} n={5} />
                  <b className="num" style={{ color: "var(--buy)" }}>{num(r.score, 1)}</b>
                  <BehaviorBadge b={r.behavior} />
                  <span className="t-muted text-xs">WR {pct(r.win_rate, 0)} ({r.closed_qualifying} vị thế) · copy {copyText(r)} · {sol(r.realized_pnl_sol, 1)}</span>
                  <span className={`t-pill ml-auto ${passed === all ? "t-buy" : "t-mute"}`}>{passed === all ? (r.official_rank ? `Top #${r.official_rank}` : "Đạt") : `${passed}/${all} mục`}</span>
                </summary>
                <ul className="mt-2 grid gap-1 text-xs md:grid-cols-2">
                  {r.checks.map((c: any) => <li key={c.key} className={c.ok ? "t-pos" : "t-neg"}>{c.ok ? "✓" : "✗"} {c.label}</li>)}
                </ul>
              </details>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

function AllWalletsPanel({ period }: { period: number }) {
  const [q, setQ] = useState("");
  const [addr, setAddr] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const qs = q.trim().length >= 3 ? `&q=${encodeURIComponent(q.trim())}` : "";
  const all = useApi<any>(`/api/wallets?period=${period}&page_size=100${qs}`, 60000, ["rankings_updated"]);
  const ST: Record<string, string> = { smart: "Verified", tracked: "Tracking", candidate: "Candidate", observed: "Không đạt / bỏ qua" };
  return (
    <Panel icon="search" title="Mọi ví đang theo dõi" sub={all.data ? `${num(all.data.total, 0)} ví${qs ? " khớp tìm kiếm" : ""}` : "Đang tải…"}>
      <div className="flex flex-wrap gap-2 px-4 pb-3">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm ví (nhập ≥ 3 ký tự đầu địa chỉ)" className="min-w-0 flex-1 text-xs" />
        <form className="flex min-w-0 flex-1 gap-2" onSubmit={async (e) => {
          e.preventDefault();
          try { await api("/api/wallets", { method: "POST", body: JSON.stringify({ address: addr }) }); setMsg("Đã thêm, đang đọc lịch sử."); setAddr(""); all.reload(); }
          catch (err: any) { setMsg(err.message); }
        }}>
          <input value={addr} onChange={(e) => setAddr(e.target.value)} placeholder="Thêm ví theo địa chỉ" className="min-w-0 flex-1 text-xs" />
          <button className="btn !py-1.5 text-xs" disabled={!addr}>Thêm</button>
        </form>
      </div>
      {msg && <div className="t-muted px-4 pb-2 text-xs">{msg}</div>}
      {!all.data && !all.error ? <SkelRows rows={6} cols={6} /> : all.error ? <EmptyState tone="error" title="Không tải được" reason={all.error} /> : !all.data.rows.length ? (
        <EmptyState title="Không có ví nào" reason={qs ? "Không ví nào bắt đầu bằng chuỗi đã nhập." : undefined} />
      ) : (
        <div className="max-h-[520px] overflow-auto">
          <table className="t-table">
            <thead><tr><th>Wallet</th><th>Trạng thái</th><th>Kiểu</th><th className="text-right">Score</th><th className="text-right">Win Rate</th><th className="text-right">Copy</th><th className="text-right">PnL</th><th className="text-right">Vị thế đóng</th><th>Lịch sử</th><th>Nguồn</th></tr></thead>
            <tbody>
              {all.data.rows.map((r: any) => (
                <tr key={r.address}>
                  <td><ShortAddr a={r.address} href={`/wallets/${r.address}`} n={5} /></td>
                  <td className="t-muted">{r.high_frequency ? "Bỏ qua: tần suất cao" : ST[r.status] || r.status}</td>
                  <td><BehaviorBadge b={r.behavior} /></td>
                  <td className="num text-right">{r.score !== null ? num(r.score, 1) : "—"}</td>
                  <td className="num text-right">{pct(r.win_rate, 0)}</td>
                  <td className="num text-right">{pct(r.copy_win_rate, 0)}</td>
                  <td className={`num text-right ${Number(r.realized_pnl_sol) >= 0 ? "t-pos" : "t-neg"}`}>{r.realized_pnl_sol == null ? "—" : compact(r.realized_pnl_sol, 2)}</td>
                  <td className="num text-right">{r.closed_qualifying ?? "—"}</td>
                  <td className="t-muted">{({ complete: "Đủ", partial: "Một phần", queued: "Đang chờ", running: "Đang đọc", failed: "Lỗi", none: "Chưa đọc" } as any)[r.backfill_status] || r.backfill_status}</td>
                  <td className="t-faint">{r.discovery_source || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="t-faint px-4 py-2 text-[11px]">Chi tiết từng vị thế, lịch sử lệnh, thời gian giữ lệnh và mức độ đầy đủ dữ liệu: bấm vào địa chỉ ví.</div>
    </Panel>
  );
}
