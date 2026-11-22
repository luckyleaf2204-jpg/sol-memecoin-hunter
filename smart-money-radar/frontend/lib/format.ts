// Vietnamese number / date conventions: 1.234,56 · dd/MM/yyyy HH:mm (GMT+7)
const TZ = "Asia/Ho_Chi_Minh";

export function num(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
  return Number(x).toLocaleString("vi-VN", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export function sol(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined) return "—";
  return `${num(x, digits)} SOL`;
}

export function signedSol(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined) return "—";
  return `${x > 0 ? "+" : ""}${num(x, digits)} SOL`;
}

export function pct(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
  return `${num(Number(x) * 100, digits)}%`;
}

export function signedPct(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined) return "—";
  return `${x > 0 ? "+" : ""}${num(x * 100, digits)}%`;
}

export function usd(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  const v = Number(x);
  if (v >= 1e6) return `$${num(v / 1e6, 2)} tr`;
  if (v >= 1e3) return `$${num(v / 1e3, 1)} N`;
  return `$${num(v, 0)}`;
}

export function price(x: number | null | undefined): string {
  if (!x) return "—";
  const v = Number(x);
  return `${v.toLocaleString("vi-VN", { maximumSignificantDigits: 4 })} SOL`;
}

export function multiple(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  return `${num(x, 2)}x`;
}

export function dateTime(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleString("vi-VN", {
    timeZone: TZ, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function ago(s: string | null | undefined): string {
  if (!s) return "—";
  const sec = (Date.now() - new Date(s).getTime()) / 1000;
  return duration(sec) + " trước";
}

export function duration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "—";
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s} giây`;
  if (s < 3600) return `${Math.round(s / 60)} phút`;
  if (s < 86400) return `${num(s / 3600, 1)} giờ`;
  return `${num(s / 86400, 1)} ngày`;
}

export function short(a: string | null | undefined, n = 4): string {
  if (!a) return "—";
  return a.length <= 2 * n + 1 ? a : `${a.slice(0, n)}…${a.slice(-n)}`;
}

/** Compact number for KPI tiles: 414.274 -> "414,3K", 1.250.000 -> "1,25M" (vi-VN separators). */
export function compact(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
  const v = Number(x), a = Math.abs(v);
  if (a >= 1e9) return `${num(v / 1e9, 2)}B`;
  if (a >= 1e6) return `${num(v / 1e6, 2)}M`;
  if (a >= 1e4) return `${num(v / 1e3, digits)}K`;
  return num(v, a >= 100 ? 0 : digits);
}
