"use client";
// Data Health — workers, data sources, queues, secrets (configured or not, never the value),
// and what the history backfill actually covers (/api/system/backfill).
import { useApi } from "@/lib/api";
import { ago, num } from "@/lib/format";
import { EmptyState, Kpi, Panel, ShortAddr, SkelRows } from "@/components/terminal";

const BF_LABEL: Record<string, [string, string]> = {
  complete: ["Đủ lịch sử", "t-buy"], partial: ["Một phần (bị giới hạn)", "t-warn"], queued: ["Đang chờ", "t-mute"],
  running: ["Đang chạy", "t-info"], failed: ["Lỗi", "t-sell"], none: ["Chưa đọc", "t-mute"],
};

export default function Health() {
  const st = useApi<any>("/api/system/status", 15000);
  const bf = useApi<any>("/api/system/backfill", 30000);
  const s = st.data, b = bf.data;
  const by = new Map<string, any>((b?.by_status || []).map((r: any) => [r.backfill_status, r]));
  const totalBf = (b?.by_status || []).reduce((a: number, r: any) => a + Number(r.n), 0);
  const ws = (s?.sources || []).find((x: any) => x.source === "solana_ws");

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi icon="feed" tone={ws?.status === "ok" ? "green" : "rose"} label="Stream on-chain" loading={!s}
          value={ws ? (ws.status === "ok" ? "Đang chạy" : ws.status) : "—"} sub={ws ? `${ws.details?.endpoint || "?"} · cập nhật ${ago(ws.updated_at)}` : "chưa có báo cáo"} />
        <Kpi icon="coins" tone="teal" label="Giao dịch đã lưu" loading={!s} value={s ? num(s.counts.trades, 0) : "—"}
          sub={s ? `${num(s.counts.verified, 0)} đã xác minh · ${num(s.counts.partial, 0)} một phần` : undefined} />
        <Kpi icon="wallets" tone="teal" label="Ví trong phạm vi backfill" loading={!b} value={b ? num(totalBf, 0) : "—"}
          sub={b ? `${num(by.get("complete")?.n || 0, 0)} đủ · ${num(by.get("partial")?.n || 0, 0)} một phần · ${num(by.get("queued")?.n || 0, 0)} chờ` : undefined} />
        <Kpi icon="alerts" tone={(s?.counts.failed_txs || 0) > 0 ? "amber" : "green"} label="Giao dịch lỗi bị loại" loading={!s}
          value={s ? num(s.counts.failed_txs, 0) : "—"} sub="giao dịch thất bại on-chain, không tính PnL" />
      </div>

      <div className="grid gap-4 xl:grid-cols-12">
        <Panel className="xl:col-span-7" icon="health" title="Worker & nguồn dữ liệu" sub="Nhịp (heartbeat) của từng tiến trình và trạng thái từng nguồn">
          {!s ? <SkelRows rows={6} cols={4} /> : (
            <div className="overflow-x-auto">
              <table className="t-table">
                <thead><tr><th>Thành phần</th><th>Trạng thái</th><th>Lần cuối</th><th>Chi tiết</th></tr></thead>
                <tbody>
                  {(s.workers || []).map((w: any) => {
                    const ageOf = (x: any) => (Date.now() - new Date(x.last_beat).getTime()) / 1000;
                    const age = ageOf(w);
                    // a stale row of the other run mode (single-process "all" vs one container per role)
                    const others = (s.workers || []).filter((x: any) => (w.worker === "all") !== (x.worker === "all"));
                    const unused = age >= 180 && others.some((x: any) => ageOf(x) < age);
                    const label = unused ? "Không dùng" : age < 180 ? "Online" : age < 900 ? "Delayed" : "Error";
                    return (
                      <tr key={"w" + w.worker}>
                        <td className="font-semibold">Worker: {w.worker === "all" ? "all (mọi vai trò, 1 tiến trình)" : w.worker}</td>
                        <td><span className={`t-pill ${unused ? "t-faint" : age < 180 ? "t-buy" : age < 900 ? "t-warn" : "t-sell"}`} title={unused ? "Nhịp cũ của chế độ chạy khác, hệ thống đang chạy theo chế độ còn lại" : undefined}>{label}</span></td>
                        <td className="t-muted">{ago(w.last_beat)}</td>
                        <td className="t-faint max-w-[340px] truncate text-[11px]">khởi động {ago(w.started_at)}</td>
                      </tr>
                    );
                  })}
                  {groupSources(s.sources || []).map((x: any) => (
                    <tr key={"s" + x.source + x.job}>
                      <td>{x.source}{x.job ? ` · ${x.job}` : ""}{x.count > 1 ? <span className="t-faint"> ({x.count} việc)</span> : null}</td>
                      <td><span className={`t-pill ${["ok", "done"].includes(x.status) ? "t-buy" : ["rate_limited", "degraded"].includes(x.status) ? "t-warn" : "t-sell"}`}>{x.status}</span></td>
                      <td className="t-muted">{ago(x.updated_at)}</td>
                      <td className="t-faint max-w-[340px] truncate text-[11px]">
                        {`OK ${num(x.requests_ok || 0, 0)} · lỗi ${num(x.requests_failed || 0, 0)} · 429 ${num(x.rate_limited || 0, 0)}`}
                        {x.last_error ? ` · ${String(x.last_error).slice(0, 80)}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel className="xl:col-span-5" icon="settings" title="Hàng đợi & cấu hình" sub="Không bao giờ hiển thị giá trị bí mật">
          {!s ? <SkelRows rows={6} cols={2} /> : (
            <div className="space-y-1 px-4 pb-4 text-xs">
              {Object.entries(s.queues || {}).map(([k, v]: any) => (
                <div key={k} className="flex justify-between border-t py-1.5" style={{ borderColor: "var(--line)" }}><span className="t-muted">Hàng đợi {k}</span><b className="num">{v == null ? "—" : num(v, 0)}</b></div>
              ))}
              {Object.entries(s.secrets || {}).filter(([k]: any) => k !== "rpc_endpoints").map(([k, v]: any) => (
                <div key={k} className="flex justify-between border-t py-1.5" style={{ borderColor: "var(--line)" }}><span className="t-muted">{k}</span>
                  <span className={`t-pill ${v ? "t-buy" : "t-warn"}`}>{v ? "Đã cấu hình" : "Not configured"}</span></div>
              ))}
              <div className="flex justify-between border-t py-1.5" style={{ borderColor: "var(--line)" }}><span className="t-muted">RPC đang dùng</span>
                <span className="text-right">{(s.secrets?.rpc_endpoints || []).join(", ") || "—"}</span></div>
              <div className="flex justify-between border-t py-1.5" style={{ borderColor: "var(--line)" }}><span className="t-muted">Token / nến giá</span><b className="num">{num(s.counts.tokens, 0)} / {num(s.counts.price_bars, 0)}</b></div>
            </div>
          )}
        </Panel>
      </div>

      <Panel icon="backtest" title="Backfill lịch sử — phạm vi và độ phủ" sub={b ? `Phạm vi: CHỈ các ví hệ thống đang theo dõi, ${b.backfill_days} ngày, tối đa ${num(b.max_signatures_per_wallet, 0)} chữ ký mỗi ví. Không phải toàn bộ lịch sử pump.fun.` : "Đang tải…"}>
        {!b ? <SkelRows rows={5} cols={5} /> : (
          <>
            <div className="overflow-x-auto">
              <table className="t-table">
                <thead><tr><th>Trạng thái</th><th className="text-right">Số ví</th><th className="text-right">Bị giới hạn số chữ ký</th><th className="text-right">Chữ ký đã thấy</th><th className="text-right">Giao dịch đã tải</th></tr></thead>
                <tbody>
                  {(b.by_status || []).map((r: any) => {
                    const [l, c] = BF_LABEL[r.backfill_status] || [r.backfill_status, "t-mute"];
                    return (
                      <tr key={r.backfill_status}>
                        <td><span className={`t-pill ${c}`}>{l}</span></td>
                        <td className="num text-right">{num(r.n, 0)}</td>
                        <td className="num text-right">{num(r.truncated, 0)}</td>
                        <td className="num text-right">{num(r.signatures, 0)}</td>
                        <td className="num text-right">{num(r.fetched, 0)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="t-faint px-4 py-3 text-[11px] leading-relaxed">
              Hàng đợi đồng bộ ví: {b.queue_length == null ? "—" : num(b.queue_length, 0)} việc · giao dịch lỗi on-chain đã loại: {num(b.failed_transactions, 0)}.
              "Một phần" = ví có nhiều giao dịch hơn giới hạn chữ ký (thường do bị rải token rác hoặc giao dịch tần suất cao); số liệu của các ví này bị gắn độ tin cậy thấp.
              Backfill chạy lại tự động từ chữ ký cuối đã đồng bộ (checkpoint <code>last_synced_signature</code>), không tải trùng.
            </div>
          </>
        )}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel icon="wallets" title="Đồng bộ gần nhất" sub="20 ví được đọc lịch sử gần nhất">
          {!b ? <SkelRows rows={5} cols={4} /> : !(b.recent || []).length ? <EmptyState title="Chưa ví nào được đồng bộ" /> : (
            <div className="overflow-x-auto">
              <table className="t-table">
                <thead><tr><th>Ví</th><th>Trạng thái</th><th className="text-right">Chữ ký</th><th>Từ ngày</th><th className="text-right">Lần cuối</th></tr></thead>
                <tbody>
                  {b.recent.map((r: any) => {
                    const [l, c] = BF_LABEL[r.backfill_status] || [r.backfill_status, "t-mute"];
                    return (
                      <tr key={r.address}>
                        <td><ShortAddr a={r.address} href={`/wallets/${r.address}`} /></td>
                        <td><span className={`t-pill ${c}`}>{l}</span></td>
                        <td className="num text-right">{num(r.backfill_sigs_seen, 0)}</td>
                        <td className="t-muted">{r.backfill_from ? new Date(r.backfill_from).toLocaleDateString("vi-VN") : "—"}</td>
                        <td className="t-muted text-right">{ago(r.last_synced_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel icon="alerts" title="Backfill lỗi" sub="Ví đọc lịch sử thất bại và lý do">
          {!b ? <SkelRows rows={3} cols={3} /> : !(b.failed || []).length ? <EmptyState title="Không có ví nào lỗi" reason="Mọi ví trong phạm vi đều đã đọc được hoặc đang chờ." /> : (
            <div className="overflow-x-auto">
              <table className="t-table">
                <thead><tr><th>Ví</th><th>Nguồn</th><th>Lỗi</th><th className="text-right">Lúc</th></tr></thead>
                <tbody>{b.failed.map((r: any) => (
                  <tr key={r.address}><td><ShortAddr a={r.address} href={`/wallets/${r.address}`} /></td><td className="t-muted">{r.discovery_source}</td>
                    <td className="t-faint max-w-[260px] truncate text-[11px]">{r.error || "—"}</td><td className="t-muted text-right">{ago(r.updated_at)}</td></tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

/** One row per source; per-token jobs (history_seed / seed) are folded into a single row. */
function groupSources(src: any[]) {
  const out: any[] = [];
  const folded = new Map<string, any>();
  for (const x of src) {
    if (x.source === "history_seed" || x.source === "seed") {
      const g = folded.get(x.source);
      if (!g) folded.set(x.source, { ...x, job: "", count: 1 });
      else {
        g.count += 1;
        if (new Date(x.updated_at) > new Date(g.updated_at)) g.updated_at = x.updated_at;
        if (x.status !== "done" && x.status !== "ok") g.status = x.status;
      }
    } else out.push({ ...x, count: 1 });
  }
  return [...out, ...folded.values()];
}
