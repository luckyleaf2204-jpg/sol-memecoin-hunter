# SMART MONEY RADAR — BÁO CÁO GIAI ĐOẠN 1.5

**Nội dung:** Kiểm chứng Data Engine và chạy thực tế
**Ngày:** 22/11/2026 (giờ ghi trong báo cáo là giờ UTC; giờ Việt Nam = UTC + 7)
**Trạng thái chung:** PARTIAL.
- Đã chạy thật và có bằng chứng: kiểm chứng dữ liệu, PnL, Docker Compose.
- Phần đo bằng Helius: BLOCKED. Khóa Helius chỉ được nhập trên máy triển khai, nên môi trường thử không có khóa.
- Backfill: đang chạy, chưa xong toàn bộ hàng đợi.

Nguyên tắc đã giữ:
- Không đổi công thức Smart Money Score, PnL, ROI, Win Rate hay ngưỡng Verified.
- Không tạo dữ liệu giả.
- Không nhận khóa Helius qua chat.
- `trading_enabled` vẫn khóa.
- Không xóa DB khi chưa sao lưu. Bản sao lưu `backups/radar-20261122-161059.dump` (241 MB) đã khôi phục thử thành công.

---

## I. TỔNG HỢP THEO 9 YÊU CẦU

| # | Yêu cầu | Kết quả | Bằng chứng |
|---|---|---|---|
| 1 | Kiểm tra 682 ví, vì sao 0 ví Smart Money | **PASS** – đã xác định nguyên nhân bằng số liệu | `A-why-zero.txt`, `A2-score.txt` |
| 2 | 370.104 giao dịch giải mã, 32.577 xác minh: nguyên nhân, tỷ lệ đủ điều kiện tính PnL | **PASS** | `B-verification.txt` |
| 3 | Backfill 60 ngày: ưu tiên, checkpoint, không trùng | **PASS** cho checkpoint/không trùng/ưu tiên; **PARTIAL** cho tiến độ (hàng đợi chưa xong trên RPC công khai) | `C-backfill.txt`, `D-checkpoint.txt`, `E-audit.*`, `F-…`, `J-…`, `K-…` |
| 4 | Chuẩn bị dùng Helius trả phí (khóa chỉ nhập vào .env trên máy triển khai) | **DONE** – sẵn sàng, xem mục V | `config.py`, `.env.example` |
| 5 | Độ trễ stream, discovery, wallet sync, verifier, price worker bằng Helius | **BLOCKED** với Helius (không có khóa ở đây); **PASS** đo nền bằng RPC công khai, có công cụ `radar latency` để đo lại trên máy triển khai | `J-run25.txt`, `K-run30-after-fix.txt` |
| 6 | Docker Compose đầy đủ: DB, Redis, API, frontend, worker | **PASS** – 13 container, HTTPS, khôi phục dữ liệu, khởi động lại daemon; phát hiện và sửa 4 lỗi ổn định, chạy lại 30 phút | `G-compose.txt`, `J-run25.txt`, `K-run30-after-fix.txt` |
| 7 | Kiểm tra lại PnL, ROI, Win Rate, Score bằng giao dịch đã xác minh | **PASS** | `H-verify-pnl.json` |
| 8 | Chưa làm Permutation Test, AI agent | Tuân thủ – không làm | — |
| 9 | Không đổi công thức Score, không tạo dữ liệu giả | Tuân thủ | `git diff` không chạm `analytics/scoring.py` |

Các file bằng chứng (A–K) nằm trong thư mục `bang chung` của gói bàn giao. Log kiểm thử tổng hợp: `docs/handover/test-log-giai-doan-1-5.txt`.

---

## II. TRẢ LỜI CÁC CÂU HỎI BẮT BUỘC

### 1. Vì sao hiện có 0 ví Smart Money?

Nguyên nhân chính là **thiếu lịch sử**, không phải công thức sai.

