# BÁO CÁO BATCH 2 – THU THẬP DỮ LIỆU (23/11/2026, 05:06 → 05:12 UTC)

**Phạm vi:** chỉ thu thập dữ liệu. Không đổi logic, scoring, schema hay scheduler; `trading_enabled = false`. Thay đổi duy nhất là cài đặt `backfill_wallets_per_day` từ 50 lên 100, để nhận thêm 50 ví theo thứ tự ưu tiên hiện có.

## Kết quả 50 ví mới

| Chỉ tiêu | Số ví |
|---|---|
| Được nhận vào batch | 50 |
| **Đã backfill (gTFA)** | **33** |
| – lịch sử đầy đủ 60 ngày (complete) | **7** |
| – một phần (dừng ở 3.000 chữ ký) | 26 |
| **Bị loại do bot** (≥ 1.000 giao dịch/ngày, quy tắc cũ) | **17** |
| Lỗi | 0 |

Ghi chú: 4 ví đã backfill vẫn còn cờ `high_frequency` từ một lần đánh giá trước, nhưng lần kiểm tra này chúng không vượt ngưỡng.

## Chất lượng dữ liệu (33 ví)

| Chỉ tiêu | Giá trị |
|---|---|
| Chữ ký trong 60 ngày | 85.392 |
| Giao dịch lấy được | 74.631 |
| **Giao dịch thiếu** | **0** |
| Giao dịch thất bại on-chain | 10.761 |
| Lệnh mua/bán | 61.013 |
| Verified | 57.886 (**94,9%**) |

Các ví có verified dưới 90%:

| Ví | Verified |
|---|---|
| G2oZrd1k | 0,65% |
| 7Kt8MvUa | 27% |
| EtZWfmdy | 46,9% |
| 2bRoascf | 47,0% |
| 4oN6EAEw | 56,6% |
| 8274EUKE | 78,4% |
| 56A6Mekf | 83,0% |

## Kiểm chứng PnL (vị thế verified, chỉ để tham khảo)

- 3.258 vị thế đã đóng: 99,26% lệch ≤ 0,001 SOL, 100% lệch ≤ 0,01 SOL, lệch lớn nhất 0,0055 SOL.
- Win Rate/PnL tính lại khớp 100%.
- 24 vị thế không có tổng token on-chain bằng 0. Đây cùng loại "bán token có trước cửa sổ lịch sử" như batch 1.

## Không backfill lặp

- 33 ví, mỗi ví backfill **đúng 1 lần**.
- **0** ví của batch 1 bị backfill lại.
- 0 dòng lỗi trong log.

## Chi phí

| Nguồn | Credit |
|---|---|
| Bộ đếm nội bộ trong batch 2 | **+8.678** (sync 8.632) |
| Tổng ước tính của từng ví | 8.613 (khoảng **261 credit/ví đã backfill**; 301 với ví chạm 3.000 chữ ký) |
| Kiểm tra bot (17 ví) | khoảng 17 |
| Tổng bộ đếm hôm nay | 53.729 / trần 150.000 |
| Dashboard Helius | **chưa xác minh** (chờ số liệu từ người dùng) |

## Ghi nhận cho mục tiêu tìm Smart Money thật

Sau 2 batch có **7 ví dữ liệu tin cậy**: lịch sử đầy đủ và verified 100%.

| Ví | Score | Win Rate | PnL 30 ngày | Phân loại hành vi |
|---|---|---|---|---|
| 8BmPoCUx | 44,6 | 60% | 269,9 SOL (đội khoảng 97 SOL, xem báo cáo trước) | LIKELY_BOT |
| 6ntUoFQS | 36,0 | 73% | 89,3 SOL | BOT |
| 78FmcvKt | 32,5 | 68% | 178,3 SOL | BOT |
| 8whR2Fm1 | 31,8 | 96% | 56,2 SOL | BOT |
| Gcn3NuPF | 30,1 | 87% | 42,6 SOL | BOT |
| VwjohQMj | 24,7 | 76% | 27,4 SOL | BOT |
| 6GQWbMEC | 16,6 | 57% | 15,6 SOL | BOT |

**Cả 7 ví đều bị xếp là BOT hoặc LIKELY_BOT. Chưa tìm thấy ví người thật đạt chuẩn.**

Nguyên nhân: thứ tự ưu tiên hiện tại xếp trước các ví có nhiều vị thế đã đóng, nên chủ yếu chọn trúng ví giao dịch tần suất cao. Thêm vào đó, 26/33 ví chạm trần 3.000 chữ ký, với khoảng 2.700 lệnh trong 3.000 chữ ký.

**Đề xuất (cần duyệt):** đổi thứ tự ưu tiên của scheduler sang ví có hành vi giống người và tần suất thấp. Ví dụ: ưu tiên ví mua ≥ 2 SOL/lệnh với ít hơn 50 lệnh/ngày, và hạ ưu tiên ví mà stream thấy có quá nhiều lệnh.
