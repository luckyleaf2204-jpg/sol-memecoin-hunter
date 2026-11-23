# BƯỚC 2–3 – STREAM BỎ SÓT GIAO DỊCH TỐT NGHIỆP: SỬA VÀ KIỂM CHỨNG

**Ngày:** 23/11/2026.
**Trạng thái:** ĐÃ XONG VÀ KIỂM CHỨNG.
**Credit:** khoảng 11 (đếm giao dịch tốt nghiệp thật trên chain để đối chiếu). Phần đọc giao dịch đầy đủ trong stream dùng RPC công khai, miễn phí.

## 1. Nguyên nhân

| Nguyên nhân | Bằng chứng |
|---|---|
| **Log của giao dịch tốt nghiệp bị Solana cắt** (giao dịch rất lớn), mất sự kiện migration ở cuối log | 20/21 giao dịch tốt nghiệp đã lưu có dòng `Log truncated` và không còn sự kiện |
| Máy chủ thử nghiệm khởi động lại nhiều lần trong ngày | Stream mất kết nối Internet sau mỗi lần |

Trước khi sửa: stream ghi nhận **121** giao dịch tốt nghiệp trong 24 giờ, thực tế là **609** (khoảng 20%).

## 2. Đã sửa (không đổi schema, scoring hay PnL)

| File | Thay đổi |
|---|---|
| `decoder/migration.py` (mới, dùng chung) | Nhận ra giao dịch tốt nghiệp qua dòng `Instruction: Migrate`/`MigrateV2` ở đầu log, phần không bị cắt. Mint là token không phải WSOL có số dư lớn nhất |
| `services/stream.py` | Giao dịch tốt nghiệp thiếu sự kiện được đọc lại đầy đủ qua RPC công khai, ghi nhận mint và thời điểm |
| `db/repo.py` | `mark_migration_fallback`: không xóa pool đã biết, không đổi thời điểm tốt nghiệp đã có |
| `tests/` | Dữ liệu kiểm thử là giao dịch tốt nghiệp thật bị cắt log |

## 3. Kiểm chứng

### 3.1 Bằng dữ liệu và chữ ký đã lưu

| Kiểm tra | Trước | Sau |
|---|---|---|
| 33 giao dịch tốt nghiệp đã lưu: nhận đúng mint | 1/33 (chỉ đọc sự kiện) | **32/33** |
| Giao dịch thật bị cắt log (dữ liệu kiểm thử) | Không nhận ra | Nhận đúng mint; stream đưa vào hàng đọc lại |
| Bước 1: 80 giao dịch mẫu của 4 token định giá bằng token khác SOL | 0 lệnh | 34 lệnh, 23 khớp số dư, 3 qua router đã giải thích |
| Test đơn vị | – | 110 passed |

**Trường hợp sai 1/33:** giao dịch chứa thêm token `8EzKJgqe…` có số dư lớn hơn token tốt nghiệp `5fryFckR…`, nên bị chọn nhầm. Đây là hạn chế đã biết của quy tắc chọn mint (khoảng 3%), chưa sửa.

### 3.2 Chạy thật 58 phút (09:08:30 → 10:06:35 UTC)

| Chỉ số | Kết quả |
|---|---|
| Giao dịch tốt nghiệp thật trên chain (đếm qua tài khoản migration) | **23** |
| Stream ghi vào database | **22** |
| Khớp | **22/23 = 95,7%** (trước khi sửa khoảng 20%) |
| Giao dịch bị cắt log được đọc lại thành công | 30/30 lượt, 0 lỗi |
| Bị sót | `53LtBtkUs7DU…pump`, chữ ký `25C3i5w6wT3Fyibjs1b3DZfu…` |

**Lý do sót:** đọc lại giao dịch này ngoại tuyến thì code mới nhận đúng mint. Vậy stream **không nhận được** thông báo từ WebSocket công khai, dù kết nối không rớt lần nào trong giờ đo. Đây là giới hạn của endpoint công khai.

**Bộ đếm nội bộ lớn hơn số thật** (46 lượt từ sự kiện, 30 lượt từ đọc lại): stream đăng ký cả hai chương trình pump.fun và PumpSwap, mà giao dịch tốt nghiệp liên quan cả hai nên được giao 2 lần. Database **không bị ghi trùng**. Chỉ là số thống kê.

## 4. Bằng chứng

- `winner_data/step2_stream_check.txt`: kết quả đối chiếu 58 phút.
- `tests/fixtures/migration_truncated.json`, `tests/unit/test_migration_detection.py`.
- Commit: stream fix + báo cáo trên nhánh `claude/smart-money-radar-pumpfun-5kczmz`.
