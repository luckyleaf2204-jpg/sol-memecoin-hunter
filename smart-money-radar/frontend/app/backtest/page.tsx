"use client";
import { useApi } from "@/lib/api";
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
  if (!run) return <Card><Empty>Chưa có lần backtest nào. Chạy trên server: <code>docker compose run --rm api python -m radar backtest</code></Empty></Card>;
  const sel = run.results.selected;
  const meta = run.params.meta || {};
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Backtest tín hiệu cùng mua</h1>
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
