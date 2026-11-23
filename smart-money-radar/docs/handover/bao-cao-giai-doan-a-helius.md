# SMART MONEY RADAR – BÁO CÁO GIAI ĐOẠN A: KẾT NỐI HELIUS

**Ngày:** 23/11/2026 (giờ UTC; giờ Việt Nam = UTC + 7)
**Môi trường:** máy chủ đám mây thử nghiệm, Docker Compose. Gói Helius Developer.
**Phạm vi:** chỉ Giai đoạn A theo `docs/EXTERNAL_SERVICES_INTEGRATION_SPEC.md`.
**Trạng thái chung:** **PASS** cho kết nối RPC, WebSocket, log, độ trễ và bảo mật khóa. **Chưa xác định được:** số credit thực tế mà WebSocket tiêu thụ (cần đọc trên dashboard Helius).

---

## 1. Những gì đã cấu hình

| Hạng mục | Cách làm | Ghi chú |
|---|---|---|
| `HELIUS_API_KEY` | Người dùng tự nhập vào **Environment variables** của môi trường đám mây | Không qua chat, không ghi file, không ghi Git |
| `HELIUS_RPS=40` | Biến môi trường (không bí mật) | Gói Developer 50 RPS; để dư 20% |
| Chuyển biến vào container | File cấu hình riêng của máy chủ thử nghiệm (ngoài repo) thêm `HELIUS_API_KEY: ${HELIUS_API_KEY}` và `HELIUS_RPS`, đồng thời tự lấy cổng proxy hiện tại | Không sửa code, không sửa database |
| Container `sync` | **Tạm dừng** | Không chạy backfill 500 ví |
| Code / database / package | **Không thay đổi** | `trading_enabled = false` |

---

## 2. Kiểm tra thành công (có bằng chứng)

### 2.1 Khóa hợp lệ, gọi trực tiếp

| Lệnh | Kết quả | Thời gian |
|---|---|---|
| `getHealth` | `"ok"` | 0,11–0,57 giây |
| `getSlot` | 472.780.263 | 0,39 giây |
| `getTokenLargestAccounts` (1 token) | Helius trả **20 tài khoản** | 0,38 giây |
| (cùng lệnh, RPC công khai) | lỗi 429 | |

### 2.2 Hệ thống thực sự dùng Helius (đọc từ log và database, không chỉ đọc biến)

- Log của stream ghi: `"stream connected to helius"`.
- Trạng thái stream: `endpoint = helius`, `status = ok`, tin nhắn cuối cách 0,2 giây.
- Bảng `api_sync_status` có các dòng `rpc_helius`. Toàn bộ request RPC của các worker đi qua Helius; không còn dòng `rpc_solana_public` hay `rpc_publicnode` nào trong cửa sổ đo.

### 2.3 Đo 30 phút (02:48:33 → 03:18:33 UTC)

**Request Helius:**

| Worker | Request thành công | Lỗi | 429 | Thời gian phản hồi TB |
|---|---|---|---|---|
| discovery | 1.214 (đợt quét lúc khởi động) | 0 | 0 | 51 ms |
| stream (tra pool) | 584 | 0 | 0 | 114 ms |
| verifier | 261 | 0 | 0 | 175 ms |
| **Tổng** | **2.059** | **0** | **0** | |

**So sánh với Giai đoạn 1.5 (RPC công khai, 30 phút):**

| Chỉ số | Giai đoạn 1.5 | Helius | Thay đổi |
|---|---|---|---|
| Stream: kết nối lại / watchdog kích hoạt | 29 / 14 | **0 / 0** | Ổn định hoàn toàn |
| Stream: độ trễ p50 / p90 / p99 | 4,44 / 13,97 / 40,18 giây | **2,18 / 3,11 / 3,62 giây** | p99 nhanh hơn 11 lần |
| Giao dịch ghi được trong 30 phút | +22.799 | **+43.775** | Gấp khoảng 1,9 lần (ít mất dữ liệu hơn) |
| Verifier: p50 / p90 | 6,99 / 21,07 giây | **2,00 / 2,98 giây** | |
| Cảnh báo KOL: p50 | 9,38 giây | **1,93 giây** (n = 4) | |
| Nến giá on-chain mới nhất / SOL-USD | 106,9 / 46,9 giây | **37,4 / 37,4 giây** | |
| Lỗi RPC / 429 | 344 / 343 (RPC công khai) | **0 / 0** | |
| Dòng ERROR trong log | 0 | 0 | |
| Restart / OOM | 0 / 0 | 0 / 0 | |

