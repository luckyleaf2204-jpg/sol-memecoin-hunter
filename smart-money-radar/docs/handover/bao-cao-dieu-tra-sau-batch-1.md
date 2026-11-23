# BÁO CÁO ĐIỀU TRA SAU BATCH 1 (50 VÍ, HELIUS gTFA)

**Ngày:** 23/11/2026 (giờ UTC).
**Phạm vi:** 23 ví được backfill trong batch 1, từ 03:33:31 đến 04:17 UTC.
**Nguyên tắc:** không chạy batch mới, không sửa scoring/PnL/schema, `trading_enabled = false`.
**Nguồn bằng chứng:** database, log, raw transactions đã lưu; **không gọi thêm Helius**.

Ký hiệu trạng thái: ✅ đã xác minh · ⚠️ chưa xác minh.

---

## 1. TỔNG KẾT ĐIỀU HÀNH

| Chỉ tiêu | Kết quả |
|---|---|
| Tổng số ví đã kiểm tra | **23** |
| Ví đủ điều kiện Smart Money (điểm ≥ 65 + đủ điều kiện bắt buộc) | **0** ✅. Ví điểm cao nhất: 78FmcvKt 32,5 và 6ntUoFQS 35,9, đều chỉ "qualifies" ở điều kiện bắt buộc |
| Ví đủ lịch sử 60 ngày (complete) | 3 |
| Ví chưa đủ dữ liệu (partial, dừng ở 3.000 chữ ký mới nhất) | 19 |
| Ví cần loại | **1** (`9yYya3F5…`: 0 giao dịch pump.fun trong cửa sổ) |
| Độ tin cậy dữ liệu | Cao 2 · Trung bình 7 · Thấp 13 · Loại 1 |
| Kết luận | **ĐƯỢC PHÉP chạy batch tiếp theo**, với hai giới hạn: (1) batch chỉ để **thu thập dữ liệu**; (2) chưa dùng PnL/Score của các ví Trung bình/Thấp để kết luận Smart Money cho đến khi sửa 2 lỗi tính PnL ở mục 5 (cần anh duyệt riêng). Dữ liệu gốc đã lưu nên khi sửa xong sẽ giải mã lại tại chỗ, **không tốn thêm credit Helius** |

Ba phát hiện chính:

1. **12 vị thế còn token:**
   - 6 vị thế là **lỗi của công cụ kiểm tra**: vị thế đúng, đã sửa công cụ.
   - 5 vị thế **thiếu lịch sử**: ví đã có token từ trước cửa sổ.
   - 1 vị thế không tái lập được.

   Công cụ đã sửa còn tìm thêm 10 vị thế cùng loại "thiếu lịch sử", tổng cộng **15 vị thế**, làm PnL bị đội **+119,8 SOL**; riêng ví `8BmPoCUx` đội +97,3 SOL.
2. **PnL của vị thế đã verified chính xác:** 99,79% lệch ≤ 0,001 SOL ✅. **PnL của vị thế chưa verified KHÔNG đáng tin:** chỉ 15/341 vị thế khớp on-chain trong 0,01 SOL, có vị thế lệch tới 92,5 SOL ✅.
3. **Lỗi backfill lặp 6 lần đã sửa và kiểm chứng** ✅. Chi phí đúng khoảng **280 credit/ví**.

---

## 2. ĐIỀU TRA 12 VỊ THẾ CÒN TOKEN ✅ (11/12 xác minh, 1 chưa xác định)