- Trong 682 ví, chỉ **58 ví** đã đọc được lịch sử (complete/partial). 429 ví ứng viên đang chờ backfill.
- 198 ví bị bỏ qua vì giao dịch tần suất cao. Trong đó:
  - 156 ví có từ 200 lệnh/ngày trở lên (hành vi bot);
  - 33 ví có 50–200 lệnh/ngày;
  - 9 ví dưới 50 lệnh/ngày. Đây là ví bị rải giao dịch rác, không phải bot. Tôi đã sửa bộ lọc và đưa 9 ví này lại vào hàng đợi.

**Phễu lọc** (30 ngày, áp dụng tuần tự cho 58 ví đã đọc lịch sử):

| Bước | Số ví |
|---|---|
| Đã đọc lịch sử | 58 |
| ≥ 5 vị thế đã đóng, mỗi vị thế ≥ 2 SOL | 37 |
| + Win Rate ≥ 65% | 8 |
| + Median buy ≥ 2 SOL | 8 |
| + PnL > 0 và ROI > 0 | 8 |
| + Độ tin cậy HIGH/MEDIUM | 8 |
| + Score ≥ 65 | **0** |

Vì sao 8 ví còn lại đều dưới 65 điểm (`A2-score.txt`):

- **6/8 ví là BOT hoặc LIKELY_BOT.** Hệ số nhân hành vi ×0,54–0,85 kéo điểm xuống, nên điểm cao nhất chỉ còn 56,94.
- **2 ví giống người** có lãi nhỏ, ví dụ 4oN6 lãi 1,8 SOL, nên điểm PnL/ROI thấp. Ví này đạt 51,46.
- Trên 37 ví có ≥ 5 vị thế, điểm bị mất trung bình theo thành phần:

  | Thành phần | Điểm mất bình quân |
  |---|---|
  | Win Rate | 16,0 |
  | ROI | 13,0 |
  | Median buy | 12,6 (median buy bình quân 4,92 SOL) |
  | PnL | 10,2 |
  | Vào lệnh sớm | 9,8 |
  | Ổn định | 3,2 |

**Phát hiện quan trọng (lỗi dữ liệu, đã sửa).** Kiểm toán lại cho thấy **44/60 ví "đủ/một phần lịch sử" thực ra bị thiếu chữ ký**.

- Nguyên nhân: RPC publicnode cắt lịch sử. Ví thử chỉ trả về 120 trên 670 chữ ký.
- Hệ quả: đánh giá trước đây của 44 ví này không đáng tin.
- Cách sửa:
  - đánh dấu publicnode `full_history=False`, nên việc liệt kê lịch sử chỉ dùng endpoint đủ lịch sử (Helius / api.mainnet-beta);
  - chạy `backfill-audit --apply` để đưa 44 ví vào lại hàng đợi.

**Kết luận.** Số 0 là kết quả đúng với dữ liệu hiện có. Chỉ nên đánh giá lại sau khi backfill xong; Helius sẽ rút thời gian đó từ khoảng 1,5 ngày xuống còn vài giờ. Không có thay đổi công thức nào để "làm tăng" số ví.

### 2. Bao nhiêu ví có lịch sử đủ để đánh giá?

| Thời điểm | Đủ lịch sử | Một phần (chạm trần 3.000 chữ ký) | Ghi chú |
|---|---|---|---|
| Trước kiểm toán (số anh nêu) | 39 | 17 | 44 ví trong số này thực ra bị cắt lịch sử |
| Sau kiểm toán 16:04 | 5 | 11 | 44 ví về hàng đợi, đọc lại có checkpoint |
| Cuối đợt chạy Docker 30 phút | 7 | 17 | xem mục 4 |

- Có **37 ví** đủ ≥ 5 vị thế đóng ≥ 2 SOL (đủ mẫu tính Win Rate).
- Có **36 ví** đạt độ tin cậy HIGH/MEDIUM.
- Các con số này tính trước kiểm toán, nên cần tính lại sau khi backfill xong.

### 3. Bao nhiêu giao dịch xác minh thành công / thất bại?

