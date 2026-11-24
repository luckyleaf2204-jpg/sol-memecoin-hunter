# KẾ HOẠCH KỸ THUẬT – TELEGRAM CALLER TRACKER

**Ngày:** 23/11/2026.

**Nguyên tắc:**
- Module tách riêng, chỉ **thêm** bảng/route/trang, không sửa bảng hay logic Smart Money Radar hiện có.
- Không giao dịch, không cần private key hay seed phrase.

---

## 1. KIẾN TRÚC HIỆN TẠI VÀ ĐIỂM TÍCH HỢP

| Thành phần có sẵn | Dùng lại cho module mới |
|---|---|
| Worker theo vai trò (`python -m radar <role>`, `ROLES` trong `__main__.py`); mỗi vai trò là 1 service trong `docker-compose.yml` | Thêm vai trò **`callers`**: 1 container mới, cùng image backend |
| Migration SQL theo phiên bản (`backend/migrations/NNN_*.sql`, tự áp dụng khi khởi động) | Thêm `005_telegram_callers.sql`: **chỉ tạo bảng mới** |
| Bảng `tokens` (giá/vốn hóa cập nhật từ stream), `amm_pools`, `wallet_transactions`, `wallets`, `watched_wallets` (KOL), `wallet_performance` | Giá lúc gọi kèo; đối chiếu ví Smart Money/KOL mua bán quanh thời điểm gọi |
| `radar/sources/dexscreener.py`, `geckoterminal.py` (miễn phí, đã có trong danh sách host) | Giá dự phòng khi token chưa có trong stream; nến 1 phút để tính ATH/drawdown |
| `radar/services/telegram.py` (Bot API: gửi tin, nhận lệnh) và bảng `alerts` | Cảnh báo dùng lại hàm gửi và bảng `alerts` (thêm loại cảnh báo mới, không đổi cấu trúc bảng) |
| API FastAPI `radar/api/app.py` (đăng nhập, `Auth`) | Thêm `radar/api/callers.py` (router riêng), gắn vào `app.py` bằng 1 dòng |
| Frontend Next.js, menu trong `components/Shell.tsx` | Thêm trang `frontend/app/callers/page.tsx` và 1 mục menu |

**Helius:** module **không cần** Helius. Giá lấy từ stream, DexScreener và GeckoTerminal. Đối chiếu Smart Money dùng dữ liệu đã có trong database. Không phát sinh credit.

---

## 2. KẾT NỐI TELEGRAM – ĐÁNH GIÁ PHƯƠNG ÁN

| Phương án | Đọc được kênh nào | Cần gì | Sửa/xóa bài | Lịch sử cũ | Đánh giá |
|---|---|---|---|---|---|
| **A. Trang xem trước công khai `t.me/s/<kênh>`** | Kênh **công khai** (ai cũng xem được trên web) | Không cần tài khoản | Thấy nhãn "edited"; bài bị xóa biến mất khi quét lại | **Có**: lật trang ngược về quá khứ | **Mặc định.** Không cần đăng nhập, hợp lệ, chạy trên Docker Desktop. Quét mỗi 30–60 giây/kênh, độ trễ 30–60 giây |
| **B. Telegram Client API (MTProto, thư viện Telethon)** bằng tài khoản của chính anh/chị | Kênh tài khoản đã tham gia (công khai hoặc riêng tư **đã được mời**) | `api_id`/`api_hash` tạo tại my.telegram.org; đăng nhập 1 lần bằng số điện thoại + mã OTP (file phiên lưu trong volume Docker) | **Có**: sự kiện sửa/xóa theo thời gian thực | Có | **Tùy chọn**, độ trễ vài giây. Chỉ đọc, giới hạn tốc độ để tránh bị Telegram khóa. Không dùng để vào kênh không được phép |
| **C. Bot API** | Chỉ kênh bot được thêm làm quản trị viên | Bot token (đã có) | Có (`edited_channel_post`) | Không | Hỗ trợ cho kênh của chính anh/chị. Không dùng được cho kênh của người khác |

**Đề xuất:**
- Triển khai **A** ngay (không cần thông tin đăng nhập, kiểm chứng được lịch sử).
- Viết sẵn bộ thu **B** dưới dạng tùy chọn, chỉ bật khi anh/chị điền `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` vào `.env` trên máy mình và tự đăng nhập bằng lệnh `docker compose run --rm callers python -m radar tg-login`.
- Mọi phương án chỉ đọc nội dung tài khoản/kênh được phép xem. Không vượt quyền kênh riêng tư.