| # | Wallet | Token mint | Token đã mua | Token còn lại on-chain | Chuyển token vào/ra liên quan | Nguyên nhân (bằng chứng) | Kết luận |
|---|---|---|---|---|---|---|---|
| 1 | `5PZxgSQnbbadoPokb9nKesjGn3cP76LrMWEqnMEqNo5p` | `AAsNWUcNMKVaMG49ECRqVFvxS7kiameVCPQQpjcEfzWo` | 13.161.603 | 561.153 | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Ví đã giữ 858.914 token TRƯỚC lần mua đầu trong cửa sổ lịch sử; vị thế bán 13.459.364 > mua 13.161.603 (bán 297.761 token có từ trước). PnL tính cả 0.1177 SOL tiền bán token không có giá vốn | Thiếu lịch sử |
| 2 | `B9K2wTQcRDLRLhMKFyRh2hPqHrr6VKiCC9yNGpkMUXrh` | `42t61wPi41TwWtpqBVgZN1V4hjiKwZBKwq27xME9pump` | 10.166.994 | 14.016.295 | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Ví đã giữ 15.528.044 token TRƯỚC lần mua đầu trong cửa sổ lịch sử; vị thế bán 11.678.742 > mua 10.166.994 (bán 1.511.748 token có từ trước). PnL tính cả 3.1201 SOL tiền bán token không có giá vốn | Thiếu lịch sử |
| 3 | `CXnf4Tt7qFz3KZNwn3Yve5MKaRyxGoAy2eDX3QT8e99m` | `6mXFCoZ515w37YfmwXGJMPfQfiK5tr9WkihW3oN9pump` | 41.949 | 5.352.722 | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Ví đã giữ 5.858.172 token TRƯỚC lần mua đầu trong cửa sổ lịch sử; vị thế bán 547.399 > mua 41.949 (bán 505.450 token có từ trước). PnL tính cả 0.9419 SOL tiền bán token không có giá vốn | Thiếu lịch sử |
| 4 | `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o` | `3umHFkiMHyphCkLxtbf8Aabie8r5hxn86QeBAovTpump` | 3.744.798 | 37.773.870 | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Ví đã giữ 38.226.170 token TRƯỚC lần mua đầu trong cửa sổ lịch sử; vị thế bán 4.197.097 > mua 3.744.798 (bán 452.299 token có từ trước). PnL tính cả 1.2163 SOL tiền bán token không có giá vốn | Thiếu lịch sử |
| 5 | `DNfuF1L62WWyW3pNakVkyGGFzVVhj4Yr52jSmdTyeBHm` | `Tqj8yFmagrg7oorpQkVGYR52r96RFTamvWfth9bpump` | 4.417.248 | 4.789.221 | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Ví đã giữ 5.161.194 token TRƯỚC lần mua đầu trong cửa sổ lịch sử; vị thế bán 4.789.221 > mua 4.417.248 (bán 371.973 token có từ trước). PnL tính cả 2.8545 SOL tiền bán token không có giá vốn | Thiếu lịch sử |
| 6 | `ArkdCAPErAo6KdVDTFA32YKnqaD7jfXsESFoyxQzDgnu` | `G68A8gnXCwQ2S24X1ukk6gVEtQm5C1F5JZmD1xNfpump` | 151.660.777 | 0 (tổng biến động on-chain của vị thế = 0) | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Mua 151.660.777 = bán 151.660.777; mỗi lệnh bán khớp chính xác biến động token on-chain. Có 2 slot chứa ≥ 2 giao dịch của vị thế, công cụ kiểm tra cũ chỉ xếp theo slot nên đọc số dư của giao dịch không phải cuối cùng (1.516.608) | Lỗi hệ thống (công cụ kiểm tra; vị thế đúng; đã sửa) |
| 7 | `ArkdCAPErAo6KdVDTFA32YKnqaD7jfXsESFoyxQzDgnu` | `H8ER1F1ojTdCq2XC9yng85Qs8Mk4XPRTLEV86yJ2pump` | 96.449.438 | 0 (tổng biến động on-chain của vị thế = 0) | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Mua 96.449.438 = bán 96.449.438; mỗi lệnh bán khớp chính xác biến động token on-chain. Có 1 slot chứa ≥ 2 giao dịch của vị thế, công cụ kiểm tra cũ chỉ xếp theo slot nên đọc số dư của giao dịch không phải cuối cùng (29.008.422) | Lỗi hệ thống (công cụ kiểm tra; vị thế đúng; đã sửa) |
| 8 | `ArkdCAPErAo6KdVDTFA32YKnqaD7jfXsESFoyxQzDgnu` | `Hkm9BYnyFCQWpZxY4cvBMK2WoW461EkPQvhgjnFjpump` | 96.449.438 | 0 (tổng biến động on-chain của vị thế = 0) | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Mua 96.449.438 = bán 96.449.438; mỗi lệnh bán khớp chính xác biến động token on-chain. Có 1 slot chứa ≥ 2 giao dịch của vị thế, công cụ kiểm tra cũ chỉ xếp theo slot nên đọc số dư của giao dịch không phải cuối cùng (64.621.124) | Lỗi hệ thống (công cụ kiểm tra; vị thế đúng; đã sửa) |
| 9 | `ArkdCAPErAo6KdVDTFA32YKnqaD7jfXsESFoyxQzDgnu` | `Ppo8b9HsNtN9fE9RXrz8gEs6BdCRj7K7YW21rUWpump` | 97.545.454 | 0 (tổng biến động on-chain của vị thế = 0) | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Mua 97.545.454 = bán 97.545.454; mỗi lệnh bán khớp chính xác biến động token on-chain. Có 1 slot chứa ≥ 2 giao dịch của vị thế, công cụ kiểm tra cũ chỉ xếp theo slot nên đọc số dư của giao dịch không phải cuối cùng (33.165.454) | Lỗi hệ thống (công cụ kiểm tra; vị thế đúng; đã sửa) |
| 10 | `ArkdCAPErAo6KdVDTFA32YKnqaD7jfXsESFoyxQzDgnu` | `nCpJ4UxPsphcvu4kxesJC3YZC3CG5bdaVqm49YNpump` | 96.449.438 | 0 (tổng biến động on-chain của vị thế = 0) | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Mua 96.449.438 = bán 96.449.438; mỗi lệnh bán khớp chính xác biến động token on-chain. Có 1 slot chứa ≥ 2 giao dịch của vị thế, công cụ kiểm tra cũ chỉ xếp theo slot nên đọc số dư của giao dịch không phải cuối cùng (29.008.422) | Lỗi hệ thống (công cụ kiểm tra; vị thế đúng; đã sửa) |
| 11 | `ArkdCAPErAo6KdVDTFA32YKnqaD7jfXsESFoyxQzDgnu` | `pGukrxxcRKCU8QLWdheMeDEarevsZWTBHyCfjAFpump` | 96.449.438 | 0 (tổng biến động on-chain của vị thế = 0) | Không có (tokens_in/out = 0; không có giao dịch đã lưu nào làm đổi số dư mint này mà thiếu dòng giải mã) | Mua 96.449.438 = bán 96.449.438; mỗi lệnh bán khớp chính xác biến động token on-chain. Có 1 slot chứa ≥ 2 giao dịch của vị thế, công cụ kiểm tra cũ chỉ xếp theo slot nên đọc số dư của giao dịch không phải cuối cùng (14.720.692) | Lỗi hệ thống (công cụ kiểm tra; vị thế đúng; đã sửa) |
| 12 | — | — | — | — | — | Có trong lần kiểm tra 04:41; sau khi analytics tính lại vị thế (dữ liệu đồng bộ mới), lần chạy lại chỉ còn 11 — không tái lập được để kiểm tra | Chưa xác định |

