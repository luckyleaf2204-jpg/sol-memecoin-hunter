"use client";
import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { statusLabel } from "@/lib/labels";
import { ago, num, pct, sol } from "@/lib/format";
import { Address, Card, CategoryBadge, ConfBadge, Empty, ErrorBox, Loading, Segmented, Td, Th, Tone } from "@/components/ui";

const SORTS = [
  { v: "score", label: "Smart Money Score" }, { v: "win_rate", label: "Win Rate" },
  { v: "median_buy", label: "Median Buy SOL" }, { v: "realized_pnl", label: "Realized PnL" },
  { v: "roi", label: "ROI" }, { v: "hit_5x", label: "5x Hit Rate" }, { v: "hit_10x", label: "10x Hit Rate" },
];

export default function Wallets() {
  const [period, setPeriod] = useState<number>(30);
  const [sort, setSort] = useState("score");
  const [official, setOfficial] = useState(true);
  const limit = official ? 10 : 100;
  const { data, error, loading } = useApi<any>(`/api/wallets/top?period=${period}&sort=${sort}&limit=${limit}&official=${official}`,
    60000, ["rankings_updated"]);
  const all = useApi<any>(`/api/wallets?period=${period}&page_size=100`, 60000, ["rankings_updated"]);
  const [addr, setAddr] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const rows = data?.rows || [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold">{official ? "Top 10 Smart Money Wallets" : "Bảng xếp hạng (gồm ví chưa đủ tin cậy)"}</h1>
        <div className="flex flex-wrap gap-2">
          <Segmented value={period} onChange={setPeriod} options={[{ v: 7, label: "7 ngày" }, { v: 30, label: "30 ngày" }, { v: 90, label: "90 ngày" }]} />
          <Segmented value={official ? "o" : "a"} onChange={(v) => setOfficial(v === "o")} options={[{ v: "o", label: "Chính thức" }, { v: "a", label: "Tất cả" }]} />
        </div>
      </div>
      <div className="flex items-center gap-2 overflow-x-auto pb-1 text-xs">
        <span className="shrink-0 text-slate-500">Sắp xếp:</span>
        {SORTS.map((s) => (
          <button key={s.v} onClick={() => setSort(s.v)} className={`shrink-0 rounded-full px-3 py-1 ${sort === s.v ? "bg-brand text-white" : "bg-slate-100 dark:bg-slate-800"}`}>{s.label}</button>
        ))}
      </div>
      {official && (
        <p className="text-xs text-slate-500">Top 10 chính thức chỉ gồm ví đạt mọi tiêu chí bắt buộc (Win Rate ≥ 65%, Median Buy ≥ 2 SOL, PnL & ROI dương sau phí,
          ≥ 5 vị thế đã đóng) với độ tin cậy dữ liệu High/Medium. Tính lại tự động khi có dữ liệu mới.</p>
      )}
      {loading && !data ? <Loading /> : error ? <ErrorBox msg={error} /> : rows.length === 0 ? (
        <Card><Empty>Chưa có ví nào đủ điều kiện trong {period} ngày. Hệ thống đang backfill lịch sử các ví ứng viên — xem trạng thái ở danh sách bên dưới.</Empty></Card>
      ) : (
        <>
          <div className="space-y-3 md:hidden">
            {rows.map((r: any, i: number) => (
              <Link key={r.wallet} href={`/wallets/${r.wallet}`} className="block rounded-2xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
                <div className="flex items-center justify-between">
                  <div className="text-lg font-bold">#{official ? r.official_rank : i + 1}</div>
                  <div className="text-right"><div className="text-2xl font-bold text-brand dark:text-teal-300">{num(r.score, 1)}</div><div className="text-[10px] text-slate-500">/ 100</div></div>
                </div>
                <div className="mt-1"><Address a={r.wallet} /></div>
                <div className="mt-2 flex flex-wrap gap-1"><CategoryBadge c={r.category} /><ConfBadge c={r.confidence} /></div>
                <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                  <M l="Win Rate" v={pct(r.win_rate, 0)} /><M l="Median Buy" v={sol(r.median_buy_sol)} /><M l="Realized PnL" v={<Tone v={r.realized_pnl_sol}>{sol(r.realized_pnl_sol, 1)}</Tone>} />
                  <M l="ROI" v={<Tone v={r.roi}>{pct(r.roi, 0)}</Tone>} /><M l="2x / 5x / 10x" v={`${pct(r.hit_rate_2x, 0)} · ${pct(r.hit_rate_5x, 0)} · ${pct(r.hit_rate_10x, 0)}`} /><M l="Last Active" v={ago(r.last_active_at)} />
                  <M l="Average Buy" v={sol(r.avg_buy_sol)} /><M l="Largest Buy" v={sol(r.largest_buy_sol)} /><M l="Vị thế đóng" v={`${r.closed_qualifying} (${r.wins}W/${r.losses}L)`} />
                </div>
              </Link>
            ))}
          </div>
          <Card className="hidden md:block">
            <div className="table-wrap">
              <table>
                <thead><tr>
                  <Th>Rank</Th><Th>Wallet</Th><Th active={sort === "score"} onClick={() => setSort("score")}>Score</Th>
                  <Th active={sort === "win_rate"} onClick={() => setSort("win_rate")}>Win Rate</Th>
                  <Th active={sort === "median_buy"} onClick={() => setSort("median_buy")}>Median Buy</Th>
                  <Th>Average Buy</Th><Th>Largest Buy</Th>
                  <Th active={sort === "realized_pnl"} onClick={() => setSort("realized_pnl")}>Realized PnL</Th>
                  <Th active={sort === "roi"} onClick={() => setSort("roi")}>ROI</Th><Th>2x Hit</Th>
                  <Th active={sort === "hit_5x"} onClick={() => setSort("hit_5x")}>5x Hit</Th>
                  <Th active={sort === "hit_10x"} onClick={() => setSort("hit_10x")}>10x Hit</Th><Th>Last Active</Th><Th>Tin cậy</Th><Th> </Th>
                </tr></thead>
                <tbody>
                  {rows.map((r: any, i: number) => (
                    <tr key={r.wallet}>
                      <Td className="font-bold">{official ? r.official_rank : i + 1}</Td>
                      <Td className="max-w-[280px] whitespace-normal"><Address a={r.wallet} href={`/wallets/${r.wallet}`} /></Td>
                      <Td className="font-semibold">{num(r.score, 1)}</Td><Td>{pct(r.win_rate, 0)}</Td>
                      <Td>{sol(r.median_buy_sol)}</Td><Td>{sol(r.avg_buy_sol)}</Td><Td>{sol(r.largest_buy_sol)}</Td>
                      <Td><Tone v={r.realized_pnl_sol}>{sol(r.realized_pnl_sol, 1)}</Tone></Td><Td><Tone v={r.roi}>{pct(r.roi, 0)}</Tone></Td>
                      <Td>{pct(r.hit_rate_2x, 0)}</Td><Td>{pct(r.hit_rate_5x, 0)}</Td><Td>{pct(r.hit_rate_10x, 0)}</Td>
                      <Td>{ago(r.last_active_at)}</Td><Td><ConfBadge c={r.confidence} /></Td>
                      <Td><Link href={`/wallets/${r.wallet}`} className="btn-ghost">Lịch sử</Link></Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      <Card title="Tất cả ví đang theo dõi" right={<span className="text-xs text-slate-500">{all.data ? `${num(all.data.total, 0)} ví` : ""}</span>}>
        <form className="mb-3 flex gap-2" onSubmit={async (e) => {
          e.preventDefault();
          try { await api("/api/wallets", { method: "POST", body: JSON.stringify({ address: addr }) }); setMsg("Đã thêm, đang backfill lịch sử 30 ngày."); setAddr(""); all.reload(); }
          catch (err: any) { setMsg(err.message); }
        }}>
          <input value={addr} onChange={(e) => setAddr(e.target.value)} placeholder="Thêm ví theo địa chỉ (vẫn được chấm điểm từ dữ liệu on-chain)" className="min-w-0 flex-1" />
          <button className="btn" disabled={!addr}>Thêm</button>
        </form>
        {msg && <div className="mb-2 text-xs text-slate-500">{msg}</div>}
        <div className="table-wrap">
          <table>
            <thead><tr><Th>Wallet</Th><Th>Trạng thái</Th><Th>Score</Th><Th>Win Rate</Th><Th>Median Buy</Th><Th>PnL</Th><Th>Vị thế đóng</Th><Th>Backfill</Th><Th>Nguồn</Th></tr></thead>
            <tbody>
              {(all.data?.rows || []).map((r: any) => (
                <tr key={r.address}>
                  <Td><Address a={r.address} href={`/wallets/${r.address}`} full={false} /></Td>
                  <Td>{statusLabel(r.status)}</Td><Td>{r.score !== null ? num(r.score, 1) : "—"}</Td><Td>{pct(r.win_rate, 0)}</Td>
                  <Td>{sol(r.median_buy_sol)}</Td><Td><Tone v={r.realized_pnl_sol}>{sol(r.realized_pnl_sol, 1)}</Tone></Td>
                  <Td>{r.closed_qualifying ?? "—"}</Td><Td>{r.backfill_status}</Td><Td>{r.discovery_source || "—"}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function M({ l, v }: { l: string; v: any }) {
  return <div><div className="text-[10px] text-slate-500">{l}</div><div className="font-medium tabular-nums">{v}</div></div>;
}