---

## 3. DỮ LIỆU (MIGRATION `005_telegram_callers.sql`, CHỈ THÊM BẢNG)

| Bảng | Nội dung |
|---|---|
| `tg_channels` | Kênh theo dõi: username, tên, nguồn (web/client/bot), bật/tắt, ngày thêm, con trỏ bài cuối, trạng thái quét |
| `tg_messages` | Bài đăng thô. Khóa (kênh, message_id). Lưu: nội dung, hash nội dung, thời điểm đăng, `edited_at`, `deleted_at`, số lần thấy. **Chống xử lý trùng** bằng khóa chính + hash |
| `tg_calls` | Kèo: kênh, message_id, mint (CA đã kiểm tra hợp lệ base58 32–44 ký tự, tồn tại on-chain), ticker/tên trong bài, thời điểm gọi, vốn hóa/giá **ghi trong bài** (nếu có), `is_repeat` (kênh đã gọi token này trước đó), `first_call_at`. Khóa (kênh, message_id, mint) |
| `tg_call_marks` | Giá thực tế theo từng mốc: 0 (lúc phát hiện), 5 phút, 15 phút, 30 phút, 1 giờ, 2 giờ, 6 giờ, 12 giờ, 24 giờ. Mỗi mốc lưu giá, vốn hóa, thanh khoản, **nguồn** (stream / DexScreener / GeckoTerminal OHLCV) và **timestamp thực tế** của giá |
| `tg_call_outcomes` | Tổng kết 24 giờ: ATH, thời điểm ATH, drawdown sâu nhất, thời gian đạt X2/X3/X5, rug (giá −90% hoặc thanh khoản −80%), độ tin cậy dữ liệu |
| `tg_call_sm` | Đối chiếu Smart Money mỗi kèo: số ví mua trước/sau khi gọi, ví bán trong 30 phút sau gọi, khối lượng, danh sách ví kèm chữ ký giao dịch |

**Quy tắc giá entry:**
- **Kèo phát hiện trực tiếp:** giá chụp ngay lúc phát hiện, kèm độ trễ phát hiện.
- **Kèo lịch sử** (quét ngược `t.me/s`): giá lấy từ nến 1 phút GeckoTerminal **tại phút đăng bài**, gắn nhãn "lịch sử – OHLCV".
- Kèo không có nến tại thời điểm đó → "không định giá được", **không** dùng giá hiện tại.

---

## 4. CHỈ SỐ VÀ PHÂN HẠNG

**Bảng xếp hạng:**

| Chỉ số | Cách tính |
|---|---|
| Total Calls | Tổng số kèo |
| Valid Calls | Số kèo có CA hợp lệ |
| Win Rate | Tỷ lệ kèo đạt tiêu chí thắng: chạm ×2 trong 24 giờ |
| X2 / X3 Rate | Tỷ lệ chạm ×2 / ×3 |
| ROI 24 giờ trung bình / trung vị | |
| Max Drawdown | |
| Rug Rate | |
| Early Call Rate | Tỷ lệ kèo gọi khi giá chưa tăng quá 50% so với giá thấp nhất trong 60 phút trước khi gọi |
| Thời gian trung bình đạt ×2 | |
| Late/Dump Rate | Tỷ lệ kèo gọi sau khi giá đã tăng ≥ 3 lần trong 6 giờ trước đó, rồi giảm sau khi gọi |

**Caller–Smart Money Correlation** (0–1), gồm 3 phần:
- tỷ lệ kèo có ví Smart Money/KOL **mua trước** khi gọi;
- tỷ lệ có ví Smart Money **mua sau** khi gọi;
- tỷ lệ có ví **bán trong 30 phút** sau khi gọi (dấu hiệu xả hàng: tính điểm trừ).

Ghi rõ số ví đang có trong database để người xem biết chỉ số dựa trên tập ví nào.

**Phân hạng:**

| Hạng | Điều kiện |
|---|---|
| **A** | ≥ 30 kèo có kết quả 24 giờ đầy đủ; win rate ≥ 40%; ROI trung vị > 0; rug rate ≤ 20%; early call ≥ 50% |
| **B** | < 30 kèo có kết quả (chưa đủ mẫu), các chỉ số hiện có không xấu |
| **C** | Đủ mẫu nhưng chỉ số trung bình hoặc dao động mạnh |
| **D** | Late/dump rate ≥ 40%, hoặc rug ≥ 40%, hoặc ROI trung vị < −30% |