**Kết luận mục 2:**
- **6 vị thế = Lỗi hệ thống của công cụ kiểm tra**, không phải lỗi dữ liệu. Đã sửa `verify_pnl`: cộng tổng biến động token on-chain của vị thế, không phụ thuộc thứ tự, và cộng mọi tài khoản token của ví.
- **5 vị thế = Thiếu lịch sử:** ví đã có token trước cửa sổ backfill.
- **1 vị thế = Chưa xác định:** không tái lập được sau khi analytics tính lại vị thế.
- **Phát hiện thêm bằng công cụ đã sửa:** 15 vị thế bán token có từ trước cửa sổ, chi tiết trong `kiem-chung-sau-batch-1/prewindow-positions.out`. PnL bị đội tổng cộng 119,8 SOL:

  | Ví | PnL bị đội | Ghi chú |
  |---|---|---|
  | 8BmPoCUx | +97,3 SOL | lịch sử *complete* nhưng token mua từ trước 60 ngày |
  | CyaE1Vxv | +10,5 SOL | |
  | ETU3GyrU | +4,9 SOL | |
  | B9K2wTQc | +3,1 SOL | |
  | DNfuF1L6 | +2,9 SOL | |
  | CXnf4Tt7 | +0,9 SOL | |
  | 5PZxgSQn | +0,1 SOL | |

  Đây là hạn chế của cách tính PnL hiện tại: tiền bán token không có giá vốn trong cửa sổ vẫn bị tính là lãi. **Chưa sửa** vì là thay đổi logic PnL, cần anh duyệt.

