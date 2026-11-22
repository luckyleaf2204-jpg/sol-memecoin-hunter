# Hướng dẫn cài đặt

## 1. Yêu cầu

| Thành phần | Tối thiểu | Khuyến nghị |
|---|---|---|
| VPS | 2 vCPU · 4 GB RAM · 40 GB SSD | 4 vCPU · 8 GB RAM · 100 GB SSD |
| Hệ điều hành | Ubuntu 22.04 / 24.04 | |
| Phần mềm | Docker Engine 24+ và Docker Compose v2 | |
| Mạng | Mở cổng 80 và 443 | |
| Tên miền | Bản ghi A trỏ về IP của VPS (để có HTTPS hợp lệ trên iPhone/Safari) | |

Dung lượng: luồng log Pump.fun tạo ~1–2 GB dữ liệu/tuần với cấu hình mặc định (giữ 7 ngày cho ví không theo dõi).

## 2. Cài Docker (Ubuntu)

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker        # tự khởi động lại sau khi server reboot
```

## 3. Lấy mã nguồn và cấu hình

```bash
git clone <repo> && cd <repo>/smart-money-radar
cp .env.example .env
nano .env
```

Bắt buộc điền:

| Biến | Cách tạo |
|---|---|
| `DOMAIN` | ví dụ `radar.tenmiencuaban.vn` (đã trỏ A record về VPS) |
| `DASHBOARD_PASSWORD` | mật khẩu dài, ngẫu nhiên |
| `SESSION_SECRET` | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | `openssl rand -hex 16` |

Khuyến nghị: `HELIUS_API_KEY` (https://dashboard.helius.dev → API Keys, gói Free đủ chạy), `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
(xem [telegram-setup.md](telegram-setup.md)).

**Không bao giờ** đưa private key hay seed phrase vào `.env` — hệ thống không cần và không dùng.

## 4. Khởi động

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f stream verifier sync
```

Lần đầu: `migrate` tạo bảng rồi dừng (trạng thái `exited (0)` là đúng); các dịch vụ khác chạy liên tục.
Caddy tự xin chứng chỉ Let's Encrypt cho `DOMAIN` (cần cổng 80/443 mở).

Mở `https://DOMAIN` trên máy tính hoặc iPhone → đăng nhập. Trên iPhone có thể dùng **Chia sẻ → Thêm vào MH chính** để mở như ứng dụng.

## 5. Khởi tạo dữ liệu (không bắt buộc)

Hệ thống tự tìm ví ứng viên từ luồng giao dịch (sau 10–30 phút). Có thể đẩy nhanh:

```bash
# backfill những người mua sớm >= 2 SOL của một token đã tăng mạnh
docker compose run --rm api python -m radar seed <MINT_ADDRESS>
# theo dõi thêm một ví cụ thể (vẫn chấm điểm hoàn toàn từ dữ liệu on-chain)
docker compose run --rm api python -m radar add-wallet <WALLET_ADDRESS>
```

## 6. Chạy thử trên máy cá nhân (không Docker)

Cần Python 3.12+, Node 20+, PostgreSQL 16, Redis 7.

```bash
# backend
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://radar:radar@127.0.0.1:5432/radar REDIS_URL=redis://127.0.0.1:6379/0 \
       DASHBOARD_PASSWORD=doi-mat-khau SESSION_SECRET=$(openssl rand -hex 16) LOG_JSON=false
python -m radar migrate
python -m radar all &          # mọi worker trong một tiến trình
python -m radar api            # http://127.0.0.1:8000/api/docs
# frontend (cửa sổ khác)
cd frontend && npm ci && BACKEND_URL=http://127.0.0.1:8000 npm run dev   # http://localhost:3000
```

## 7. Chạy bộ kiểm thử (máy dev)

```bash
cd backend && pip install -r requirements-dev.txt
pytest -q tests/unit                                   # không cần mạng / DB
TEST_DATABASE_URL=postgresql://radar:radar@127.0.0.1:5432/radar_test TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
  pytest -q tests/integration                          # PostgreSQL + Redis thật
pytest -q --live tests/integration/test_live_sources.py   # gọi RPC / Jupiter / DexScreener / GeckoTerminal thật
```

## 8. Kiểm tra sau khi cài

```bash
docker compose exec api python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read())"
```
Trong dashboard: **Cài đặt → Trạng thái hệ thống** phải thấy `solana_ws logs_stream ok` và các worker có heartbeat dưới 2 phút.
