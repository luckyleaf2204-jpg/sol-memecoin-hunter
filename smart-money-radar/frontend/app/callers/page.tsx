"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, useApi } from "@/lib/api";
import { ago, dateTime, multiple, num, pct, price, usd } from "@/lib/format";
import { Card, Empty, ErrorBox, Loading, Pill, Segmented, Td, Th } from "@/components/ui";

const GRADE_STYLE: Record<string, string> = {
  A: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  B: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  C: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  D: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
};
const RANGES = [{ v: 24, label: "24 giờ" }, { v: 168, label: "7 ngày" }, { v: 720, label: "30 ngày" }, { v: 0, label: "Tất cả" }];
const short = (a: string) => `${a.slice(0, 5)}…${a.slice(-4)}`;

function Grade({ g, why }: { g: string; why?: string }) {
  return <span title={why}><Pill className={GRADE_STYLE[g] || ""}>{g}</Pill></span>;
}

function Mult({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-slate-400">—</span>;
  const cls = v >= 2 ? "text-emerald-600 font-semibold" : v >= 1 ? "text-emerald-600" : "text-rose-600";
  return <span className={cls}>{multiple(v)}</span>;
}

export default function Callers() {
  const [range, setRange] = useState<number>(168);
  const [channel, setChannel] = useState<number | "">("");
  const [token, setToken] = useState("");
  const [minMult, setMinMult] = useState<string>("");
  const [view, setView] = useState<"latest" | "pumping" | "rug" | "history">("latest");
  const [input, setInput] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const since = range ? `since_h=${range}` : "";

  const chans = useApi<any[]>("/api/callers/channels", 30000);
  const board = useApi<any>(`/api/callers/leaderboard?${since}`, 60000);
  const q = [since, channel ? `channel=${channel}` : "", token ? `mint=${encodeURIComponent(token)}` : "",
    minMult ? `min_mult=${minMult}` : "", view === "rug" ? "only=rug" : view === "history" ? "only=valid" : ""].filter(Boolean).join("&");
  const calls = useApi<any[]>(view === "pumping" ? "/api/callers/pumping" : `/api/callers/calls?limit=200&${q}`, 20000);
  const series = useApi<any[]>(`/api/callers/roi-series?${since}${channel ? `&channel=${channel}` : ""}`, 60000);
  const sm = useApi<any>(`/api/callers/smart-money?${since}`, 60000);

  async function add() {
    try {
      await api("/api/callers/channels", { method: "POST", body: JSON.stringify({ username: input }) });
      setMsg(`Đã thêm ${input}. Bài đăng sẽ được đọc trong vòng 1 phút.`); setInput(""); chans.reload();
    } catch (e: any) { setMsg(e.message); }
  }
  async function act(path: string, method: string) {
    try { await api(path, { method }); chans.reload(); board.reload(); } catch (e: any) { setMsg(e.message); }
  }

  const chartData = useMemo(() => (series.data || []).filter((r) => r.roi_24h != null).map((r) => ({
    t: dateTime(r.called_at), roi: Math.round(r.roi_24h * 1000) / 10, who: r.username, rug: r.rugged,
  })), [series.data]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Telegram Caller Tracker</h1>
        <p className="mt-1 text-xs text-slate-500">
          Theo dõi kênh Telegram gọi kèo Solana / pump.fun và chấm điểm bằng giá thật sau mỗi kèo. Giá vào lệnh là giá chụp lúc phát hiện bài
          (kèo trực tiếp) hoặc nến 1 phút của chính phút đăng bài (kèo lịch sử). Không bao giờ dùng giá hiện tại cho thời điểm cũ. Chỉ kèo đầu tiên
          của một kênh với một token được tính điểm; kênh chưa đủ {board.data?.min_sample ?? 30} kèo có kết quả 24 giờ luôn ở hạng B (chưa đủ dữ liệu).
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Segmented value={range} options={RANGES} onChange={setRange} />
        <select className="w-auto" value={channel} onChange={(e) => setChannel(e.target.value ? Number(e.target.value) : "")}>
          <option value="">Tất cả kênh</option>
          {(chans.data || []).map((c: any) => <option key={c.id} value={c.id}>@{c.username}</option>)}
        </select>
        <input className="w-56" placeholder="Lọc token: CA hoặc ticker" value={token} onChange={(e) => setToken(e.target.value)} />
        <select className="w-auto" value={minMult} onChange={(e) => setMinMult(e.target.value)}>
          <option value="">Mọi mức lợi nhuận</option><option value="2">Đạt ≥ x2</option><option value="3">Đạt ≥ x3</option><option value="5">Đạt ≥ x5</option>
        </select>
      </div>

      <Card title="Bảng xếp hạng Caller" right={<span className="text-xs text-slate-500">Thắng = chạm x{board.data?.win_multiple ?? 2} trong 24 giờ</span>}>
        {!board.data ? <Loading /> : board.error ? <ErrorBox msg={board.error} /> : board.data.channels.length === 0 ? (
          <Empty>Chưa có kênh nào. Thêm kênh ở phần "Kênh đang theo dõi".</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><Th>Hạng</Th><Th>Kênh</Th><Th>Tổng kèo</Th><Th>CA hợp lệ</Th><Th>Đã chấm</Th><Th>Win Rate</Th><Th>X2</Th><Th>X3</Th>
                <Th>ROI 24h TB</Th><Th>ROI 24h trung vị</Th><Th>Drawdown trung vị</Th><Th>Rug</Th><Th>Gọi sớm</Th><Th>Gọi trễ + xả</Th>
                <Th>TG tới x2</Th><Th>Tương quan SM</Th></tr></thead>
              <tbody>
                {board.data.channels.map((c: any) => (
                  <tr key={c.id}>
                    <Td><Grade g={c.grade} why={c.grade_reason} /></Td>
                    <Td><a href={`https://t.me/${c.username}`} target="_blank" rel="noreferrer" className="text-brand dark:text-teal-300">@{c.username}</a>
                      {!c.enabled && <span className="ml-1 text-[11px] text-slate-400">(tạm dừng)</span>}</Td>
                    <Td>{c.total_calls}</Td><Td>{c.valid_calls}</Td>
                    <Td>{c.scored_calls}{c.scored_calls < board.data.min_sample && <span className="text-[11px] text-slate-400"> /{board.data.min_sample}</span>}</Td>
                    <Td>{pct(c.win_rate, 0)}</Td><Td>{pct(c.x2_rate, 0)}</Td><Td>{pct(c.x3_rate, 0)}</Td>
                    <Td>{pct(c.avg_roi_24h, 0)}</Td><Td>{pct(c.median_roi_24h, 0)}</Td><Td>{pct(c.median_max_drawdown, 0)}</Td>
                    <Td>{pct(c.rug_rate, 0)}</Td><Td>{pct(c.early_call_rate, 0)}</Td><Td>{pct(c.late_dump_rate, 0)}</Td>
                    <Td>{c.avg_minutes_to_x2 != null ? `${num(c.avg_minutes_to_x2, 0)} phút` : "—"}</Td>
                    <Td>{c.caller_sm_correlation != null ? num(c.caller_sm_correlation, 2) : "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Kèo" right={<Segmented value={view} onChange={setView} options={[
        { v: "latest", label: "Mới nhất" }, { v: "pumping", label: "Đang tăng (24h)" }, { v: "rug", label: "Đã rug" }, { v: "history", label: "Lịch sử kết quả" }]} />}>
        {!calls.data ? <Loading /> : calls.error ? <ErrorBox msg={calls.error} /> : calls.data.length === 0 ? <Empty>Chưa có kèo phù hợp bộ lọc.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><Th>Lúc gọi</Th><Th>Kênh</Th><Th>Token</Th><Th>Loại</Th><Th>Giá lúc gọi</Th><Th>MC lúc gọi</Th>
                {view === "pumping" ? <Th>Hiện tại</Th> : <><Th>ATH</Th><Th>ROI 24h</Th><Th>Drawdown</Th><Th>x2 / x3</Th></>}
                <Th>Giá đã tăng trước khi gọi (1h)</Th><Th>SM mua trước / sau</Th><Th>SM bán 30'</Th><Th>Trạng thái</Th></tr></thead>
              <tbody>
                {calls.data.map((c: any) => (
                  <tr key={c.id}>
                    <Td>{dateTime(c.called_at)}<div className="text-[11px] text-slate-400">{c.live ? `phát hiện sau ${num((new Date(c.detected_at).getTime() - new Date(c.called_at).getTime()) / 1000, 0)}s` : "lịch sử"}</div></Td>
                    <Td><a href={`https://t.me/${c.username}/${c.msg_id}`} target="_blank" rel="noreferrer" className="text-brand dark:text-teal-300">@{c.username}</a></Td>
                    <Td><Link href={`/tokens/${c.mint}`} className="text-brand dark:text-teal-300">{c.ticker ? `$${c.ticker}` : (c.name || short(c.mint))}</Link>
                      <div className="font-mono text-[11px] text-slate-400">{short(c.mint)}</div></Td>
                    <Td>{c.status === "invalid" ? <Pill className="bg-slate-200 text-slate-600">CA không hợp lệ</Pill> : c.is_repeat ? <Pill className="bg-slate-100 text-slate-600">gọi lại</Pill> : "lần đầu"}</Td>
                    <Td>{price(c.entry_price_usd ?? c.entry_live_usd)}<div className="text-[11px] text-slate-400">{c.entry_source || (c.entry_live_usd ? "live_snapshot" : "")}</div></Td>
                    <Td>{c.entry_mc_usd ? usd(c.entry_mc_usd) : c.stated_mc_usd ? <span title="Theo bài đăng">{usd(c.stated_mc_usd)}*</span> : "—"}</Td>
                    {view === "pumping" ? <Td><Mult v={c.multiple_now} /><div className="text-[11px] text-slate-400">{c.last_offset != null ? `mốc ${c.last_offset} phút` : ""}</div></Td> : <>
                      <Td><Mult v={c.ath_multiple} /></Td>
                      <Td>{c.roi_24h != null ? <span className={c.roi_24h >= 0 ? "text-emerald-600" : "text-rose-600"}>{pct(c.roi_24h, 0)}</span> : "—"}</Td>
                      <Td>{pct(c.max_drawdown, 0)}</Td>
                      <Td>{c.x2_at ? `${num((new Date(c.x2_at).getTime() - new Date(c.called_at).getTime()) / 60000, 0)}'` : "—"} / {c.x3_at ? `${num((new Date(c.x3_at).getTime() - new Date(c.called_at).getTime()) / 60000, 0)}'` : "—"}</Td>
                    </>}
                    <Td>{pct(c.pre_call_run, 0)}</Td>
                    <Td>{c.wallets_known ? `${c.buyers_before} / ${c.buyers_after}` : "—"}</Td>
                    <Td>{c.wallets_known ? (c.sellers_30m ? <span className="text-rose-600">{c.sellers_30m} ví</span> : "0") : "—"}</Td>
                    <Td>{c.rugged ? <Pill className="bg-rose-100 text-rose-700" >rug</Pill> : c.finalized ? "đủ 24h" : c.status === "unpriced" ? "không định giá được" : c.status === "invalid" ? "—" : "đang theo dõi"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="ROI 24 giờ từng kèo (kèo đầu tiên, đã đủ 24 giờ)">
          {!series.data ? <Loading /> : chartData.length === 0 ? <Empty>Chưa có kèo nào đủ 24 giờ.</Empty> : (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="t" hide />
                  <YAxis tickFormatter={(v) => `${v}%`} width={50} />
                  <ReferenceLine y={0} stroke="#94a3b8" />
                  <Tooltip formatter={(v: any) => [`${v}%`, "ROI 24h"]} labelFormatter={(l, p: any) => `${l} · @${p?.[0]?.payload?.who ?? ""}`} />
                  <Bar dataKey="roi">{chartData.map((d, i) => <Cell key={i} fill={d.rug ? "#e11d48" : d.roi >= 0 ? "#10b981" : "#f97316"} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card title="Caller ↔ Smart Money" right={<span className="text-xs text-slate-500">{sm.data ? `${sm.data.wallets_known} ví smart/tracked/KOL trong database` : ""}</span>}>
          {!sm.data ? <Loading /> : sm.data.channels.length === 0 ? <Empty>Chưa có kèo nào được đối chiếu (đối chiếu sau 30 phút và lúc đủ 24 giờ).</Empty> : (
            <div className="space-y-3">
              {sm.data.channels.map((c: any) => (
                <div key={c.channel_id}>
                  <div className="text-sm font-medium">@{c.username} <span className="text-xs text-slate-500">· {c.calls_checked} kèo đã đối chiếu</span></div>
                  {c.top_wallets.length === 0 ? <div className="text-xs text-slate-500">Không có ví nào trong danh sách giao dịch quanh các kèo.</div> : (
                    <div className="table-wrap"><table>
                      <thead><tr><Th>Ví</Th><Th>Loại</Th><Th>Mua trước kèo</Th><Th>Mua sau kèo</Th><Th>Bán sau kèo</Th></tr></thead>
                      <tbody>{c.top_wallets.map((w: any) => (
                        <tr key={w.wallet}><Td><Link href={`/wallets/${w.wallet}`} className="font-mono text-[12px] text-brand dark:text-teal-300">{short(w.wallet)}</Link></Td>
                          <Td>{w.role}</Td><Td>{w.buy_before}</Td><Td>{w.buy_after}</Td><Td>{w.sell_after}</Td></tr>))}</tbody>
                    </table></div>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title={`Kênh đang theo dõi (${(chans.data || []).filter((c: any) => c.enabled).length})`}>
        <div className="mb-3 flex flex-wrap gap-2">
          <input className="w-72" placeholder="@kenh hoặc https://t.me/kenh" value={input} onChange={(e) => setInput(e.target.value)} />
          <button className="btn" onClick={add} disabled={!input.trim()}>Thêm kênh</button>
          {msg && <span className="self-center text-xs text-slate-500">{msg}</span>}
        </div>
        <p className="mb-2 text-xs text-slate-500">Đọc qua trang xem trước công khai t.me/s (chỉ kênh công khai). Kênh riêng tư cần bật nguồn Telegram client bằng tài khoản của bạn, xem hướng dẫn trong docs/telegram-callers.md.</p>
        {!chans.data ? <Loading /> : chans.data.length === 0 ? <Empty>Chưa có kênh nào.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><Th>Kênh</Th><Th>Nguồn</Th><Th>Bài đã đọc</Th><Th>Kèo</Th><Th>Kèo gần nhất</Th><Th>Đọc gần nhất</Th><Th>Trạng thái</Th><Th> </Th></tr></thead>
              <tbody>
                {chans.data.map((c: any) => (
                  <tr key={c.id}>
                    <Td><a href={`https://t.me/${c.username}`} target="_blank" rel="noreferrer" className="text-brand dark:text-teal-300">@{c.username}</a>
                      <div className="text-[11px] text-slate-400">{c.title}</div></Td>
                    <Td>{c.source === "client" ? "Telegram client" : "t.me/s"}</Td>
                    <Td>{c.messages}</Td><Td>{c.calls}</Td><Td>{ago(c.last_call_at)}</Td><Td>{ago(c.last_ok_at)}</Td>
                    <Td>{!c.enabled ? "tạm dừng" : c.last_error ? <span className="text-rose-600" title={c.last_error}>lỗi ({c.error_count})</span> : "đang theo dõi"}</Td>
                    <Td className="whitespace-nowrap">
                      <button className="btn-ghost" onClick={() => act(`/api/callers/channels/${c.id}/toggle`, "POST")}>{c.enabled ? "Tạm dừng" : "Bật lại"}</button>
                      <button className="btn-ghost" onClick={() => confirm(`Ngừng theo dõi @${c.username}? Lịch sử kèo vẫn được giữ.`) && act(`/api/callers/channels/${c.id}`, "DELETE")}>Xoá</button>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