---

## 3. PHÂN TÍCH 23 VÍ VÀ TỶ LỆ VERIFIED ✅

| # | Wallet | Backfill | Chữ ký đã lấy | Verified | Nguyên nhân verified thấp | Mức đầy đủ lịch sử | Độ tin cậy | Đề xuất |
|---|---|---|---|---|---|---|---|---|
| 1 | `6ntUoFQSGThAfytrQ58yhenXjpGNvATeqVBCN9udBosW` | complete | 846 | 100.0% | — | Đủ 60 ngày | Cao | Giữ |
| 2 | `78FmcvKtbWEMTeZ8Z5jc1DjfskQ8oJNf6XgAyaf6tSPE` | complete | 1470 | 100.0% | — | Đủ 60 ngày | Cao | Giữ |
| 3 | `5PZxgSQnbbadoPokb9nKesjGn3cP76LrMWEqnMEqNo5p` | partial | 3000 | 100.0% | — | Một phần (dừng ở 3.000 chữ ký mới nhất); PnL đội +0,1 SOL | Trung bình | Theo dõi |
| 4 | `5sr55MD6EZCMKftkdzRBjbvVuCjBcDarv4sShn1DVmPY` | partial | 3000 | 100.0% | — | Một phần (dừng ở 3.000 chữ ký mới nhất) | Trung bình | Theo dõi |
| 5 | `8BmPoCUxDUJq7yvc9878RFSukkswSuvpvrrE1ZTgyaB7` | complete | 1771 | 100.0% | — | Đủ 60 ngày; PnL đội +97,3 SOL do bán token có trước cửa sổ | Trung bình | Theo dõi |
| 6 | `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o` | partial | 3000 | 100.0% | — | Một phần (dừng ở 3.000 chữ ký mới nhất); PnL đội +10,5 SOL (6 vị thế) | Trung bình | Theo dõi |
| 7 | `2Giyf5L9mrfw22GpLvPztxMBiZE5x6BMUcxxMSUbo7af` | partial | 3000 | 100.0% | —; PnL vị thế chưa verified: hệ thống +0.0 SOL vs on-chain +0.0 SOL (1/1 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất) | Trung bình | Theo dõi |
| 8 | `92aios2R2XXhPkeBqEmwDB8EQ8sZFLBPBpt1X3N1xniD` | partial | 3000 | 99.5% | —; PnL vị thế chưa verified: hệ thống +0.3 SOL vs on-chain +9.8 SOL (0/3 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất) | Trung bình | Theo dõi |
| 9 | `ArkdCAPErAo6KdVDTFA32YKnqaD7jfXsESFoyxQzDgnu` | partial | 3000 | 77.1% | 179/786 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); PnL vị thế chưa verified: hệ thống -0.1 SOL vs on-chain -0.4 SOL (0/1 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất) | Trung bình | Theo dõi |
| 10 | `DjM7Tu7whh6P3pGVBfDzwXAx2zaw51GJWrJE3PwtuN7s` | partial | 3000 | 97.0% | 1 lệnh có trả/nhận USDC trong cùng giao dịch | Một phần (dừng ở 3.000 chữ ký mới nhất) | Thấp | Theo dõi |
| 11 | `sAdNbe1cKNMDqDsa4npB3TfL62T14uAo2MsUQfLvzLT` | partial | 3000 | 93.8% | 1 lệnh có trả/nhận USDC trong cùng giao dịch | Một phần (dừng ở 3.000 chữ ký mới nhất) | Thấp | Theo dõi |
| 12 | `DNfuF1L62WWyW3pNakVkyGGFzVVhj4Yr52jSmdTyeBHm` | partial | 3000 | 93.3% | 1/15 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế) | Một phần (dừng ở 3.000 chữ ký mới nhất); PnL đội +2,9 SOL | Thấp | Theo dõi |
| 13 | `9FNz4MjPUmnJqTf6yEDbL1D4SsHVh7uA8zRHhR5K138r` | partial | 3000 | 83.4% | 51/469 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); PnL vị thế chưa verified: hệ thống -13.2 SOL vs on-chain -8.5 SOL (0/11 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất) | Thấp | Theo dõi |
| 14 | `CATk62cYqDFXTh3rsRbS1ibCyzBeovc2KXpXEaxEg3nB` | partial | 3000 | 80.7% | 109/667 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); 6 lệnh lệch SOL vài lamport; PnL vị thế chưa verified: hệ thống -21.4 SOL vs on-chain -4.0 SOL (2/30 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất) | Thấp | Theo dõi |
| 15 | `ETU3GyrUsv6UztQJxHgsBX2UoJFmq79WJe3JyDpAqGMz` | partial | 3000 | 76.0% | 403/1712 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); 5 lệnh lệch SOL vài lamport; PnL vị thế chưa verified: hệ thống -105.9 SOL vs on-chain -31.3 SOL (6/152 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất); PnL đội +4,9 SOL | Thấp | Theo dõi |
| 16 | `AVAZvHLR2PcWpDf8BXY4rVxNHYRBytycHkcB5z5QNXYm` | partial | 3000 | 70.2% | 3/67 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); 6 lệnh lệch SOL vài lamport; PnL vị thế chưa verified: hệ thống -5.5 SOL vs on-chain -42.9 SOL (0/6 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất) | Thấp | Theo dõi |
| 17 | `GJA1HEbxGnqBhBifH9uQauzXSB53to5rhDrzmKxhSU65` | partial | 3000 | 66.9% | 14/127 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); 14 lệnh có trả/nhận USDC trong cùng giao dịch; PnL vị thế chưa verified: hệ thống -2.5 SOL vs on-chain -0.8 SOL (0/4 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất) | Thấp | Theo dõi |
| 18 | `2X4H5Y9C4Fy6Pf3wpq8Q4gMvLcWvfrrwDv2bdR8AAwQv` | partial | 3000 | 64.8% | 30/196 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); 30 lệnh có trả/nhận USDC trong cùng giao dịch; PnL vị thế chưa verified: hệ thống -120.2 SOL vs on-chain -2.8 SOL (0/10 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất) | Thấp | Theo dõi |
| 19 | `B9K2wTQcRDLRLhMKFyRh2hPqHrr6VKiCC9yNGpkMUXrh` | partial | 3000 | 60.8% | 288/737 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); PnL vị thế chưa verified: hệ thống -72.4 SOL vs on-chain -32.1 SOL (0/50 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất); PnL đội +3,1 SOL | Thấp | Theo dõi |
| 20 | `G6fUXjMKPJzCY1rveAE6Qm7wy5U3vZgKDJmN1VPAdiZC` | partial | 3000 | 56.9% | 251/693 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); PnL vị thế chưa verified: hệ thống -67.4 SOL vs on-chain -0.3 SOL (2/65 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất) | Thấp | Theo dõi |
| 21 | `CXnf4Tt7qFz3KZNwn3Yve5MKaRyxGoAy2eDX3QT8e99m` | partial | 3000 | 48.2% | 14/114 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); 42 lệnh có trả/nhận USDC trong cùng giao dịch; PnL vị thế chưa verified: hệ thống -3.1 SOL vs on-chain -0.3 SOL (4/5 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất); PnL đội +0,9 SOL | Thấp | Theo dõi |
| 22 | `DuQabFqdC9eeBULVa7TTdZYxe8vK8ct5DZr4Xcf7docy` | partial | 3000 | 38.1% | 13/21 lệnh trên curve định giá USDC qua router (pump_curve_nonsol, luôn 'một phần' theo thiết kế); PnL vị thế chưa verified: hệ thống -2.5 SOL vs on-chain -2.0 SOL (0/3 khớp) | Một phần (dừng ở 3.000 chữ ký mới nhất) | Thấp | Theo dõi |
| 23 | `9yYya3F5EJoLnBNKW6z4bZvyQytMXzDcpU5D6yYr4jqL` | partial | 3000 | — | 0 giao dịch pump.fun trong cửa sổ | Một phần (dừng ở 3.000 chữ ký mới nhất) | — | Loại |

