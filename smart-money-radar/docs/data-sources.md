# Nguồn dữ liệu — kết quả kiểm tra (gọi thật ngày 22/11/2026)

| Nguồn | Endpoint / phương thức | Kết quả kiểm tra | Dùng cho | Giới hạn & xử lý |
|---|---|---|---|---|
| **Solana RPC công khai** `api.mainnet-beta.solana.com` | `getTransaction`, `getSignaturesForAddress`, `getAccountInfo`, `getMultipleAccounts`, `getTokenAccountsByOwner` | ✅ | xác minh, backfill, tài khoản | 100 req/10 s/IP, 40 req/10 s/phương thức; thực tế ~23% `429` trên IP dùng chung → rate limiter có back-off |
| | `getTransaction` với `maxSupportedTransactionVersion: 0` | ❌ `-32015 Transaction version (1) is not supported` | — | **mainnet đã có tx version 1** → hệ thống dùng `1` |
| | `getTokenLargestAccounts` | ❌ `429` | holder | cần Helius |
| | WebSocket `logsSubscribe {mentions:[program]}` | ✅ ~170 thông báo/giây cho pump.fun | stream thời gian thực | ngắt kết nối định kỳ → tự kết nối lại, ghi khoảng trống |
| **publicnode** `solana-rpc.publicnode.com` | HTTP + WS | ✅ (trừ `getMultipleAccounts`, `getTokenLargestAccounts`: `403`) | endpoint dự phòng | failover tự động |
| **Helius** | RPC + WS `mainnet.helius-rpc.com/?api-key=` | cần `HELIUS_API_KEY` (không có trong môi trường thử) | RPC ưu tiên, holder | gói Free 10 req/s; cấu hình `HELIUS_RPS` |
| **Yellowstone gRPC** | — | không dùng: cần nhà cung cấp trả phí, proxy môi trường thử không hỗ trợ gRPC | — | `logsSubscribe` thay thế (độ trễ ~1 slot) |
| **Pump.fun** frontend API (không chính thức) | `/coins?sort=…` | ✅ | — (không phụ thuộc) | `/coins/{mint}`, `/coins-v2/{mint}`: **404**; `/trades/all`: **400** → mọi dữ liệu Pump.fun lấy từ on-chain |
| **Pump.fun on-chain** | tài khoản Global `4wTV1Ymi…` | ✅ đọc được | hằng số curve | initial virtual token 1 073 000 000 000 000, virtual SOL 30 SOL, real token 793 100 000 000 000, fee 95 bps |
| **PumpPortal** | `wss://pumpportal.fun/api/data` | tài liệu: `subscribeTokenTrade` / `subscribeAccountTrade` **tính phí** (0,01 SOL/10 000 tin, cần API key + ví nạp SOL) | không dùng mặc định | `PUMPPORTAL_API_KEY` để dành; stream RPC miễn phí thay thế |
| **Jupiter** `lite-api.jup.ag` | `/price/v3`, `/swap/v1/quote` | ✅ | SOL/USD, khả năng bán token đã migrate | ~60 req/phút |
| **DexScreener** | `/tokens/v1/solana/{≤30 mint}` | ✅ (có cả pair `pumpfun` và `pumpswap`) | market cap/liquidity USD, volume 24h | 300 req/phút; chỉ giá hiện tại |
| **GeckoTerminal** | `/tokens/{mint}/pools`, `/pools/{pool}/ohlcv/minute|hour`, `/pools/{pool}/trades` | ✅ có pool `pump-fun` (curve) và `pumpswap` | nến giá lịch sử, đối chiếu độc lập 300 lệnh gần nhất/pool | 30 req/phút; nến không có số lệnh/thanh khoản |
| **Birdeye** | — | cần key trả phí | không dùng | tuỳ chọn `BIRDEYE_API_KEY` |
| **Solscan Pro** | — | cần key trả phí | không dùng | liên kết Solscan trong dashboard để kiểm tra thủ công |
| **Telegram Bot API** | `sendMessage`, `getUpdates` | ✅ | cảnh báo | 1 tin/giây/chat |

## Vì sao không scrape
Không thu thập dữ liệu trái điều khoản (không scrape pump.fun, x.com, solscan.io). Tất cả số liệu ví/giao dịch đến từ blockchain qua RPC;
dữ liệu thị trường từ API công khai có tài liệu.

## Giá trị không thể lấy đầy đủ và phương án thay thế

| Thiếu | Lý do | Thay thế |
|---|---|---|
| Holder count / top holder khi không có Helius | `getTokenLargestAccounts` bị chặn trên RPC công khai | kiểm tra "top 10" ghi `unknown` (+0,5 điểm rủi ro); thêm Helius |
| Lịch sử giá trước khi hệ thống chạy | không có nguồn nến on-chain miễn phí | GeckoTerminal (1 phút ~16 giờ đầu, 1 giờ cho 7 ngày) |
| Funding "đầu tiên" của ví cũ | phải đọc toàn bộ lịch sử chữ ký | lấy giao dịch nạp ≥ 0,05 SOL sớm nhất trong cửa sổ backfill; ghi `funding_is_first_ever` |
| Lịch sử ví bot (> 1 000 tx/ngày) | chi phí RPC | bỏ qua (Không ưu tiên theo yêu cầu "ít giao dịch") — cấu hình `skip_high_frequency_wallets` |
