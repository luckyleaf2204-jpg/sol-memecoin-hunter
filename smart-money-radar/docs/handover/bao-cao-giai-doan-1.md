# SMART MONEY RADAR — BÁO CÁO BÀN GIAO

**1. Giai đoạn:** Giai đoạn 1 (MVP 4–6 giờ theo Mục 16) — giao diện Dark Trading Terminal theo UI Kit + nối API thật
**2. Trạng thái chung:** PARTIAL — P0 đạt trên môi trường local/preview; triển khai cloud (VPS/domain) BLOCKED vì chưa có VPS/domain.
**Ngày:** 22/11/2026

## 3. Branch và commit

- Repo: `luckyleaf2204-jpg/sol-memecoin-hunter`, branch `claude/smart-money-radar-pumpfun-5kczmz`
- Commit giao diện: `65def85` — "Smart Money Radar UI: dark trading terminal (UI Kit), new Overview, Wallets, Live Feed, Data Health"
- Commit báo cáo: commit chứa file này (xem `git log`)

## 4. URL chạy thử

- **Chưa có URL public / production.** Chưa có VPS và domain (Mục 11: chưa xác nhận).
- Đã chạy và kiểm tra trên môi trường preview nội bộ (sandbox): API `http://127.0.0.1:8000`, dashboard `http://127.0.0.1:3001`.
- Người dùng tự chạy trên máy: Windows → `windows\start.bat` rồi mở `http://localhost:8080` (xem `docs/windows.md`); VPS → `docker compose up -d --build` rồi mở `https://<DOMAIN>` (xem `docs/install.md`).

## Tình trạng từng hạng mục

| Ưu tiên | Hạng mục | Trạng thái | Ghi chú |
|---|---|---|---|
| P0 | Khởi động hệ thống (frontend, backend, DB, Redis, worker) | DONE (local) | Chạy bằng tiến trình trực tiếp trong sandbox; worker chạy chế độ 1 tiến trình `radar all` |
| P0 | Tích hợp UI Kit (Shell.tsx, globals.css) | DONE | Giữ nguyên API/route; bổ sung Live Feed, Data Health, trạng thái kết nối thật, giờ cập nhật, nút refresh, menu drawer mobile |
| P0 | Overview theo hình mẫu Mục 15 | DONE | KPI, Smart Money Radar, biểu đồ dòng tiền + tỷ lệ Mua/Bán, Top Wallets, KOL Tracker, Live Feed, Token Radar — toàn bộ từ API |
| P0 | Top Wallets | DONE | 7/30/90 ngày, Official/All, tìm theo địa chỉ, sort (Score, WR, PnL, ROI, Copyability, Median Buy, 5x, 10x, Last Active), Official/Verified/Tracking/Candidate, sample size + confidence, card mobile |
| P0 | Responsive desktop + iPhone, không vỡ layout | DONE | Kiểm tra 11 trang (Overview, Wallets, chi tiết ví, KOL, Radar, chi tiết token, Live, Alerts, Backtest, Health, Settings) + đăng nhập: không tràn ngang (scrollWidth = innerWidth) ở 1440 px và iPhone 13 |
| P0 | Build sạch | DONE | typecheck + build PASS (log mục 10) |
| P1 | KOL Tracker | DONE | Thêm/xóa ví, gợi ý ví phụ (direct funder), feed mua/bán, cảnh báo Telegram mua/bán. Chưa có sửa label tại chỗ (xóa + thêm lại) |
| P1 | Token Radar / Live Feed | DONE | Radar có chọn nhóm ví; Live Feed có lọc nhóm ví / Mua-Bán / min SOL, tạm dừng, độ trễ từng lệnh |
| P1 | Data Health | DONE | Worker, nguồn dữ liệu, hàng đợi, secrets (chỉ "đã cấu hình / Not configured"), phạm vi + độ phủ backfill |
| P1 | Alerts | DONE (web) / chưa test Telegram thật | Telegram chưa có token/chat ID → hiển thị Not configured, app vẫn chạy |
| P2 | Research Lab bản nền | DONE (khung) | Liệt kê phương pháp: walk-forward (đã có), copy trễ (đã có), paper trading (chạy tay), permutation và walk-forward permutation (**Not implemented**) |
| P2 | Backfill đáng tin cậy | PARTIAL | Có checkpoint, dedup, coverage; nhiều ví KOL bị rải token rác → chỉ đọc được một phần lịch sử |
| P2 | Telegram | BLOCKED | Cần Bot Token + Chat ID của người dùng |
| P2 | Cloud production (HTTPS, domain) | BLOCKED | Cần VPS + domain; Docker Compose đã có sẵn và cấu hình hợp lệ |

