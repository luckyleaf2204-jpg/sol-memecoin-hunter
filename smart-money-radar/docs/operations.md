# Hướng dẫn vận hành

## Các dịch vụ

| Dịch vụ | Lệnh | Việc làm | Nếu dừng |
|---|---|---|---|
| `postgres` | TimescaleDB 2.17 / PG16 | dữ liệu | mọi thứ dừng |
| `redis` | Redis 7 (AOF) | hàng đợi, cache, rate limit dùng chung, pub/sub | worker chờ và tự kết nối lại |
| `migrate` | `radar migrate` | áp dụng migration rồi thoát | — |
| `stream` | `radar stream` | logsSubscribe pump.fun + PumpSwap, nến giá, lưu lệnh | mất dữ liệu thời gian thực (sync bù cho ví theo dõi) |
| `verifier` | `radar verifier` | tải giao dịch đầy đủ, đối soát, gắn Verified | tín hiệu chỉ ở mức WATCH |
| `sync` | `radar sync` | backfill 30 ngày + đồng bộ 30 phút/lần cho ví theo dõi | hiệu suất ví không cập nhật |
| `discovery` | `radar discovery` | tìm ví ứng viên (10 phút/lần) | không có ví mới |
| `prices` | `radar prices` | SOL/USD, DexScreener, nến lịch sử GeckoTerminal | thiếu USD / thiếu giá lịch sử |
| `analytics` | `radar analytics` | vị thế, PnL, Score, Top 10, quan hệ ví, rủi ro (2 phút/lần, toàn bộ mỗi giờ) | bảng xếp hạng đứng yên |
| `signals` | `radar signals` | phát hiện cùng mua, cảnh báo Telegram, lệnh bot | không có cảnh báo |
| `api`, `frontend`, `caddy` | | dashboard + HTTPS | không truy cập được dashboard |
| `backup` | `pg_dump` hằng ngày 02:30 giờ VN, giữ 14 ngày | `./backups/` | |

Tất cả có `restart: unless-stopped`; với `systemctl enable docker` hệ thống tự chạy lại sau khi server khởi động lại.

## Lệnh thường dùng

```bash
docker compose ps                                   # trạng thái
docker compose logs -f --since 10m stream signals   # log (JSON, đã che API key)
docker compose restart sync                         # khởi động lại một worker
docker compose run --rm api python -m radar seed <MINT>          # backfill người mua sớm của token
docker compose run --rm api python -m radar add-wallet <ADDR>    # theo dõi thêm ví
docker compose run --rm api python -m radar recompute            # tính lại toàn bộ ngay
docker compose run --rm api python -m radar redecode             # giải mã lại giao dịch thô đã lưu (sau khi nâng cấp decoder)
docker compose run --rm api python -m radar backtest --out /tmp/bt.json
docker compose run --rm api python -m radar crosscheck --n 25 --out /tmp/crosscheck.md
```

## Giám sát

* Dashboard → **Cài đặt → Trạng thái hệ thống**: số request thành công/lỗi/429 theo từng nguồn (bảng `api_sync_status`),
  heartbeat từng worker (`worker_heartbeats`), độ dài hàng đợi Redis, số lệnh Verified / Partially Verified.
* `GET /api/health` (không cần đăng nhập) — dùng cho uptime monitor bên ngoài (UptimeRobot, BetterStack…).
* Dấu hiệu cần xử lý:
  * `solana_ws logs_stream` không `ok` hoặc `last_message_age_s` > 60 → kiểm tra mạng/RPC; worker tự chuyển endpoint.
  * nhiều `rate_limited` trên `rpc_solana_public` → thêm `HELIUS_API_KEY` hoặc RPC riêng (`SOLANA_RPC_URLS`).
  * `q:wallet_sync` tăng mãi → RPC quá chậm cho số ví ứng viên; giảm `tracked_wallet_limit` hoặc tăng RPC.

## Backup & khôi phục

```bash
ls -lh backups/                                     # radar-YYYYMMDD-HHMM.dump
# khôi phục vào database đang chạy (dừng worker trước)
docker compose stop stream verifier sync discovery prices analytics signals api
docker compose exec -T postgres pg_restore -U radar -d radar --clean --if-exists < backups/radar-20261122-1930.dump
docker compose up -d
```
Nên chép thư mục `backups/` sang nơi khác (rclone/S3) định kỳ.

## Cập nhật phiên bản

```bash
git pull
docker compose up -d --build        # migration mới được áp dụng tự động bởi dịch vụ migrate
docker compose run --rm api python -m radar redecode   # nếu bản cập nhật sửa decoder
```

## Bảo mật

* Không có private key / seed phrase / quyền ký trong hệ thống. Không có endpoint nào tạo hay gửi giao dịch.
* API key chỉ trong `.env` (quyền đọc `chmod 600 .env`), không vào database, không gửi về trình duyệt (Settings chỉ hiện "Đã cấu hình").
* Log đi qua bộ lọc che `api-key=…`, token bot Telegram, mật khẩu, secret.
* Dashboard: một mật khẩu, cookie phiên ký HMAC (HttpOnly, SameSite=Lax, Secure qua HTTPS), khoá 15 phút sau 10 lần sai.
* Chỉ mở cổng 80/443; PostgreSQL và Redis không công bố ra ngoài.

## Dung lượng & hiệu năng

* Lệnh của ví không theo dõi giữ `stream_retention_days` (7 ngày) rồi xoá; lịch sử ví theo dõi, nến giá, tín hiệu, cảnh báo giữ lâu dài.
* Tốc độ backfill phụ thuộc RPC: RPC công khai ≈ 2–4 giao dịch/giây (một ví ~500 chữ ký ≈ 3 phút); Helius free ≈ 8–10/giây.
* Có thể chạy thêm worker `sync` (`docker compose up -d --scale sync=2`): giới hạn tốc độ RPC được chia sẻ qua Redis nên không vượt hạn mức.

## Xử lý sự cố

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| Dashboard 502 | `api` chưa sẵn sàng / DB chưa lên | `docker compose logs api postgres` |
| Không có ví Smart Money | hệ thống mới chạy, ví ứng viên chưa đủ vị thế đóng | chờ; xem danh sách "Tất cả ví đang theo dõi" |
| Telegram không gửi | token/chat id sai, `telegram_enabled` tắt | Cài đặt → Gửi tin nhắn thử |
| Nhiều Partially Verified | swap qua router / trả bằng USDC — xem cột ghi chú | bình thường; chỉ lệnh Verified được dùng cho STRONG |
| Holder count trống | RPC công khai chặn `getTokenLargestAccounts` | thêm `HELIUS_API_KEY` |
