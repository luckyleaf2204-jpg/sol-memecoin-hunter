"use client";
import { useApi } from "@/lib/api";
import { EmptyState, Panel } from "@/components/terminal";
import { dateTime, num, pct, signedPct } from "@/lib/format";
import { Card, Empty, ErrorBox, Loading } from "@/components/ui";

function Block({ title, s }: { title: string; s: any }) {
  if (!s) return null;
  const r = (k: string) => s[k] || {};
  return (
    <Card title={`${title} — ${s.tradable} / ${s.signals} tín hiệu giao dịch được`}>
      <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
        <div>Tăng 2x: <b>{s.hit_2x_count}</b> ({pct(s.hit_2x, 0)})</div>
        <div>Tăng 5x: <b>{s.hit_5x_count}</b> ({pct(s.hit_5x, 0)})</div>
        <div>Tăng 10x: <b>{s.hit_10x_count}</b> ({pct(s.hit_10x, 0)})</div>
        <div>Giảm 50%: <b>{s.drawdown_50_count}</b> ({pct(s.drawdown_50, 0)})</div>
        <div>Mất thanh khoản: <b>{s.liquidity_lost_count}</b> ({pct(s.liquidity_lost, 0)})</div>
        <div>LN TB thoát sau 1h: <b>{signedPct(r("ret_1h").mean, 1)}</b> (n={r("ret_1h").n})</div>
        <div>LN TB thoát sau 6h: <b>{signedPct(r("ret_6h").mean, 1)}</b> (n={r("ret_6h").n})</div>
        <div>LN TB thoát sau 24h: <b>{signedPct(r("ret_24h").mean, 1)}</b> (n={r("ret_24h").n})</div>
        <div>TP 2x / SL −50%: <b>{signedPct(r("ret_tp2x_sl50").mean, 1)}</b> · trung vị {signedPct(r("ret_tp2x_sl50").median, 1)}</div>
      </div>
    </Card>
  );
}

export default function Backtest() {
  const { data, error, loading } = useApi<any>("/api/backtest/latest", 0);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox msg={error} />;
  const run = data.run;
  if (!run) return <div className="space-y-4"><ResearchLab /><Card><Empty>Chưa có lần backtest nào. Chạy trên server: <code>docker compose run --rm api python -m radar backtest</code></Empty></Card></div>;
  const sel = run.results.selected;
  const meta = run.params.meta || {};
  return (
    <div className="space-y-4">
      <ResearchLab />
      <h1 className="text-xl font-bold">Backtest tín hiệu cùng mua (lần chạy gần nhất)</h1>
      <p className="text-xs text-slate-500">
        Chạy lúc {dateTime(run.created_at)} · {num(meta.wallets, 0)} ví · {num(meta.trades, 0)} lệnh · {num(meta.tokens, 0)} token ({num(meta.tokens_with_bars, 0)} có giá lịch sử).
        Walk-forward không look-ahead: tập ví Smart Money tại thời điểm t chỉ dựa trên vị thế đã đóng trước t; entry = giá đầu tiên sau khi phát hiện + độ trễ + slippage + phí.
        Tham số chọn trên tập train: {JSON.stringify(run.results.selected_params)}.
      </p>
      {sel ? (<>
        <Block title="Training (60%)" s={sel.train} />
        <Block title="Validation (20%)" s={sel.validation} />
        <Block title="Out-of-sample test (20%)" s={sel.test} />
      </>) : <Empty>Không có tín hiệu nào trong dữ liệu hiện có.</Empty>}
    </div>
  );
}

/** Research Lab: what has actually been run, and what is NOT implemented yet (never a fake result). */
const METHODS: { name: string; status: "done" | "manual" | "todo"; note: string }[] = [
  { name: "Walk-forward backtest tín hiệu cùng mua (train 60% / validation 20% / out-of-sample 20%)", status: "done",
    note: "Chạy bằng lệnh `python -m radar backtest`; kết quả lưu vào bảng backtest_runs và hiện ở trên. Không look-ahead: tập ví tại thời điểm t chỉ dựa trên vị thế đã đóng trước t." },
  { name: "Mô phỏng copy trễ cho từng ví (0 / 30 / 60 / 180 giây)", status: "done",
    note: "Tính tự động cho mọi ví đã đọc lịch sử; xem ở trang chi tiết ví (mục \"Có copy được ví này không?\")." },
  { name: "Paper trading kèo KOL (vào theo, tự chốt +50% / cắt −30%)", status: "manual",
    note: "Đã chạy thử bằng script ngoài dashboard trên dữ liệu live; chưa có trang ghi lệnh giả lập tự động." },
  { name: "Permutation test (xáo trộn nhãn để kiểm định ý nghĩa thống kê)", status: "todo", note: "Chưa triển khai — Not implemented." },
  { name: "Walk-forward permutation (kiểm định ngoài mẫu + p-value)", status: "todo", note: "Chưa triển khai — Not implemented." },
];

function ResearchLab() {
  const runs = useApi<any>("/api/backtest/runs", 0);
  return (
    <div className="space-y-4">
      <Panel icon="lab" title="Research Lab" sub="Các phương pháp kiểm định và trạng thái thật của từng phương pháp">
        <div className="overflow-x-auto">
          <table className="t-table">
            <thead><tr><th>Phương pháp</th><th>Trạng thái</th><th>Ghi chú</th></tr></thead>
            <tbody>
              {METHODS.map((m) => (
                <tr key={m.name}>
                  <td className="whitespace-normal font-semibold">{m.name}</td>
                  <td><span className={`t-pill ${m.status === "done" ? "t-buy" : m.status === "manual" ? "t-warn" : "t-mute"}`}>{m.status === "done" ? "Đã có" : m.status === "manual" ? "Chạy tay" : "Not implemented"}</span></td>
                  <td className="t-muted whitespace-normal text-[11px]">{m.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel icon="backtest" title="Các lần backtest đã chạy" sub="Lấy từ bảng backtest_runs — chỉ lần chạy thật">
        {!runs.data && !runs.error ? <div className="t-skel mx-4 mb-4 h-16" /> : runs.error ? <EmptyState tone="error" title="Không tải được" reason={runs.error} /> : !(runs.data.runs || []).length ? (
          <EmptyState title="Chưa chạy backtest lần nào" reason="Chạy: docker compose run --rm api python -m radar backtest" />
        ) : (
          <div className="overflow-x-auto">
            <table className="t-table">
              <thead><tr><th>#</th><th>Lúc chạy</th><th>Dữ liệu</th><th>Tín hiệu (train / val / test)</th></tr></thead>
              <tbody>
                {runs.data.runs.map((r: any) => (
                  <tr key={r.id}>
                    <td className="t-faint">{r.id}</td>
                    <td>{new Date(r.created_at).toLocaleString("vi-VN", { hour12: false })}</td>
                    <td className="t-muted">{Object.entries(r.summary || {}).map(([k, v]) => `${k}: ${v}`).join(" · ") || "—"}</td>
                    <td className="t-muted">{["train", "validation", "test"].map((k) => r.splits?.[k] ? `${r.splits[k].traded ?? 0}/${r.splits[k].signals ?? 0}` : "—").join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