Tiêu chí đánh giá độ tin cậy:
- **Cao:** đủ lịch sử 60 ngày, verified 100%, không có vị thế bán token có từ trước cửa sổ.
- **Trung bình:** verified ≥ 99%, hoặc phần lớn vị thế đã đóng là verified; nhưng lịch sử bị cắt ở 3.000 chữ ký, hoặc PnL bị đội.
- **Thấp:** verified < 95%, hoặc quá ít vị thế để đánh giá.

**Giải thích nhóm verified dưới 90%**
(DuQabFqd 38,1%, CXnf4Tt7 48,3%, G6fUXjMK 56,9%, B9K2wTQc 60,8%, 2X4H5Y9C 64,8%, GJA1HEbx 66,9%, AVAZvHLR 70,2%, ETU3GyrU 76,0%, ArkdCAPE 77,1%, CATk62cY 80,7%, 9FNz4MjP 83,4%)

- **Nguyên nhân chính:** lệnh mua/bán token trên curve **định giá bằng USDC**, đi qua router (`pump_curve_nonsol`). Bộ giải mã lấy giá trị SOL từ biến động SOL của ví và gắn nhãn "một phần" theo thiết kế, nên các lệnh này không bao giờ thành "verified".

  | Ví | Lệnh `pump_curve_nonsol` / tổng lệnh |
  |---|---|
  | DuQabFqd | 13/21 |
  | G6fUXjMK | 251/693 |
  | B9K2wTQc | 288/737 |
  | ETU3GyrU | 403/1.712 |
  | ArkdCAPE | 179/786 |