Tổng số dòng giao dịch đã giải mã: 372.792.

| Nguồn | Trạng thái | Số dòng | Tỷ lệ |
|---|---|---|---|
| Stream (log chương trình) | partially_verified – theo thiết kế | 334.734 | 89,8% |
| Đọc giao dịch đầy đủ (verifier/backfill) | verified | 35.305 | 9,5% |
| Đọc giao dịch đầy đủ | partially_verified | 2.753 | 0,7% |

**Nguyên nhân chỉ ~9,5% "verified".** Đây không phải lỗi xác minh.

- Stream chỉ đọc log chương trình nên luôn là "một phần".
- Chỉ giao dịch của ví đã được đọc lịch sử mới được tải đầy đủ và đối chiếu số dư.
- Với nhóm ví đã đọc lịch sử, tỷ lệ verified là **98,0%** (23.081/23.547).
- Phần còn lại thuộc các nhóm:
  - ví đang chờ backfill: 150.605 dòng;
  - ví bị bỏ qua hoặc chỉ quan sát: 116.815 dòng;
  - ví không theo dõi: 81.825 dòng.

**Thất bại / chưa xác minh đủ** (nhóm ví đã đọc lịch sử, 466 dòng):

| Lý do | Số dòng |
|---|---|
| Ví trả/nhận thêm tài sản khác (USDC) trong cùng giao dịch | 192 |
| Đang chờ đối chiếu | 98 |
| Đường cong định giá bằng USDC | 70 |
| Pool đảo chiều | 51 |
| Lệch vài lamport hoặc lệch số token | còn lại |

**Đủ điều kiện tính PnL.** Có 1.275 vị thế đã đóng ≥ 2 SOL. Trong đó **1.209 (94,8%) verified**.

Ngoài ra, 32.171 giao dịch thất bại on-chain (thuộc 57 ví) được loại, không tính PnL.

**Kiểm tra độc lập PnL** (mục 7).

- Phạm vi: 2.082 vị thế, so lợi nhuận hệ thống với biến động số dư SOL thực tế của ví trên chain (không qua bộ giải mã). Bỏ qua 135 vị thế có chuyển token.
- Kết quả:
  - **99,71%** vị thế lệch ≤ 0,001 SOL;
  - **100%** lệch ≤ 0,01 SOL;
  - lệch lớn nhất 0,004993 SOL;
  - tổng chênh +0,0153 SOL.
- Win Rate / PnL / số vị thế: đếm lại 60/60 ví khớp.
- Score: tính lại 60/60 ví, chênh 0.

### 4. Backfill đã xử lý thêm được bao nhiêu ví?

- **Checkpoint, chạy thật trên ví FA3x…** Chạy lần 1 rồi ngắt ở giây 75 (đã ghi 400 chữ ký). Chạy lần 2 tiếp tục và hoàn tất:
  - **0** giao dịch phải tải lại từ RPC;
  - **0** dòng trùng (signature, event_key).
- **Thứ tự ưu tiên** đã cài trong scheduler:
  1. Smart;
  2. thêm tay / watchlist;
  3. ví đang theo dõi;
  4. patient winner đang chờ;
  5. ví đã đạt điều kiện bắt buộc theo điểm sơ bộ;
  6. ví có nhiều vị thế đóng ≥ 2 SOL;
  7. điểm số.
- **Tiến độ thật trên RPC công khai** (khoảng 4,4 chữ ký/giây):

  | Đợt | Số ví hoàn tất thêm |
  |---|---|
  | Local 16:05–16:10 | 0 |
  | Docker 16:25–16:50 | 4 (3 một phần ở mức trần 3.000, 1 đủ với 772 chữ ký) |
  | Docker 16:55–17:25 (sau sửa lỗi) | 2 (cả 2 một phần ở mức trần 3.000) |
  | Tổng sau kiểm toán | Đủ 5 → 7, một phần 11 → 17: **thêm 8 ví** có lịch sử đọc lại đầy đủ trong phạm vi 60 ngày / 3.000 chữ ký |

  Hàng đợi còn 514 (513 chờ + 1 đang chạy; hàng đợi tăng vì discovery liên tục thêm ứng viên mới) ví. Một ví có 3.000 chữ ký mất khoảng 10–11 phút trên RPC công khai. Ước tính cần khoảng 1,5 ngày để xong; với Helius trả phí chỉ còn vài giờ.