## 5. Các chức năng đã hoàn thành (người dùng thao tác được)

| Trang | Đường dẫn | Thao tác |
|---|---|---|
| Overview | `/` | Đổi nhóm ví (Smart Money / Đang theo dõi / KOL), đổi 24H/7D cho Radar, tạm dừng Live Feed, bấm token/ví để xem chi tiết, refresh toàn trang |
| Top Wallets | `/wallets` | Official/All, 7D/30D/90D, chỉ ví người, tìm ví, sort 9 tiêu chí, mở bảng kiểm Top 20, thêm ví theo địa chỉ |
| Chi tiết ví | `/wallets/<địa chỉ>` | Score và thành phần, PnL, lịch sử vị thế/lệnh, người/bot, mô phỏng copy trễ 0/30/60/180 giây, độ đầy đủ lịch sử |
| KOL Tracker | `/kol` | Dán nhiều ví `địa_chỉ Tên @X`, xóa ví, xác nhận/bỏ ví phụ gợi ý, xem mua/bán 3 ngày |
| Token Radar | `/radar` | Nhóm ví × 1h/6h/24h/3 ngày |
| Live Feed | `/live` | Lọc nhóm ví, Mua/Bán, min SOL; tạm dừng; xem độ trễ |
| Cảnh báo | `/alerts` | Danh sách, đánh dấu đã đọc |
| Backtest / Research | `/backtest` | Research Lab + các lần backtest thật + kết quả lần gần nhất |
| Data Health | `/health` | Worker, nguồn, hàng đợi, secrets, backfill |
| Cài đặt | `/settings` | Ngưỡng Smart Money, copy, KOL, cảnh báo, Telegram test |

## 6. Các chức năng chưa hoàn thành và lý do

- **Cloud/HTTPS/domain**: chưa có VPS, domain. Cần người dùng cung cấp (mục 14).
- **Telegram thật**: chưa có Bot Token / Chat ID. Code gửi + nút test đã có.
- **Docker Compose up trong sprint này**: chỉ kiểm tra `docker compose config` (hợp lệ). Docker daemon trong sandbox không chạy sau khi máy sandbox tự khởi động lại; lần chạy `docker compose up` đầy đủ gần nhất là ở giai đoạn trước (bản Windows), chưa chạy lại với giao diện mới.
- **Permutation test / walk-forward permutation**: chưa triển khai (ghi rõ "Not implemented" trên giao diện).
- **Sửa label ví KOL tại chỗ**: chưa có; hiện xóa và thêm lại.
- **Metadata token cũ**: token tạo trước khi hệ thống chạy hiện "chưa có metadata" (tên/ký hiệu) — chưa có job lấy metadata cho token cũ.
- **Bộ lọc 30D cho Smart Money Radar**: API `/api/radar` giới hạn tối đa 7 ngày; giao diện chỉ có 24H/7D (không giả lập 30D).

## 7. API đã kết nối / API còn thiếu

Frontend đang gọi (đều cần đăng nhập, trừ `/api/health`):

| Endpoint | Dùng ở | Nguồn dữ liệu |
|---|---|---|
| `GET /api/overview` | Overview, top bar, Shell | bảng `wallets`, `smart_money_scores`, `wallet_transactions`, `alerts`, `api_sync_status`, `watched_wallets` |
| `GET /api/flow?hours&scope` **(mới)** | KPI Buy Flow / Active Tokens, biểu đồ dòng tiền | `wallet_transactions` gom theo giờ |
| `GET /api/radar?hours&scope` (thêm `scope`) | Smart Money Radar, Token Radar | `wallet_transactions`, `tokens`, `token_prices`, `token_risk_reports` |
| `GET /api/trades/recent` **(mới)** | Live Feed | `wallet_transactions` (kèm độ trễ `ingested_at − block_time`) |
| `GET /api/wallets/top`, `/api/wallets/candidates`, `/api/wallets`, `/api/wallets/{a}` (+ positions, transactions) | Top Wallets, chi tiết ví | `smart_money_scores`, `wallet_performance`, `wallet_positions` |
| `GET/POST/DELETE /api/watchlist*` | KOL Tracker | `watched_wallets`, `wallet_transactions` |
| `GET /api/system/status` | Data Health, sidebar | `worker_heartbeats`, `api_sync_status`, Redis |
| `GET /api/system/backfill` **(mới)** | Data Health | `wallets` (backfill_*), `failed_transactions` |
| `GET /api/backtest/latest`, `/api/backtest/runs` **(mới)** | Backtest / Research | `backtest_runs` |
| `GET /api/alerts`, `/api/settings`, `PUT /api/settings`, `POST /api/settings/test-telegram` | Cảnh báo, Cài đặt | `alerts`, `system_settings` |