- **Nguyên nhân phụ:**
  - lệnh có trả/nhận USDC trong cùng giao dịch: CXnf4Tt7 42 lệnh;
  - số token giải mã lệch với biến động token on-chain (token delta mismatch): 2X4H5Y9C, GJA1HEbx;
  - lệch SOL vài lamport.
- **Ảnh hưởng đến PnL (đo bằng biến động SOL on-chain của chính ví):** PnL hệ thống của các vị thế chưa verified **sai lệch lớn**. Chỉ 15/341 vị thế khớp ≤ 0,01 SOL:

  | Ví | PnL hệ thống | PnL on-chain |
  |---|---|---|
  | G6fUXjMK | −67,4 SOL | −0,3 SOL |
  | ETU3GyrU | −105,9 SOL | −31,3 SOL |
  | 2X4H5Y9C | −120,2 SOL | −2,8 SOL |
  | AVAZvHLR | −5,5 SOL | −42,9 SOL |

  ⇒ Tổng PnL, Win Rate và Score của các ví này **không đáng tin**.
- **Ngoại lệ ArkdCAPE:** 164/166 vị thế đã đóng là verified; PnL chưa verified chỉ −6,4 SOL. Vì vậy ví này được xếp Trung bình.

---

## 4. KIỂM TRA LỖI VÀ CHI PHÍ HELIUS

| Hạng mục | Kết quả | Trạng thái |
|---|---|---|
| Nguyên nhân lỗi lặp | Hàng đợi cũ chứa nhiều việc backfill trùng cho cùng ví; worker backfill lại dù ví đã xong | ✅ |
| Số lần backfill trước khi sửa (03:33–04:41) | 137 lần backfill gTFA cho 23 ví, khoảng **6 lần/ví**. Số liệu ghi lúc 04:41 từ log container cũ; log này mất khi tạo lại container | ✅ (số liệu đã ghi, log gốc không còn) |
| Sau khi sửa (từ 04:41:35) | 24 lần xử lý, **tất cả `mode: sync`** (chỉ đồng bộ phần mới), **0 lần backfill lại**; 18 ví của batch đã đồng bộ tăng dần | ✅ |
| Credit sync trước khi sửa | 40.690 (đúng ra khoảng 6.428 cho 1 lượt; khoảng 34.000 bị lãng phí) | ✅ (bộ đếm nội bộ) |
| Credit sync sau khi sửa | +206 trong 12 phút (đồng bộ tăng dần) | ✅ |
| Chi phí đúng 1 lượt backfill | 6.428 credit / 23 ví, khoảng **280 credit/ví** (300 với ví chạm 3.000 chữ ký) | ✅ (theo bảng giá) |
| Tổng bộ đếm hôm nay (từ 03:31) | 43.815 (sync 40.896, discovery 2.432, verifier 487) | ✅ |
| Credit thực tế trên dashboard Helius | Chưa có số liệu từ anh | ⚠️ chưa xác minh |
| Trần 150.000 credit/ngày | Bộ đếm cộng đúng: 137 lượt × khoảng 297 ≈ 40.690. Logic dừng khi chạm trần có test. **Chưa kích hoạt thực tế** vì bộ đếm mới ở 43.815 | ⚠️ chưa chạm trần thực tế |
| Lỗi phụ đã sửa | `backfill_sigs_seen` bị ghi đè khi đồng bộ tăng dần (ví dụ 3.000 thành 2). Đã sửa; con số đúng vẫn còn trong `backfill_check`. **Không chạy `backfill-audit`** cho các ví này trước khi dữ liệu trường này được khôi phục, vì công cụ sẽ coi là thiếu và backfill lại | ✅ |