### 5. Độ trễ thực tế khi dùng Helius

**BLOCKED.** Môi trường thử không có khóa Helius, vì anh yêu cầu chỉ nhập khóa vào .env trên máy triển khai. Các số dưới đây đo bằng **RPC công khai** (solana_public/publicnode) để làm mốc so sánh. Lệnh đo lại trên máy triển khai nằm ở mục V.

| Chỉ số (RPC công khai) | Đợt 25 phút (trước sửa) | Đợt 30 phút (sau sửa) |
|---|---|---|
| Stream: từ khối đến lúc lưu, p50 / p90 / p99 | 6,97 / 13,09 / 15,67 giây | 4,44 / 13,97 / 40,18 giây (p99 tăng do websocket công khai ngắt khoảng 1 phút một lần) |
| Verifier (lệnh live), p50 / p90 | 8,46 / 15,79 giây | 6,99 / 21,07 giây (n = 245) |
| Giá SOL/USD (Jupiter), độ tuổi | (đọc nhầm dòng cũ, xem mục IV) | 46,9 giây (cập nhật mỗi phút) |
| Nến giá on-chain mới nhất, độ tuổi | 979,7 giây (do stream im lặng) | 106,9 giây |
| Cảnh báo KOL mua: từ khối đến lúc tạo cảnh báo, p50 / max | 6,99 giây (n=1, đo trước đó) | 9,38 / 13,15 giây (n = 4) |
| Wallet sync (lần đồng bộ gần nhất), p50 | 4.310 giây | 6.438 giây (sync bận backfill, xem mục 6) |
| Discovery | 26.578 giây (n=2; chờ ví mua token thứ 2, theo thiết kế) | p50 0,89 giây (n = 5); 1 ví 26.703 giây do chờ giao dịch thứ 2 |

Ghi chú về discovery: một ví cần mua ≥ 2 token ≥ min buy thì mới thành ứng viên. Con số này chủ yếu là thời gian chờ ví tự giao dịch lần 2, không phải độ trễ xử lý.

### 6. Docker Compose và toàn bộ worker có chạy ổn định không?

**Những gì đã chạy thật** (`G-compose.txt`):

- 13 service:
  - hạ tầng: postgres (TimescaleDB 2.17 PG16), redis, backup;
  - ứng dụng: api, frontend, caddy;
  - worker: stream, verifier, sync, discovery, prices, analytics, signals.
- Khôi phục dữ liệu thật bằng `deploy/backup/restore.sh`: mã thoát 0, 699 ví.
- Kiểm tra qua HTTPS (Caddy, CA nội bộ):
  - `/api/health` 200;
  - chưa đăng nhập 401;
  - đăng nhập 200;
  - cookie `HttpOnly; Secure; SameSite=lax`;
  - có HSTS, X-Frame-Options DENY, nosniff, Referrer-Policy.
- Thử khởi động lại (dừng rồi bật lại Docker daemon, giống reboot máy chủ): mọi container tự chạy lại, dữ liệu còn nguyên, số dòng giao dịch tiếp tục tăng.

**Đợt 25 phút đầu (16:25–16:50).** 0 lần restart, 0 OOM, nhưng phát hiện 4 lỗi. Tôi không báo PASS cho đợt này.

