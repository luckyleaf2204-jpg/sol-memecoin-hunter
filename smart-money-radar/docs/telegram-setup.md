# Hướng dẫn cài đặt Telegram Bot

Hệ thống dùng **Telegram Bot API chính thức** (`sendMessage`, `getUpdates`). Token bot chỉ nằm trong `.env`,
không lưu trong database, không hiển thị trên dashboard và bị che trong log.

## Bước 1 — Tạo bot
1. Mở Telegram, chat với **@BotFather**.
2. Gửi `/newbot`, đặt tên hiển thị (ví dụ `SM Radar`) và username kết thúc bằng `bot`.
3. BotFather trả về token dạng `1234567890:AA...`. Chép vào `.env`:
   ```
   TELEGRAM_BOT_TOKEN=1234567890:AA...
   ```

## Bước 2 — Lấy Chat ID
1. Khởi động lại dịch vụ tín hiệu: `docker compose up -d signals`
2. Mở bot vừa tạo trên Telegram, bấm **Start** (gửi `/start`).
3. Bot trả lời: `Chat ID của bạn: 123456789`.
4. Điền vào `.env` (`TELEGRAM_CHAT_ID=123456789`) **hoặc** vào **Dashboard → Cài đặt → Telegram → Chat ID** (không cần khởi động lại).

Muốn nhận cảnh báo trong một nhóm: thêm bot vào nhóm, gửi `/start@ten_bot` trong nhóm, lấy chat id (số âm, ví dụ `-100…`).

## Bước 3 — Kiểm tra
Dashboard → Cài đặt → Telegram → **Gửi tin nhắn thử**. Nếu lỗi `401` → token sai; lỗi `400 chat not found` → chat id sai
hoặc bạn chưa bấm Start với bot.

## Lệnh của bot
| Lệnh | Tác dụng |
|---|---|
| `/start` | Trả về chat id (ai cũng dùng được — chỉ là số định danh của chính họ) |
| `/top` | Top 10 Smart Money chính thức (chỉ trả lời chat đã cấu hình) |
| `/status` | Số ví theo dõi, số Smart Money, số cảnh báo 24h |

## Nội dung cảnh báo
```
🚨 SMART MONEY ALERT — STRONG
Token / Symbol / Mint Address
Market Cap · Liquidity (SOL, kèm USD nếu có)
Số ví Smart Money mua (độc lập) · Tổng vốn mua · Net Smart Money Flow
Ví mua: địa chỉ đầy đủ – SOL – Win Rate – Score
Thời gian phát hiện (GMT+7) · Giá tại thời điểm phát hiện · Smart Money Score · Risk Level · Xác minh on-chain
Token Link: pump.fun · DexScreener
```

## Chống gửi trùng
* Mỗi token chỉ gửi **một lần cho mỗi mức** (WATCH / STRONG / VERY STRONG) — khóa duy nhất trong bảng `alerts`.
* Gửi lại cùng mức chỉ khi có **biến động quan trọng**: thêm ≥ 2 ví độc lập hoặc vốn Smart Money tăng ≥ 2 lần, sau thời gian chờ 10 phút
  (cấu hình trong Cài đặt).
* Tăng cấp (WATCH → STRONG → VERY STRONG) luôn được gửi.
* Mọi quyết định (đã gửi / lỗi / chỉ dashboard) được lưu và hiển thị ở **Alert Center**.
