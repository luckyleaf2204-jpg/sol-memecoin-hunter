"use client";
import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago, dateTime, num, pct, sol } from "@/lib/format";
import { BehaviorBadge, Card, Empty, ErrorBox, Loading, Pill, Td, Th } from "@/components/ui";

export default function Kol() {
  const list = useApi<any>("/api/watchlist", 30000, ["alert"]);
  const feed = useApi<any>("/api/watchlist/feed?limit=150", 15000, ["alert"]);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const rows = list.data?.rows || [];
  const kols = rows.filter((r: any) => r.confirmed);
  const suggested = rows.filter((r: any) => !r.confirmed);

  async function add() {
    try {
      const r = await api("/api/watchlist", { method: "POST", body: JSON.stringify({ lines: text }) });
      setMsg(`Đã thêm ${r.added} ví.${r.errors.length ? " Lỗi: " + r.errors.join("; ") : ""}`);
      setText(""); list.reload();
    } catch (e: any) { setMsg(e.message); }
  }
  async function act(path: string, method: string) {
    try { await api(path, { method }); list.reload(); } catch (e: any) { setMsg(e.message); }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Ví KOL / người gọi kèo</h1>
        <p className="mt-1 text-xs text-slate-500">Ví trong danh sách này mua token nào là có cảnh báo Telegram ngay (thường 2–5 giây sau khi giao dịch
          được xác nhận), kèm thứ tự: KOL thứ mấy mua token đó, token ra mắt được bao lâu. Khi ví đó bán ≥ 50% / ≥ 90% sẽ có cảnh báo bán.
          Tên và tài khoản X là nhãn bạn nhập hoặc lấy từ trang theo dõi KOL công khai; hệ thống không tự khẳng định ai là chủ ví.</p>
      </div>

      <Card title="Mua/bán gần nhất của ví KOL (3 ngày)">
        {!feed.data ? <Loading /> : feed.error ? <ErrorBox msg={feed.error} /> : feed.data.rows.length === 0 ? (
          <Empty>Chưa thấy giao dịch nào của ví trong danh sách. Hệ thống đang theo dõi trực tiếp.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><Th>Lúc</Th><Th>KOL</Th><Th>Lệnh</Th><Th>SOL</Th><Th>Token</Th><Th>MC lúc khớp</Th><Th>Token tuổi</Th></tr></thead>
              <tbody>
                {feed.data.rows.map((t: any) => (
                  <tr key={t.signature + t.wallet + t.tx_type}>
                    <Td>{dateTime(t.block_time)}</Td>
                    <Td><Link href={`/wallets/${t.wallet}`} className="text-brand dark:text-teal-300">{t.label}</Link></Td>
                    <Td><span className={t.tx_type === "buy" ? "text-emerald-600" : "text-rose-600"}>{t.tx_type === "buy" ? "Mua" : "Bán"}</span></Td>
                    <Td>{num(t.sol_amount, 2)}</Td>
                    <Td><Link href={`/tokens/${t.mint}`} className="text-brand dark:text-teal-300">{t.symbol || t.mint.slice(0, 6) + "…"}</Link></Td>
                    <Td>{t.market_cap_sol ? sol(t.market_cap_sol, 0) : "—"}</Td>
                    <Td>{t.created_at ? ageAt(t.created_at, t.block_time) : "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={`Danh sách theo dõi (${kols.length} ví)`}>
        {!list.data ? <Loading /> : kols.length === 0 ? <Empty>Chưa có ví nào.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><Th>Tên</Th><Th>X</Th><Th>Ví</Th><Th>Loại</Th><Th>Mua gần nhất</Th><Th>Token 7 ngày</Th><Th>Win Rate</Th><Th>Copy trễ 1 phút</Th><Th>Kiểu</Th><Th> </Th></tr></thead>
              <tbody>
                {kols.map((r: any) => (
                  <tr key={r.address}>
                    <Td className="font-medium">{r.label}</Td>
                    <Td>{r.x_handle ? <a href={`https://x.com/${r.x_handle}`} target="_blank" rel="noreferrer" className="text-brand dark:text-teal-300">@{r.x_handle}</a> : "—"}</Td>
                    <Td><Link href={`/wallets/${r.address}`} className="font-mono text-[12px] text-brand dark:text-teal-300">{r.address.slice(0, 6)}…{r.address.slice(-4)}</Link></Td>
                    <Td>{r.kind === "side_wallet" ? <Pill className="bg-amber-100 text-amber-800">Ví phụ</Pill> : "KOL"}</Td>
                    <Td>{ago(r.last_buy_at)}</Td><Td>{r.tokens_7d ?? 0}</Td>
                    <Td>{r.closed_qualifying ? `${pct(r.win_rate, 0)} (${r.closed_qualifying})` : r.backfill_status === "complete" ? "chưa có vị thế ≥ 2 SOL" : "đang đọc lịch sử"}</Td>
                    <Td>{pct(r.copy_win_rate, 0)}</Td><Td><BehaviorBadge b={r.behavior} /></Td>
                    <Td><button className="btn-ghost" onClick={() => confirm(`Xoá ${r.label} khỏi danh sách?`) && act(`/api/watchlist/${r.address}`, "DELETE")}>Xoá</button></Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {suggested.length > 0 && (
        <Card title={`Ví phụ nghi vấn (${suggested.length}) — được ví KOL nạp SOL trực tiếp rồi giao dịch pump.fun`}>
          <ul className="space-y-2 text-sm">
            {suggested.map((r: any) => (
              <li key={r.address} className="flex flex-wrap items-center gap-2">
                <Link href={`/wallets/${r.address}`} className="font-mono text-[12px] text-brand dark:text-teal-300">{r.address.slice(0, 6)}…{r.address.slice(-4)}</Link>
                <span className="text-xs text-slate-500">{r.label} · nhận {num(r.evidence?.funded_sol, 2)} SOL
                  {r.evidence?.signature && <> · <a className="underline" href={`https://solscan.io/tx/${r.evidence.signature}`} target="_blank" rel="noreferrer">giao dịch nạp</a></>}</span>
                <button className="btn" onClick={() => act(`/api/watchlist/${r.address}/confirm`, "POST")}>Theo dõi</button>
                <button className="btn-ghost" onClick={() => act(`/api/watchlist/${r.address}`, "DELETE")}>Bỏ</button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Thêm ví KOL">
        <p className="mb-2 text-xs text-slate-500">Mỗi dòng một ví: <code>địa_chỉ_ví Tên @tài_khoản_X</code> (tên và @X không bắt buộc). Dán được nhiều dòng một lần.</p>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} className="w-full font-mono text-[12px]"
          placeholder={"CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o Cented\n2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f Cupsey"} />
        <div className="mt-2 flex items-center gap-2">
          <button className="btn" disabled={!text.trim()} onClick={add}>Thêm vào danh sách</button>
          {msg && <span className="text-xs text-slate-500">{msg}</span>}
        </div>
      </Card>
    </div>
  );
}

function ageAt(created: string, at: string) {
  const s = (new Date(at).getTime() - new Date(created).getTime()) / 1000;
  if (s < 0) return "—";
  if (s < 90) return `${Math.round(s)} giây`;
  if (s < 5400) return `${Math.round(s / 60)} phút`;
  if (s < 172800) return `${(s / 3600).toFixed(1).replace(".", ",")} giờ`;
  return `${Math.round(s / 86400)} ngày`;
}