| Lỗi | Nguyên nhân | Sửa |
|---|---|---|
| analytics lỗi lặp lại "numeric field overflow" ở ví ATVDZ44t… | Ví chuyển hết token ra ngoài, giá vốn còn lại là sai số dấu phẩy động 8,9e-16 SOL, nên ROI = −1,1×10¹⁴ và tràn cột | Giá vốn dưới 1 lamport coi là 0, ROI = "không áp dụng". Không đổi công thức; thêm test hồi quy |
| sync báo unhealthy | Chỉ gửi heartbeat sau mỗi lô ví, mà 1 ví 3.000 chữ ký mất khoảng 11 phút | Heartbeat riêng mỗi 60 giây, kèm danh sách ví đang xử lý và thời gian chạy (ví bị treo sẽ lộ ra) |
| Stream im lặng 14 phút, socket vẫn mở | publicnode ngừng gửi mà không đóng kết nối | Watchdog: 45 giây không có tin thì ngắt và chuyển endpoint |
| Màn hình báo "Workers: Error"; công cụ đo báo SOL/USD cũ 2.391 giây | Đọc nhầm dòng heartbeat/trạng thái cũ của lần chạy 1 tiến trình trước đó | Giao diện chỉ xét chế độ đang chạy; công cụ đo đọc thẳng bảng `sol_usd_prices` |

**Đợt 30 phút sau khi sửa (16:55–17:25)** (`K-run30-after-fix.txt`):

| Hạng mục | Kết quả |
|---|---|
| Container | 13/13 chạy; tất cả service có healthcheck đều **healthy**, kể cả sync và discovery (trước đây unhealthy) |
| Restart / OOM | 0 / 0 |
| Dòng ERROR/Traceback | **0** ở cả 10 service (trước: analytics 4) |
| Heartbeat cuối đợt | stream 7 giây, verifier 11 giây, sync 18 giây (đang xử lý ví 26g9… 511 giây), analytics 120 giây, discovery 166 giây (dưới ngưỡng 300 giây) |
| Stream | Trạng thái `ok`, tin nhắn cuối cách 3,3 giây; tổng số dòng tăng 404.615 → 427.414 (+22.799 trong 30 phút) |
| Watchdog | Kích hoạt 14 lần, 29 lần kết nối lại: websocket công khai không ổn định. Hệ thống tự phục hồi, không còn im lặng dài |
| Analytics | 14 vòng, mỗi vòng khoảng 17 giây, 67 ví; không còn lỗi tràn số |

**Đánh giá: PASS** cho tính ổn định (không lỗi, không restart, tự phục hồi).

Hai điểm còn yếu, đều do RPC công khai:

- Websocket công khai bị ngắt khoảng 1 phút một lần, làm p99 độ trễ stream lên 40 giây.
- Khi sync bận backfill (2 luồng), việc đồng bộ tăng dần các ví đã đủ lịch sử bị chậm (p50 6.438 giây).

Helius sẽ xử lý cả hai, vì có websocket ổn định và tốc độ cao hơn. Đây là việc phải đo lại sau khi nhập khóa (mục V).

### 7. Ảnh giao diện và log kiểm thử mới

- Ảnh chụp từ stack Docker Compose qua `https://localhost` (Caddy), desktop 1440 px và iPhone 13 (file ảnh 01–06 trong gói bàn giao):
  - Overview, Top Wallets, Live Feed, Data Health (desktop);
  - Overview, Data Health (iPhone).
- Log: `docs/handover/test-log-giai-doan-1-5.txt`, gồm toàn văn các file bằng chứng A–K. Không chứa khóa, mật khẩu hay chuỗi kết nối.

---

## III. THAY ĐỔI MÃ NGUỒN TRONG GIAI ĐOẠN 1.5

Không thêm tính năng hay giao diện mới. Chỉ sửa lỗi dữ liệu và độ ổn định, và thêm công cụ kiểm chứng.