`scope`: `smart` = ví đạt chuẩn Smart Money; `tracked` = mọi ví hệ thống theo dõi (gồm ứng viên chưa xác minh); `kol` = ví KOL; `all` (chỉ Live Feed) = mọi lệnh ≥ 1 SOL stream lưu. Các endpoint mới chỉ thêm, không đổi endpoint cũ; không đổi công thức PnL/ROI/Win Rate/Score.

Còn thiếu: endpoint sửa label ví KOL (`PATCH /api/watchlist/{address}`), job lấy metadata token cũ.

## 8. Dữ liệu thật đã xác minh (truy vấn database lúc 22:45 ngày 22/11/2026)

- **Số ví:** 682 ví trong DB; 461 ví đang theo dõi (candidate/tracked/smart); **0 ví đạt chuẩn Smart Money**; 0 ví Official Top; 681 ví đã chấm điểm 30 ngày; 19 ví KOL.
- **Số token:** 13.566
- **Giao dịch / chữ ký:** 370.104 lệnh đã giải mã (32.577 xác minh đầy đủ bằng giao dịch gốc); 46.694 giao dịch gốc lưu để kiểm tra lại; 69.995 chữ ký đã đọc khi backfill; 31.945 giao dịch thất bại on-chain bị loại; 125.354 nến giá.
- **Khoảng thời gian dữ liệu lệnh:** 09/11/2026 23:39 → 22/11/2026 22:45 (giờ Việt Nam).
- **Backfill coverage:** phạm vi CHỈ là ví hệ thống theo dõi (không phải toàn bộ lịch sử pump.fun), 60 ngày, tối đa 3.000 chữ ký/ví: 39 ví đủ lịch sử, 17 ví một phần (bị giới hạn chữ ký — thường do ví bị rải token rác hoặc tần suất cao), 427 ví đang chờ.
- **Nguồn:** RPC Solana công khai (sandbox không có Helius key); log chương trình pump.fun/PumpSwap + `getTransaction`; giá SOL Jupiter; nến lịch sử GeckoTerminal; DexScreener.

## 9. Worker / Docker / Database / Redis

- Sandbox: PostgreSQL 16 + TimescaleDB, Redis 7, API (uvicorn), worker `radar all` (stream, verifier, sync, discovery, prices, analytics, signals trong 1 tiến trình), dashboard Next.js.
- Lúc kiểm tra: API `/api/health` 200; heartbeat worker `all` Online; stream `solana_ws` ok/degraded (RPC công khai chập chờn, có lúc trễ 30+ giây); hàng đợi verify ~90, wallet_sync ~230, price_backfill ~158.
- Docker: `docker compose config -q` PASS cho `docker-compose.yml` và `docker-compose.windows.yml`. **Chưa chạy `docker compose up` trong sprint này** (Docker daemon sandbox không chạy sau khi máy sandbox khởi động lại).
- Lưu ý vận hành: máy sandbox tự khởi động lại nhiều lần trong phiên; dữ liệu PostgreSQL vẫn còn sau mỗi lần (thư mục dữ liệu bền), dịch vụ được bật lại bằng script.

## 10. Lệnh kiểm thử đã chạy và kết quả

Log đầy đủ có giờ: `docs/handover/test-log-2026-11-22.txt`.