### 2.4 Bảo mật khóa

Chỉ đếm số lần xuất hiện, không in giá trị khóa.

| Nơi kiểm tra | Số lần khóa xuất hiện |
|---|---|
| Log toàn bộ container | 0 |
| Bảng `api_sync_status` (lỗi và chi tiết) | 0 |
| File trong repo và thư mục chạy | 0 |

---

## 3. Chưa hoạt động / chưa kiểm chứng

| Hạng mục | Tình trạng | Lý do |
|---|---|---|
| Bảng `token_holders` | Vẫn 0 dòng | Hệ thống chỉ chụp holder cho token mà **ví Smart Money** mua, và hiện có 0 ví đạt chuẩn. Helius đã trả đúng dữ liệu khi gọi thử (mục 2.1), nên đây không phải lỗi kết nối |
| Backfill | 0 ví (đúng yêu cầu) | `sync` tạm dừng; hàng đợi còn 519 ví |
| Đồng bộ ví đang theo dõi | Dừng theo `sync` | Ví theo dõi không được cập nhật lịch sử trong lúc thử (giao dịch mới vẫn vào qua stream) |
| **Credit thực tế** | **Chưa đọc được từ máy chủ** | Số credit chỉ hiển thị trên dashboard Helius của người dùng |

---

## 4. Chi phí

- **Không bật dịch vụ trả phí mới.** Chỉ dùng hạn mức của gói Helius Developer hiện có.
- **Ước tính theo request** (bảng giá Helius: RPC thường tốn 1 credit mỗi lần; cần đối chiếu dashboard):
  - 30 phút: khoảng **2.070 credit** (2.059 request của hệ thống + khoảng 10 lần gọi thử);
  - sau đợt quét khởi động: stream + verifier khoảng 1.700 request/giờ, discovery tối đa khoảng 1.200/giờ khi còn token chờ quét;
  - tổng khoảng **70.000 credit/ngày, khoảng 2,1 triệu/tháng**, dưới hạn mức 10 triệu/tháng của gói Developer.
- **WebSocket:** chưa biết Helius có tính credit cho từng tin nhắn không. Luồng pump.fun ghi hơn 43.000 giao dịch trong 30 phút. Người dùng so số credit trên dashboard: nếu tăng nhiều hơn khoảng 2.100 trong 30 phút, phần chênh lệch là do WebSocket.
- Khuyến nghị: tắt chế độ tự mua thêm credit (autoscaling/overage) trên dashboard Helius và đặt cảnh báo mức dùng nếu có.

---

## 5. Discovery gọi nhiều lúc khởi động (đã phân tích)

- Lúc khởi động, discovery quét 3 token đã migration chưa từng quét: 14 trang chữ ký + 1.200 giao dịch = **1.214 request**. Đợt quét chạy từ 02:46:41 đến 02:47:32, sau đó không còn request nào.
- Lặp lại mỗi 60 phút, mỗi lượt tối đa 3 token. Hiện còn 37 token chờ quét, mất khoảng 12 giờ.
- Không tạm dừng discovery vì tốc độ không duy trì liên tục.

---

## 6. Rollback

- **Quay về RPC công khai:** xóa biến `HELIUS_API_KEY` trong Environment variables rồi tạo lại container. Code tự dùng RPC công khai khi không có khóa.
- **Chạy lại `sync`:** `docker compose ... up -d sync`. Chưa làm vì đang thử Giai đoạn A.

---

## 7. Bước tiếp theo (chờ phê duyệt, chưa thực hiện)

1. Người dùng đối chiếu credit trên dashboard Helius (mục 4).
2. Quyết định có bật lại `sync` hay không:
   - bật lại = backfill khoảng 519 ví bằng Helius;
   - ước tính khoảng 1,5 triệu credit với cách hiện tại, hoặc khoảng 150.000 credit nếu làm Giai đoạn B.
3. Giai đoạn B chỉ làm khi được duyệt.

Bằng chứng: `docs/handover/test-log-giai-doan-a-helius.txt`.