| File | Thay đổi |
|---|---|
| `radar/config.py`, `radar/solana/rpc.py` | Endpoint có cờ `full_history`; liệt kê lịch sử chỉ dùng endpoint đủ lịch sử |
| `radar/services/wallet_sync.py` | Bộ lọc tần suất cao tính theo lệnh pump.fun của chính ví (không theo số chữ ký rác); cache chữ ký không liên quan; ghi theo từng khối 200 chữ ký (checkpoint); thứ tự ưu tiên; heartbeat 60 giây |
| `radar/services/discovery.py` | Gửi heartbeat trước phần việc nặng |
| `radar/services/stream.py` | Watchdog im lặng 45 giây |
| `radar/analytics/positions.py` | Giá vốn dưới 1 lamport coi là 0 (chống tràn ROI) |
| `radar/tools/backfill_audit.py` | Mới: `python -m radar backfill-audit [--apply]` |
| `radar/tools/latency.py` | Mới: `python -m radar latency [--minutes N]` |
| `radar/tools/verify_pnl.py` | Mới: `python -m radar verify-pnl` – đối chiếu PnL với số dư on-chain |
| `frontend/components/Shell.tsx`, `frontend/app/health/page.tsx` | Không báo lỗi giả từ heartbeat cũ của chế độ chạy khác |
| `backend/tests/unit/*` | Thêm test: endpoint thiếu lịch sử, đối chiếu số dư độc lập, tràn ROI khi chuyển hết token |

Test đơn vị: 87 passed (13 test tích hợp bỏ qua khi không có DB cục bộ). Typecheck và build frontend: PASS.

---

## IV. LƯU Ý VỀ SỐ LIỆU

- Số "370.104 / 32.577" anh nêu là ảnh chụp lúc trước. Lúc kiểm tra (15:58) đã là 372.792 / 35.305, và số liệu tiếp tục tăng khi hệ thống chạy.
- Công cụ đo lần đầu báo SOL/USD cũ 2.391 giây. Đó là lỗi của công cụ đo (đọc dòng trạng thái cũ), không phải giá bị cũ: bảng `sol_usd_prices` vẫn có bản ghi mỗi phút. Đã sửa.
- Môi trường thử dùng thêm một file override mạng riêng (proxy của sandbox). File này **không** nằm trong repo. Trên VPS hoặc máy Windows chạy bình thường, không cần.

---

## V. CÁC BƯỚC DÙNG HELIUS TRÊN MÁY TRIỂN KHAI (anh tự làm, không gửi khóa qua chat)

1. Mở file `.env` trong thư mục `smart-money-radar` trên máy triển khai, điền:
   ```
   HELIUS_API_KEY=<khóa của anh>
   HELIUS_RPS=<số request/giây theo gói trả phí, ví dụ 50>
   ```
2. Khởi động lại worker:
   ```
   docker compose up -d
   ```
3. Kiểm tra trang Data Health. Ô "helius_api_key" phải chuyển sang "đã cấu hình" (không hiện giá trị). Thanh trên cùng phải ghi "Helius: Connected".
4. Kiểm toán lại lịch sử bằng Helius, đưa ví thiếu vào hàng đợi:
   ```
   docker compose exec sync python -m radar backfill-audit --apply
   ```
5. Sau 30 phút, đo độ trễ và tốc độ backfill bằng Helius:
   ```
   docker compose exec sync python -m radar latency --minutes 30
   ```
   Dòng `"stream_endpoint"` phải là `helius`. Gửi lại kết quả này (không chứa khóa) để hoàn tất mục 5.
6. Khi backfill xong, kiểm tra lại PnL và Score:
   ```
   docker compose exec sync python -m radar verify-pnl
   ```

---

## VI. VIỆC CÒN LẠI

| Việc | Trạng thái | Cần gì |
|---|---|---|
| Đo độ trễ bằng Helius | BLOCKED | Anh nhập khóa vào .env trên máy triển khai, chạy bước V.5 |
| Backfill hết hàng đợi | Đang chạy | Khoảng 1,5 ngày trên RPC công khai; vài giờ với Helius |
| Đánh giá lại số ví Smart Money | Chờ backfill | Chạy lại sau khi backfill xong; không đổi công thức |
| 15 ví backfill lỗi (lỗi kết nối RPC công khai) | Sẽ tự thử lại | Scheduler đưa lại vào hàng đợi; ít lỗi hơn khi có Helius |
