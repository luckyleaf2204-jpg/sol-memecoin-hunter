# SMART MONEY RADAR — Pump.fun / Solana

Hệ thống phát hiện, phân tích và theo dõi **Smart Money Wallets** trên Pump.fun: ví chọn đúng meme coin,
xuống vốn từ 2 SOL/vị thế, mua trước khi token tăng 2x–10x và có hiệu suất **kiểm chứng được bằng giao dịch on-chain**.

> **Chỉ đọc dữ liệu & cảnh báo.** Không có chức năng giao dịch, không hỏi private key / seed phrase,
> không ký giao dịch. Mọi chỉ số đều truy ngược được tới chữ ký giao dịch (signature) trên Solana.

| Tài liệu | Nội dung |
|---|---|
| [docs/windows.md](docs/windows.md) | **Chạy trên máy tính Windows 10/11 bằng Docker Desktop (không cần VPS)** |
| [docs/kol.md](docs/kol.md) | **Theo dõi ví KOL / người gọi kèo: cảnh báo ngay khi họ mua** |
| [docs/install.md](docs/install.md) | Cài đặt trên VPS (Docker, HTTPS, tên miền) và chạy thử trên máy |
| [docs/operations.md](docs/operations.md) | Vận hành 24/7: dịch vụ, log, giám sát, backup/khôi phục, cập nhật, xử lý sự cố |
| [docs/telegram-setup.md](docs/telegram-setup.md) | Tạo bot Telegram, lấy chat id, kiểm tra gửi cảnh báo |
| [docs/methodology.md](docs/methodology.md) | Định nghĩa vị thế, vốn thực tế, PnL, Win Rate, 2x–10x, Score, độ tin cậy, tín hiệu, rủi ro |
| [docs/data-sources.md](docs/data-sources.md) | Kết quả kiểm tra API (đã gọi thật), giới hạn, phương án thay thế |
| [docs/api.md](docs/api.md) | REST + WebSocket API |
| [docs/test-report.md](docs/test-report.md) | Báo cáo kiểm thử, đối chiếu on-chain, backtest |

## Kiến trúc

```
                 Solana mainnet (logsSubscribe · getTransaction · getSignaturesForAddress)
                                │
   ┌──────────── stream ────────┴───────┐      ┌─ prices ─────────────────────────┐
   │ giải mã event pump.fun + PumpSwap  │      │ SOL/USD (Jupiter) · DexScreener   │
   │ từ log → nến giá 1 phút on-chain   │      │ nến lịch sử GeckoTerminal         │
   └──────┬───────────────┬─────────────┘      └──────────────────────────────────┘
          │ q:verify      │ ev:smart_trades (Redis Streams)
   ┌──────▼─────┐   ┌─────▼──────────┐   ┌─ sync ─────────────┐   ┌─ discovery ───────┐
   │ verifier   │   │ signals        │   │ backfill 30 ngày   │   │ ví mua lớn / mua   │
   │ getTx +    │   │ cùng mua → tín │   │ + đồng bộ tăng dần │   │ sớm trước khi tăng │
   │ đối soát   │   │ hiệu → Telegram│   │ từng ví            │   │ → ứng viên         │
   └──────┬─────┘   └─────┬──────────┘   └──────┬─────────────┘   └───────────────────┘
          └───────────────┴──────┬──────────────┘
                     PostgreSQL (TimescaleDB) + Redis
                                 │
              analytics: vị thế → PnL → hiệu suất 7/30/90 ngày → Score → Top 10
                         quan hệ ví / cụm · rủi ro token
                                 │
                    api (FastAPI, REST + WebSocket) ──► dashboard (Next.js) ──► Caddy HTTPS
```

Mỗi khối là một tiến trình/container riêng (`python -m radar <role>`), mã nguồn tách module:

| Thư mục | Vai trò |
|---|---|
| `backend/radar/solana/` | RPC client (failover, rate limit chia sẻ qua Redis, retry), base58, PDA |
| `backend/radar/decoder/` | **Transaction decoder**: event pump.fun/PumpSwap, phí, rent, tip, đối soát số dư, multi-hop, router |
| `backend/radar/analytics/` | Vị thế, PnL, hiệu suất, 2x–10x có xác nhận thanh khoản, Score, quan hệ ví, tín hiệu, rủi ro, backtest (thuần Python, có unit test) |
| `backend/radar/services/` | stream, verifier, wallet sync, discovery, prices, analytics runner, signal engine + Telegram |
| `backend/radar/api/` | FastAPI + xác thực + WebSocket |
| `backend/migrations/` | SQL migration có version |
| `frontend/` | Dashboard Next.js + TypeScript + Tailwind + Recharts (ưu tiên iPhone/Safari, Dark Mode) |
| `deploy/` | Caddy (HTTPS tự động), script backup |

## Chạy trên máy tính Windows (không cần VPS)

Cài Docker Desktop → tải mã nguồn → bấm đúp `windows\setup.bat` → `windows\start.bat` → mở http://localhost:8080.
Chi tiết từng bước: [docs/windows.md](docs/windows.md).

## Chạy nhanh (VPS có Docker)

```bash
cd smart-money-radar
cp .env.example .env          # điền DOMAIN, DASHBOARD_PASSWORD, SESSION_SECRET, POSTGRES_PASSWORD
docker compose up -d --build
docker compose ps             # mọi dịch vụ "running"/"healthy"
```
Mở `https://<DOMAIN>` → đăng nhập bằng `DASHBOARD_PASSWORD`. Chi tiết: [docs/install.md](docs/install.md).

## Những gì hệ thống làm (và cách kiểm chứng)

1. **Thu thập**: `logsSubscribe` trên chương trình pump.fun `6EF8…F6P` và PumpSwap `pAMM…EA` — mọi lệnh mua/bán, tạo token,
   hoàn tất bonding curve, migration được giải mã trực tiếp từ dữ liệu on-chain.
2. **Xác minh**: lệnh của ví được theo dõi được tải lại bằng `getTransaction` và đối soát với số dư trước/sau của ví
   → nhãn **Verified / Partially Verified / Unverified**. Tín hiệu STRONG trở lên chỉ dùng lệnh Verified.
3. **Vốn thực tế**: SOL thực vào curve/pool của mọi lệnh mua trong một vị thế; phí giao dịch, phí mạng, tip, phí bot tách riêng;
   rent tài khoản token không tính là chi phí (được hoàn lại).
4. **Smart Money**: chỉ vị thế ≥ 2 SOL; Win Rate theo vị thế đã đóng; PnL sau phí; ví ôm token chết bị tính lỗ (write-off);
   2x–10x chỉ tính khi giá được xác nhận bằng khối lượng/thanh khoản; Score 100 điểm + độ tin cậy dữ liệu.
5. **Ví độc lập**: cùng nguồn funding, chuyển SOL/token qua lại, mua đồng bộ, liên quan creator → gom cụm; tín hiệu đếm cụm, không đếm ví.
6. **Cảnh báo**: Telegram + Alert Center, chống gửi trùng, chỉ gửi lại khi tăng cấp/biến động quan trọng.
7. **Backtest** walk-forward không look-ahead, chia train / validation / out-of-sample.

## Giới hạn quan trọng (đọc trước khi dùng)

* **RPC công khai rất chậm cho backfill** (≈ 2–4 `getTransaction`/giây do 429 trên IP dùng chung): backfill 30 ngày một ví mất vài phút.
  Với `HELIUS_API_KEY` (gói free 10 req/s, gói trả phí nhanh hơn) hệ thống nhanh hơn nhiều và có thêm phân bố holder (`getTokenLargestAccounts`
  bị chặn trên RPC công khai).
* Lịch sử giá trước thời điểm hệ thống bắt đầu chạy lấy từ GeckoTerminal (30 lệnh/phút). Nến GeckoTerminal không có số lệnh/thanh khoản
  nên tiêu chí xác nhận đỉnh dùng khối lượng (≥ 3 SOL/nến) — ghi rõ trong [methodology](docs/methodology.md).
* Kết quả xếp hạng chỉ có ý nghĩa thống kê khi hệ thống đã chạy đủ lâu để có nhiều vị thế đã đóng. Ví ít dữ liệu luôn bị gắn
  **Low Confidence** và không vào Top 10 chính thức.
* Đây là công cụ phân tích dữ liệu, **không phải lời khuyên đầu tư**. Meme coin có rủi ro mất toàn bộ vốn.
