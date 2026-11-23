# SMART MONEY RADAR – BÁO CÁO BƯỚC 1: BACKFILL BẰNG HELIUS (ĐỢT THỬ 50 VÍ)

**Ngày:** 23/11/2026, 03:33 → 04:17 UTC.
**Cấu hình:**
- `helius_tx_history = true`
- trần 150.000 credit/ngày
- 50 ví/ngày, lịch sử 60 ngày, tối đa 3.000 chữ ký mỗi ví

Không đổi schema, không đổi scoring, `trading_enabled = false`. Sao lưu trước khi chạy: `radar-20261123-033138.dump`.

## 1. Kiểm tra getTransactionsForAddress (gTFA) trước khi dùng

| Kiểm tra | Kết quả |
|---|---|
| Gói Developer gọi được | Có. Mỗi lần trả tối đa 100 giao dịch đầy đủ, kèm `paginationToken` |
| Cấu trúc dữ liệu | Giống hệt getTransaction (jsonParsed), so từng trường trên 5 giao dịch: 0 khác biệt |
| Giải mã | Bộ decoder cho cùng giao dịch mua/bán |
| Danh sách chữ ký | Khớp 1.000/1.000 với getSignaturesForAddress, gồm cả 11 giao dịch thất bại |
| Phiên bản giao dịch | Phải dùng `maxSupportedTransactionVersion = 1` (code đã dùng sẵn) |

## 2. Kết quả 50 ví

| Kết quả | Số ví |
|---|---|
| Backfill xong bằng gTFA | **23** (3 đủ lịch sử, 20 chạm trần 3.000 chữ ký) |
| Bị loại vì giao dịch tần suất cao (≥ 1.000 giao dịch/ngày, quy tắc cũ không đổi) | 27 |
| Lỗi | 0 |

Số liệu của 23 ví:

| Chỉ số | Giá trị |
|---|---|
| Chữ ký trong cửa sổ 60 ngày | 64.087 |
| Giao dịch thất bại on-chain (lưu riêng, không tính PnL) | 17.732 |
| Giao dịch không liên quan pump.fun, đã nhận biết từ trước (bỏ qua) | 12.227 |
| Giao dịch lấy được | 34.128 |
| **Giao dịch thiếu** | **0** |
| Giao dịch mua/bán giải mã được | 17.878 |
| Trong đó đối chiếu khớp số dư (verified) | 16.281 (91,1%) |
| Chuyển token vào/ra | 1.129 |
| Phí mạng của các lệnh | 4,11 SOL |
| Dòng ghi trùng | 0 |

## 3. Độ chính xác PnL (`verify-pnl --since 03:33:31`)

**PnL theo SOL:** so với biến động số dư SOL thực tế của ví (đã gồm phí) trên 1.906 vị thế đã đóng:

| Mức lệch | Tỷ lệ vị thế |
|---|---|
| ≤ 0,001 SOL | **99,79%** |
| ≤ 0,01 SOL | 100% |

Lệch lớn nhất 0,0038 SOL. Tổng chênh lệch +0,0114 SOL.

**Token sau khi đóng vị thế:** 1.894 / 1.906 vị thế có số dư token on-chain về 0 hoặc chỉ còn bụi. 12 vị thế (0,6%) vẫn còn token. Một số trường hợp số token còn lại lớn hơn số đã mua, khả năng là nhận token từ giao dịch nằm ngoài 3.000 chữ ký. Cần điều tra thêm.

**Win Rate / PnL / Score:** tính lại khớp 23/23 ví, chênh 0.

## 4. Credit

| Nguồn | Credit (ước tính theo bảng giá) |
|---|---|
| Chi phí đúng của 1 lượt backfill 23 ví (gTFA) | **6.428**, khoảng 280/ví; 300 với ví chạm trần 3.000 chữ ký |
| Thực tế worker sync đã dùng | 40.690 (do lỗi chạy lặp, mục 5) |
| Verifier / discovery | 470 / 2.432 |
| Tổng bộ đếm từ 03:31 | 43.592 |

Credit thực tế cần đối chiếu với dashboard Helius.

## 5. Lỗi phát hiện và đã sửa

- **Lỗi:** mỗi ví bị backfill 6 lần. Hàng đợi cũ chứa nhiều việc trùng cho cùng ví; khi ví đã backfill xong, việc trùng vẫn chạy backfill lại.
- **Hệ quả:** khoảng 34.000 credit bị tốn thêm. Dữ liệu không bị ghi trùng (0 dòng trùng).
- **Sửa:** việc backfill cho ví đã có lịch sử (complete/partial) giờ chỉ đồng bộ phần mới. Có test, đã triển khai lúc 04:41.

## 6. Ghi nhận cần quyết định

1. **27/50 suất bị dùng cho ví bot.** Đề xuất: ví bị loại vì tần suất cao không tính vào hạn mức 50 ví/ngày.
2. **20/23 ví chạm trần 3.000 chữ ký.** Các ví này nhiều giao dịch rác, nên lịch sử 60 ngày chưa đủ.
3. **Tỷ lệ verified 91,1% thấp hơn mức 98% trước đây**, có ví chỉ 38–48%. Cần xem nguyên nhân (giao dịch qua USDC/router) trước khi chấm điểm các ví này.
4. **12 vị thế còn token sau khi đóng:** cần điều tra.
