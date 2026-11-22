"use client";
import { useState } from "react";
import { api } from "@/lib/api";

export default function Login() {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ password: pw }) });
      const next = new URLSearchParams(location.search).get("next") || "/";
      location.href = next.startsWith("/") ? next : "/";
    } catch (e: any) {
      setErr(e.status === 429 ? "Sai quá nhiều lần, thử lại sau 15 phút." : "Mật khẩu không đúng.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow dark:border-slate-800 dark:bg-slate-900">
        <div>
          <div className="text-lg font-bold text-brand dark:text-teal-300">SMART MONEY RADAR</div>
          <div className="text-xs text-slate-500">Đăng nhập dashboard</div>
        </div>
        <input type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="Mật khẩu" className="w-full" autoFocus />
        {err && <div className="text-sm text-rose-600">{err}</div>}
        <button className="btn w-full" disabled={busy || !pw}>{busy ? "Đang kiểm tra…" : "Đăng nhập"}</button>
        <p className="text-[11px] text-slate-500">Ứng dụng chỉ đọc dữ liệu blockchain. Không bao giờ hỏi private key hay seed phrase.</p>
      </form>
    </div>
  );
}