| Lệnh | Kết quả |
|---|---|
| `pytest tests -q` (unit + integration với PostgreSQL/Redis) | **PASS** — 94 passed, 3 skipped (3 test gọi nguồn ngoài, chỉ chạy với `--live`) |
| `npm run typecheck` | **PASS** |
| `npm run build` | **PASS** — 13 route Next.js |
| Smoke test API (curl): 14 endpoint có đăng nhập | **PASS** — tất cả 200 |
| Không đăng nhập → 6 endpoint riêng | **PASS** — 401 |
| Sai mật khẩu → 401; đúng → 200, cookie `HttpOnly; SameSite=lax` | **PASS** |
| Logout → gọi lại API | **PASS** — 401 |
| Bật `trading_enabled=true` qua API | **PASS** — bị từ chối (422), vẫn `false` |
| Playwright: 11 trang × (desktop 1440 px + iPhone 13) — lỗi JS, lỗi tải tài nguyên, tràn ngang | **PASS** — không lỗi, không tràn |
| `docker compose config -q` (2 file) | **PASS** |
| `docker compose up` | **NOT RUN** trong sprint này (xem mục 9) |

## 11. Lỗi còn tồn tại

- Stream dùng RPC công khai nên chập chờn/trễ trong sandbox; với Helius trả phí trên máy người dùng cần kiểm tra lại độ trễ thực tế.
- 0 ví đạt chuẩn Smart Money → các ô "Smart Money" trống; Overview tự chuyển sang "Ví đang theo dõi" và hiển thị cảnh báo vàng nói rõ đây chưa phải Smart Money đã xác minh.
- Nhóm "Đang theo dõi" bị chi phối bởi vài token PumpSwap volume rất lớn (ví arbitrage) — dữ liệu thật, nhưng làm biểu đồ dòng tiền ít ý nghĩa.
- Token cũ thiếu tên/ký hiệu ("chưa có metadata").
- Ảnh chụp cả trang dài (full page) hiển thị thanh menu trái chỉ ở phần đầu ảnh — do cách chụp; khi dùng thật menu cố định theo màn hình.
- Mã lỗi khi cố bật giao dịch tự động là 422 (validation) thay vì 400.

## 12. Ảnh chụp desktop/mobile đính kèm

`01 Overview desktop`, `02 Overview iPhone 1–2`, `03 Top Wallets desktop`, `04 Top Wallets iPhone 1–2`, `05 KOL Tracker desktop`, `06 Live Feed desktop`, `07 Token Radar desktop`, `08 Data Health desktop`, `09 Research Lab desktop` (dữ liệu thật lúc 22:45 ngày 22/11/2026, giao diện tối).

## 13. Vấn đề bảo mật đã kiểm tra

- Không có file `.env` nào được commit (git ls-files: 0); diff sprint không chứa chuỗi giống API key / bot token (0).
- Bundle frontend: chỉ chứa **tên** biến trong chữ hướng dẫn (ví dụ "Thêm HELIUS_API_KEY…"), không chứa giá trị bí mật (0 file chứa mật khẩu/secret thử nghiệm).
- `/api/settings` chỉ trả "đã cấu hình / chưa", không trả giá trị secret.
- Mọi API dữ liệu yêu cầu đăng nhập (401 khi chưa đăng nhập); logout hủy phiên.
- Giao dịch tự động bị khóa cứng (`trading_enabled` luôn `false`, API từ chối bật).
- Cookie `Secure` chỉ bật khi chạy HTTPS (Caddy) — sandbox chạy HTTP nên chưa kiểm tra được cờ `Secure`.

## 14. Việc cần người dùng cung cấp

1. **VPS** Ubuntu (khuyến nghị 4 vCPU / 8 GB RAM / 100 GB SSD) **hoặc** dùng máy Windows ở nhà (đã có bộ cài).
2. **Domain/subdomain** trỏ A record về IP VPS (để có HTTPS).
3. **Telegram Bot Token + Chat ID** (tạo qua @BotFather, xem `docs/telegram-setup.md`).
4. **Helius API key**: nhập trực tiếp vào `.env` trên máy triển khai — **không gửi qua chat**.

## 15. Việc đề xuất làm tiếp theo

1. Triển khai lên VPS/máy Windows với Helius key thật; chạy `docker compose up -d --build`, kiểm tra HTTPS, login, reboot.
2. Đo lại độ trễ cảnh báo KOL với Helius; test Telegram thật.
3. Job lấy metadata token (tên, ký hiệu) cho token cũ.
4. `PATCH /api/watchlist/{address}` để sửa label KOL tại chỗ.
5. Đọc lịch sử ví KOL bằng API lọc swap của Helius (tránh token rác) để có đủ dữ liệu đánh giá.
6. Research Lab: paper trading tự động ghi lệnh giả lập theo ngày; permutation test.