Test đơn vị: **95 passed**.

---

## 5. KẾT LUẬN VÀ HÀNH ĐỘNG TIẾP THEO

| Hạng mục | Kết quả | Hành động |
|---|---|---|
| Chất lượng dữ liệu | 0 giao dịch thiếu, 0 dòng trùng; 64.087 chữ ký, 34.128 giao dịch lấy được ✅ | Giữ nguyên cách thu thập |
| Độ chính xác PnL | Vị thế verified: 99,79% lệch ≤ 0,001 SOL ✅. Vị thế chưa verified: sai lệch lớn ✅ | Đề xuất sửa định giá `pump_curve_nonsol` / USDC (cần duyệt). Trước khi sửa: không dùng PnL chưa verified để kết luận |
| Verified | 91,1% chung; 11 ví dưới 90% do lệnh curve USDC qua router | Chỉ ví verified ≥ 95% được đánh giá Smart Money |
| 12 vị thế còn token | 6 lỗi công cụ (đã sửa); 5 thiếu lịch sử; 1 chưa xác định. Thêm 10 vị thế thiếu lịch sử, PnL đội 119,8 SOL | Đề xuất: vị thế bán token có trước cửa sổ không tính vào PnL/Win Rate (cần duyệt) |
| Backfill lặp | Đã sửa, kiểm chứng: 0 lần backfill lại sau 04:41 ✅ | Không cần thêm |
| Credit | Khoảng 280 credit/ví; 50 ví khoảng 14.000 credit, dưới trần 150.000 | Anh đối chiếu dashboard Helius |
| Điều kiện chạy batch tiếp | Thu thập dữ liệu đúng và rẻ; phân tích PnL còn 2 lỗi nhưng sửa sau được, không tốn credit | Chạy batch 2 để thu thập dữ liệu; chưa công bố Smart Money cho ví Trung bình/Thấp |

**Đề xuất kèm theo:** ví bị loại vì tần suất cao không tính vào hạn mức 50 ví/ngày (hôm nay mất 27/50 suất). Đây là thay đổi nhỏ trong scheduler, cần anh duyệt.

**KẾT LUẬN: ĐƯỢC PHÉP CHẠY BATCH TIẾP THEO**

---

### Phụ lục – Bằng chứng và truy vấn (thư mục `docs/handover/kiem-chung-sau-batch-1/`)

| File | Nội dung |
|---|---|
| `inv23.out` | Kết quả `inv23.py`: các vị thế bị đánh dấu (kiểm tra không phụ thuộc thứ tự) và phân tích từng ví (verified, nguyên nhân, venue, PnL chưa verified, điểm) |
| `unverified-pnl.out` | Kết quả `unv.py`: PnL vị thế chưa verified so với biến động SOL on-chain |
| `prewindow-positions.out` | Kết quả `over.py`: 15 vị thế bán token có trước cửa sổ và PnL bị đội |
| `verify-pnl-after-fix.json` | `python -m radar verify-pnl --since 2026-11-23T03:33:31Z` sau khi sửa công cụ |
| `B1-batch50.txt` | Log batch 1 và bộ đếm credit theo thời gian |
| `ark.py`, `inv23.py`, `unv.py`, `over.py` | Script truy vấn (chỉ đọc) |
