# BƯỚC 1 – SỬA DECODER: 4 TOKEN ĐỌC RA 0 LỆNH

**Ngày:** 23/11/2026.
**Trạng thái:** ĐÃ XONG VÀ KIỂM CHỨNG.
**Credit dùng ở bước này:** khoảng 40 (4 lần lấy mẫu × 20 giao dịch).

## 1. Nguyên nhân thật (khác giả thuyết ban đầu)

Ban đầu tôi đoán sự kiện nằm trong inner instruction. Kết quả kiểm tra: **không phải**.
- Bộ đọc log đã lần đúng chương trình được gọi lồng bên trong. Giao dịch vào qua các router/bot (`MAyhSmzX…`, `L2TExM…`, `DkhKhd…`, `BMofZX…`), và sự kiện TradeEvent vẫn được tìm thấy.
- Nguyên nhân thật: cả 4 token đều là **bonding curve định giá bằng token khác SOL**:

  | Token | Quote token |
  |---|---|
  | `Cmf39V6X…` (chế độ Mayhem), `FVzqGqRm…`, `82Z6CcE5…` | USDC (`EPjFWdd5…`) |
  | `89iRVoDV…` | token `4CxjZyMN…pump` |

- Với các curve này, mọi trường SOL trong TradeEvent bằng 0. Số tiền thật nằm ở **phần mới (v2) của TradeEvent**, gồm: `quote_mint`, `quote_amount`, `virtual_quote_reserves`, `real_quote_reserves`.
- Bộ giải mã cũ không đọc phần v2 này và bỏ qua lệnh, nên ra 0 lệnh.

## 2. Đã sửa

| File | Thay đổi |
|---|---|
| `decoder/events.py` | Đọc thêm 4 trường quote (sau tên lệnh 37 byte) |
| `decoder/logs.py` | Thêm tùy chọn `quote_rates` (mặc định tắt): bật lên thì lệnh trên curve định giá bằng token khác được trả ra, quy đổi sang SOL theo tỷ giá do bên gọi truyền vào |
| `tests/` | Dữ liệu kiểm thử là giao dịch thật; test "mặc định không đổi" và "số quote khớp biến động USDC của ví" |

**Hành vi mặc định của stream và PnL không đổi.** Test đã chứng minh: không truyền `quote_rates` thì kết quả y như trước.

## 3. Kiểm chứng trước/sau (80 giao dịch thật đã lưu)

| Token | Giao dịch | Lệnh TRƯỚC | Lệnh SAU | Đối chiếu với biến động token quote của ví |
|---|---|---|---|---|
| `82Z6CcE5…` | 20 | 0 | 2 | 2 khớp trong phạm vi phí |
| `89iRVoDV…` | 20 | 0 | 5 | 1 khớp trong phạm vi phí, 4 chưa kiểm tra được |
| `Cmf39V6X…` | 20 | 0 | 19 | **17 khớp tuyệt đối**, 1 trong phạm vi phí, 1 chưa kiểm tra được |
| `FVzqGqRm…` | 20 | 0 | 8 | 2 trong phạm vi phí, 3 chưa kiểm tra được, 3 qua router (giải thích dưới) |
| **Tổng** | **80** | **0** | **34** | **23 khớp, 8 chưa kiểm tra được, 3 qua router** |

**3 lệnh "lệch" là lệnh mua qua router, không phải lỗi giải mã.** Ví trả bằng SOL, router đổi SOL sang USDC rồi mua trên curve:

| Giao dịch | SOL ví trả | USDC curve nhận |
|---|---|---|
| `4co587V3Ky3R…` | 0,503 SOL | 33,54 USDC |
| `4YBfAU77Y4ZL…` | 1,019 SOL | 68,49 USDC |
| `4DBDr1nPP11V…` | 0,303 SOL | 20,55 USDC |

Với giá SOL khoảng 70 USD, số SOL ví trả bằng số USDC curve nhận cộng phí route. Với loại lệnh này, chi phí thật lấy theo SOL của ví (cách bộ giải mã giao dịch đầy đủ đang dùng).

**"Chưa kiểm tra được"** gồm hai trường hợp: ví có nhiều lệnh trong cùng một giao dịch, hoặc router giữ token quote thay cho ví.

## 4. Phát hiện kèm theo

- `BwWK17cb…` là **agent của chế độ Mayhem** (tài khoản giao thức) và thực hiện phần lớn lệnh trên token Mayhem. Đây không phải ví người dùng, sẽ được loại khỏi danh sách người mua sớm ở bước 4.
- Lệnh `buy_exact_quote_in` của ví người thật `BRNiER…` (giao dịch `4UehS76K6M…`): `quote_amount` là 21,515 USDC trước phí, ví thực trả 21,884 USDC. Phần chênh 0,369 USDC đúng bằng phí pump và phí creator.

## 5. Bằng chứng

- Mẫu giao dịch gốc: `winner_data/raw_samples/*_first20.json`
- Kết quả so sánh trước/sau: `winner_data/raw_samples/decoder_before_after.txt`
- Test: `backend/tests/unit/test_quote_curve_decoder.py`
- Commit: `889cfb8`