**Không bao giờ gán A khi chưa đủ 30 kèo.** Ngưỡng nằm trong `system_settings`, chỉnh được.

---

## 5. CẢNH BÁO (DÙNG LẠI TELEGRAM VÀ BẢNG `alerts`)

| Cảnh báo | Điều kiện |
|---|---|
| Kèo mới | Kênh theo dõi gọi CA mới |
| Đồng loạt | ≥ 2 kênh gọi cùng token trong 60 phút |
| Trùng Smart Money | Có ví Smart Money/KOL mua token trong ±15 phút quanh thời điểm gọi |
| Smart Money xả | Ví Smart Money/KOL bán ≥ 50% vị thế trong 30 phút sau gọi |
| ×2 / ×3 | Kèo chạm ×2, ×3 |
| Rug | Giá −90% hoặc thanh khoản −80% so với lúc gọi |

Có giới hạn số cảnh báo/giờ để không spam.

---

## 6. DANH SÁCH FILE

| File | Thay đổi |
|---|---|
| `backend/migrations/005_telegram_callers.sql` | **Mới.** 6 bảng |
| `backend/radar/callers/__init__.py` | **Mới.** Package |
| `backend/radar/callers/parse.py` | **Mới.** Nhận diện CA, ticker, vốn hóa/giá trong bài; chuẩn hóa nội dung, hash |
| `backend/radar/callers/sources.py` | **Mới.** Bộ thu `t.me/s` (mặc định), Telethon (tùy chọn), Bot API (kênh của mình); retry và backoff |
| `backend/radar/callers/tracker.py` | **Mới.** Chụp giá các mốc, tổng kết 24 giờ, rug |
| `backend/radar/callers/smartmoney.py` | **Mới.** Đối chiếu ví quanh thời điểm gọi |
| `backend/radar/callers/stats.py` | **Mới.** Bảng xếp hạng, phân hạng A–D |
| `backend/radar/callers/worker.py` | **Mới.** Vai trò `callers`: vòng quét, lịch chụp giá, cảnh báo |
| `backend/radar/api/callers.py` | **Mới.** API: kênh (thêm/xóa/bật/tắt), kèo, bảng xếp hạng, chi tiết kèo, đối chiếu Smart Money |
| `backend/radar/__main__.py` | **Sửa nhỏ:** thêm `callers` vào danh sách vai trò; lệnh `tg-backfill`, `tg-login` |
| `backend/radar/api/app.py` | **Sửa nhỏ:** gắn router mới |
| `backend/requirements.txt` | Thêm `telethon` (chỉ dùng khi bật phương án B) |
| `docker-compose.yml` | Thêm service `callers` (cùng image, volume cho file phiên Telethon) |
| `frontend/app/callers/page.tsx` | **Mới.** Trang Telegram Caller Tracker |
| `frontend/components/Shell.tsx` | Thêm 1 mục menu |
| `backend/tests/unit/test_callers_*.py` | **Mới.** Test nhận diện CA, chống trùng, chỉ số, phân hạng |
| `docs/telegram-callers.md` | **Mới.** Hướng dẫn trên Windows Docker Desktop |

**Không sửa:** bảng/logic hiện có, scoring, PnL, stream, các worker khác.

---

## 7. RỦI RO VÀ GIỚI HẠN ĐÃ BIẾT

| Rủi ro | Xử lý |
|---|---|
| `t.me/s` chỉ có kênh công khai; bài bị xóa phát hiện được khi quét lại (đánh dấu `deleted_at`) | Kênh riêng tư dùng phương án B (nếu tài khoản đã được mời) |
| Một số kênh không bật trang xem trước web | Báo "không đọc được qua web", gợi ý phương án B |
| Giá lịch sử của token rất mới có thể không có nến GeckoTerminal | Ghi "không định giá được", không ước tính |
| Telegram giới hạn tốc độ | Quét lần lượt, chờ giữa các kênh, backoff khi lỗi 429 |
| Môi trường cloud của tôi đang chặn `t.me` | Cần mở host `t.me` để kiểm thử bộ thu trên dữ liệu thật. Trên máy Windows của anh/chị không có giới hạn này |
