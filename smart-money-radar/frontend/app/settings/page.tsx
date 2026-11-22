"use client";
import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago, num } from "@/lib/format";
import { Card, ErrorBox, Loading, Pill } from "@/components/ui";

type F = { k: string; label: string; type: "number" | "bool" | "text" | "level"; hint?: string; pct?: boolean };
const GROUPS: { title: string; fields: F[] }[] = [
  { title: "Bộ lọc Smart Money", fields: [
    { k: "analysis_period_days", label: "Khoảng thời gian phân tích (ngày: 7 / 30 / 90)", type: "number" },
    { k: "min_buy_sol", label: "Minimum Buy (SOL / vị thế)", type: "number" },
    { k: "min_win_rate", label: "Win Rate tối thiểu", type: "number", pct: true },
    { k: "preferred_positions_min", label: "Số kèo ưu tiên — tối thiểu", type: "number" },
    { k: "preferred_positions_max", label: "Số kèo ưu tiên — tối đa", type: "number" },
    { k: "min_closed_positions_official", label: "Số vị thế đóng tối thiểu để vào Top chính thức", type: "number" },
    { k: "signal_min_wallet_score", label: "Điểm tối thiểu của ví được tính vào tín hiệu", type: "number" },
    { k: "holder_alert_pct", label: "Cảnh báo khi một ví nắm trên (% supply)", type: "number" },
    { k: "fresh_wallet_hours", label: "Ví mới tạo: hoạt động đầu tiên trong vòng (giờ)", type: "number" },
  ] },
  { title: "Ví copy được (người thật, copy trễ vẫn thắng)", fields: [
    { k: "require_human", label: "Chỉ xếp hạng ví giao dịch giống người (loại bot)", type: "bool" },
    { k: "require_copyable", label: "Chỉ xếp hạng ví copy trễ vẫn thắng", type: "bool" },
    { k: "copy_delay_seconds", label: "Bạn copy trễ bao nhiêu giây", type: "number", hint: "60 = 1 phút" },
    { k: "min_copy_win_rate", label: "Tỷ lệ thắng tối thiểu khi copy trễ", type: "number", pct: true },
    { k: "min_copy_positions", label: "Số vị thế có giá tối thiểu để đánh giá copy", type: "number" },
    { k: "copy_cost_pct", label: "Phí + trượt giá của bạn (cả mua và bán)", type: "number", pct: true },
  ] },
  { title: "Điều kiện cảnh báo (cùng mua)", fields: [
    { k: "signal_window_minutes", label: "Cửa sổ thời gian (phút)", type: "number" },
    { k: "signal_min_position_sol", label: "Vốn tối thiểu mỗi vị thế (SOL)", type: "number" },
    { k: "signal_watch_min_wallets", label: "WATCH — số ví độc lập", type: "number" },
    { k: "signal_watch_min_total_sol", label: "WATCH — tổng vốn (SOL)", type: "number" },
    { k: "signal_strong_min_wallets", label: "STRONG — số ví", type: "number" },
    { k: "signal_strong_min_total_sol", label: "STRONG — tổng vốn (SOL)", type: "number" },
    { k: "signal_very_strong_min_wallets", label: "VERY STRONG — số ví", type: "number" },
    { k: "signal_very_strong_min_total_sol", label: "VERY STRONG — tổng vốn (SOL)", type: "number" },
    { k: "signal_max_runup", label: "Giá tối đa so với entry SM đầu tiên (lần)", type: "number" },
    { k: "signal_require_verified_for_strong", label: "STRONG trở lên phải được xác minh on-chain", type: "bool" },
    { k: "signal_high_risk_caps_watch", label: "Token HIGH RISK chỉ ở mức WATCH", type: "bool" },
  ] },
  { title: "Telegram", fields: [
    { k: "telegram_enabled", label: "Bật gửi Telegram", type: "bool" },
    { k: "telegram_chat_id", label: "Chat ID (gửi /start cho bot để lấy)", type: "text" },
    { k: "alert_min_level", label: "Mức tối thiểu để gửi", type: "level" },
    { k: "alert_cooldown_minutes", label: "Thời gian chờ trước khi gửi lại (phút)", type: "number" },
    { k: "alert_resend_new_buyers", label: "Gửi lại khi thêm ≥ N ví độc lập", type: "number" },
    { k: "exit_alert_enabled", label: "Cảnh báo khi ví Smart Money của tín hiệu bán ra (thoát lệnh)", type: "bool" },
    { k: "exit_alert_share", label: "Ngưỡng cảnh báo thoát: tỷ lệ vị thế đã bán", type: "number", pct: true },
  ] },
  { title: "Thu thập dữ liệu", fields: [
    { k: "backfill_days", label: "Số ngày backfill lịch sử ví", type: "number" },
    { k: "backfill_max_signatures", label: "Số chữ ký tối đa mỗi ví", type: "number" },
    { k: "tracked_wallet_limit", label: "Số ví theo dõi tối đa", type: "number" },
    { k: "discovery_min_distinct_tokens", label: "Discovery: số token khác nhau tối thiểu", type: "number" },
    { k: "skip_high_frequency_wallets", label: "Bỏ qua ví tần suất cao (bot)", type: "bool" },
    { k: "stream_store_min_sol", label: "Lưu lệnh của ví lạ từ (SOL)", type: "number" },
  ] },
];

export default function Settings() {
  const s = useApi<any>("/api/settings", 0);
  const sys = useApi<any>("/api/system/status", 15000);
  const [form, setForm] = useState<any>(null);
  const [excl, setExcl] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (s.data) {
      setForm({ ...s.data.settings });
      setExcl((s.data.settings.excluded_wallets || []).join("\n"));
    }
  }, [s.data]);
  if (s.loading && !s.data) return <Loading />;
  if (s.error) return <ErrorBox msg={s.error} />;
  if (!form) return <Loading />;
  const sec = s.data.secrets;
  async function save() {
    setMsg(null);
    const changes: any = {};
    for (const g of GROUPS) for (const f of g.fields) if (form[f.k] !== s.data.settings[f.k]) changes[f.k] = form[f.k];
    const ex = excl.split(/\s+/).map((x) => x.trim()).filter(Boolean);
    if (JSON.stringify(ex) !== JSON.stringify(s.data.settings.excluded_wallets)) changes.excluded_wallets = ex;
    try { await api("/api/settings", { method: "PUT", body: JSON.stringify({ changes }) }); setMsg("Đã lưu."); s.reload(); }
    catch (e: any) { setMsg(`Lỗi: ${e.message}`); }
  }
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Cài đặt</h1>
      <Card title="API keys (cấu hình trong file .env trên server — không bao giờ gửi về trình duyệt)">
        <div className="grid gap-2 text-sm md:grid-cols-2">
          {[["HELIUS_API_KEY", sec.helius_api_key, "RPC + holder + websocket tốc độ cao"], ["TELEGRAM_BOT_TOKEN", sec.telegram_bot_token, "gửi cảnh báo"],
            ["BIRDEYE_API_KEY", sec.birdeye_api_key, "tùy chọn"], ["SOLSCAN_API_KEY", sec.solscan_api_key, "tùy chọn"],
            ["PUMPPORTAL_API_KEY", sec.pumpportal_api_key, "tùy chọn (dữ liệu trả phí)"]].map(([k, ok, h]: any) => (
            <div key={k} className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 p-2 dark:border-slate-800">
              <div><div className="font-mono text-xs">{k}</div><div className="text-[11px] text-slate-500">{h}</div></div>
              <Pill className={ok ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}>{ok ? "Đã cấu hình" : "Chưa có"}</Pill>
            </div>
          ))}
        </div>
        <div className="mt-2 text-[11px] text-slate-500">RPC đang dùng: {sec.rpc_endpoints.join(" → ")}. Giao dịch tự động: TẮT (không thể bật).</div>
      </Card>

      {GROUPS.map((g) => (
        <Card key={g.title} title={g.title}>
          <div className="grid gap-3 md:grid-cols-2">
            {g.fields.map((f) => (
              <label key={f.k} className="flex items-center justify-between gap-3 text-sm">
                <span>{f.label}</span>
                {f.type === "bool" ? (
                  <input type="checkbox" checked={!!form[f.k]} onChange={(e) => setForm({ ...form, [f.k]: e.target.checked })} className="h-5 w-5" />
                ) : f.type === "level" ? (
                  <select value={form[f.k]} onChange={(e) => setForm({ ...form, [f.k]: e.target.value })}>
                    <option value="WATCH">WATCH</option><option value="STRONG">STRONG</option><option value="VERY_STRONG">VERY STRONG</option>
                  </select>
                ) : f.type === "text" ? (
                  <input value={form[f.k] || ""} onChange={(e) => setForm({ ...form, [f.k]: e.target.value })} className="w-40" />
                ) : (
                  <input type="number" inputMode="decimal" step="any" className="w-28 text-right"
                    value={f.pct ? Math.round(form[f.k] * 1000) / 10 : form[f.k]}
                    onChange={(e) => setForm({ ...form, [f.k]: f.pct ? Number(e.target.value) / 100 : Number(e.target.value) })} />
                )}
              </label>
            ))}
          </div>
          {g.title === "Telegram" && (
            <button className="btn-ghost mt-3" onClick={async () => {
              try { await api("/api/settings/test-telegram", { method: "POST" }); setMsg("Đã gửi tin nhắn thử."); } catch (e: any) { setMsg(`Lỗi: ${e.message}`); }
            }}>Gửi tin nhắn thử</button>
          )}
        </Card>
      ))}

      <Card title="Danh sách ví loại trừ (mỗi dòng một địa chỉ)">
        <textarea value={excl} onChange={(e) => setExcl(e.target.value)} rows={4} className="w-full rounded-lg border border-slate-300 bg-white p-2 font-mono text-xs dark:border-slate-700 dark:bg-slate-800" />
      </Card>
      <div className="sticky bottom-20 z-10 flex items-center gap-3 md:bottom-4">
        <button className="btn shadow-lg" onClick={save}>Lưu cài đặt</button>{msg && <span className="text-sm">{msg}</span>}
      </div>

      <Card title="Trạng thái hệ thống">
        {!sys.data ? <Loading /> : (
          <div className="space-y-3 text-xs">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              {Object.entries(sys.data.counts).map(([k, v]: any) => <div key={k}><div className="text-slate-500">{k}</div><div className="font-semibold">{num(v, 0)}</div></div>)}
              {Object.entries(sys.data.queues).map(([k, v]: any) => <div key={k}><div className="text-slate-500">{k}</div><div className="font-semibold">{v ?? "—"}</div></div>)}
            </div>
            <div className="table-wrap"><table><tbody>
              {sys.data.sources.map((r: any) => (
                <tr key={`${r.source}-${r.job}`}>
                  <td className="py-1 pr-2 font-mono">{r.source}</td><td className="pr-2 text-slate-500">{r.job}</td>
                  <td className="pr-2"><Pill className={r.status === "ok" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}>{r.status}</Pill></td>
                  <td className="pr-2">{num(r.requests_ok, 0)} ok / {num(r.requests_failed, 0)} lỗi / {num(r.rate_limited, 0)} 429</td>
                  <td className="text-slate-500">{ago(r.updated_at)}</td>
                </tr>
              ))}
            </tbody></table></div>
            <div className="table-wrap"><table><tbody>
              {sys.data.workers.map((w: any) => (
                <tr key={w.worker}><td className="py-1 pr-2 font-mono">{w.worker}</td><td className="pr-2">{ago(w.last_beat)}</td>
                  <td className="max-w-[420px] truncate text-slate-500">{JSON.stringify(w.info)}</td></tr>
              ))}
            </tbody></table></div>
          </div>
        )}
      </Card>
    </div>
  );
}
