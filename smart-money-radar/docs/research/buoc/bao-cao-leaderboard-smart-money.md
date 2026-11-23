# BÁO CÁO DANH SÁCH VÍ SMART MONEY TỪ LEADERBOARD CÔNG KHAI

**Ngày:** 23/11/2026 (giờ UTC).

**Nguồn:**
- Solana Tracker: các bảng PnL, Axiom, Photon, GMGN, pump.fun.
- dEdge.
- KOL Explorer.

**Kiểm chứng on-chain:** RPC công khai miễn phí của Solana.

**Helius credit dùng: 0.**

> **Quy ước:** cột/số liệu ghi **[LB]** là số liệu lấy nguyên từ leaderboard. Cột ghi **[OC]** là số liệu tôi tự tính từ giao dịch trên chain. Ô "–" là nguồn không cung cấp, không tự điền.

---

## 1. KẾT QUẢ TÓM TẮT

| Bước | Số ví |
|---|---|
| Thu thập: Solana Tracker 125 dòng (5 bảng 30D), dEdge 90 ví, KOL Explorer 151 KOL | **335 ví khác nhau** |
| Xuất hiện ở ≥ 2 nguồn | 7 (đều bị loại, xem mục 3) |
| Qua bộ lọc theo số liệu leaderboard | **42** |
| Kiểm chứng on-chain (5.040 giao dịch, 827 vị thế đã đóng dựng lại từ số dư) | 42 |
| **Đủ bằng chứng để theo dõi thử** | **4** |
| **Tiềm năng, cần thêm dữ liệu** | **10** |
| Không phù hợp | 28 trong nhóm 42 + 293 bị loại ở vòng lọc |

**Chọn 14 ví, không cố đủ 20.**
- Chưa ví nào đủ điều kiện **copy trade thật**.
- 4 ví nhóm 1 đủ điều kiện **theo dõi thử trên giấy (paper)**: lãi sau phí, win rate cao, lợi nhuận không phụ thuộc một lệnh, giữ lệnh đủ lâu để chạy theo.

---

## 2. DANH SÁCH XẾP HẠNG THEO KHẢ NĂNG COPY

**Cách xếp hạng:** ưu tiên theo thứ tự:
1. giữ lệnh đủ lâu để copy với độ trễ 1–3 giây (thời gian giữ trung vị, tỷ lệ lệnh giữ < 60 giây);
2. PnL on-chain dương sau phí, đủ số vị thế;
3. win rate;
4. lợi nhuận phân bổ đều (lệnh lớn nhất ≤ 45% tổng lãi);
5. tần suất giao dịch vừa phải;
6. không phối hợp nhiều ví.

**Không** xếp theo tổng PnL.

### 2.1 Nhóm 1 – Đủ bằng chứng để theo dõi thử

| # | Ví (đầy đủ) | Nguồn | [LB] dEdge 15 ngày: net SOL / WR / lệnh đóng / token | [OC] vị thế đóng | [OC] WR | [OC] PnL sau phí (SOL) | [OC] lệnh lớn nhất / tổng lãi | [OC] giữ lệnh trung vị | [OC] lệnh giữ < 60 giây | [OC] swap/ngày | [OC] vốn/lệnh |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `BvtZeURZMcvvQVBr5k9jDT53E2xuFq2Da6d6RTDP7gM4` | dEdge | +35 / 61,2% / 186 / 187 | 26 | 69,2% | **+5,96** | 26% | **281 giây** | 19% | 39 | 1,01 SOL |
| 2 | `7Q8rvHKi1brA49epwVyinrqX6rMKZxZqW1FoUgpXTpTs` | dEdge | +37 / 58,0% / 355 / 363 | 42 | 59,5% | **+5,52** | 27% | 114 giây | 33% | 136 (cao) | 1,55 SOL |
| 3 | `ArF3BG43u4xakKDGQmyzcaLuxzZcY1L6SC8K6Miy4maz` | dEdge | +26 / 63,2% / 524 / 524 | 49 | 79,6% | **+2,66** | 44% | 60 giây | 33% | 92 | 0,30 SOL |
| 4 | `3kxcF8wHKm4sEtnxjXXeUvNGeDjTmo47LgtSRg71YfG5` | dEdge | +54 / 67,7% / 131 / 149 | 28 | 82,1% | **+10,31** | 21% | 51 giây | 64% | 14 | 1,00 SOL |

**Số liệu 7D/30D:** dEdge chỉ công bố cửa sổ **15 ngày**. 4 ví này **không có** trong bảng 7D/30D của Solana Tracker hay KOL Explorer, nên **thiếu** số liệu 7D/30D.

**Ghi chú từng ví:**
- **#1 `BvtZ…`:** dễ copy nhất.
  - Giữ lệnh trung vị gần 5 phút, chỉ 19% lệnh giữ dưới 1 phút.
  - 39 swap/ngày, không dùng tip Jito.
  - 35% giao dịch trong khung là của người khác gửi đến (spam), đã loại.
- **#2 `7Q8r…`:** lãi đều (lệnh lớn nhất 27%), nhưng 136 swap/ngày, tức là tần suất cao, khó theo đủ lệnh.
- **#3 `ArF3…`:** win rate cao, nhưng mỗi lệnh chỉ khoảng 0,3 SOL. Lãi tuyệt đối nhỏ, lệnh lớn nhất chiếm 44%.
- **#4 `3kxc…`:** lãi on-chain tốt nhất nhóm và ít giao dịch, nhưng 64% lệnh giữ dưới 60 giây.
  - Copy trễ 1–3 giây sẽ mất phần lớn lợi nhuận.
  - Ví `HzKH3s8K…` đi sau ví này ở 10/12 token, cách khoảng 2 giây: một bot đang copy `3kxc`, có thể là chính chủ hoặc người khác. Đây là bằng chứng ví được người khác theo, không phải giao dịch phối hợp thao túng.

### 2.2 Nhóm 2 – Tiềm năng, cần thêm dữ liệu

| # | Ví (đầy đủ) | Nguồn | [LB] số liệu nguồn | [OC] vị thế / WR / PnL (SOL) | [OC] giữ trung vị / < 60 giây | Lý do chưa đủ |
|---|---|---|---|---|---|---|
| 5 | `B8bvkQ7UqykUXUkVSdxngFtFBnonHf4sJTEX7s3gwwmS` | dEdge | 15D: +38 SOL, 58,2%, 189 lệnh | 13 / 76,9% / +14,40 | **2,5 giờ** / 0% | Kiểu giữ lệnh dễ copy nhất, nhưng mới 13 vị thế đóng (< 20). 37 giao dịch bán lỗi do ví ký (dùng terminal `term9YPb`), 16 swap có tip Jito |
| 6 | `HUcYnQCHCojCmWTEHWtNZkMUYoY1yekWJ7HU1Xu6rAdY` | dEdge | 15D: +95, 65,1%, 303 | 38 / 52,6% / +3,02 | 108 giây / 47% | WR on-chain chỉ 52,6%. Phí 0,31 SOL ăn khoảng 10% lãi |
| 7 | `8P2vQR4MNhDp1fzKyV9zw6QSAUkHaAsSZdYxkwBmr5nG` | dEdge | 15D: +94, 82,1%, 183 | 34 / 64,7% / +3,82 | 43 giây / 62% | **Cụm A:** mua cùng slot với `E69vrqyf…` và `5HM96vkn…` ở 27–28/28 token (1 người chia lệnh). Cả cụm: +3,82 / +1,04 / −0,81 SOL |
| 8 | `Gmjvdkz2C5oMnYVEQFefnnFavEy9Ae7TrLyR5nq3LXyK` | dEdge | 15D: +63, 64,7%, 166 | 36 / 69,4% / +9,49 | 43 giây / 64% | **Cụm B:** cùng slot với `6AZAXbi3…` ở 30/30 token. Lệnh lớn nhất chiếm 62% lãi |
| 9 | `DCgZR7U5ti6Y3UB2supWyy6msfmSNmT7pZsE9tkkJ6F8` | dEdge | 15D: +82, 56,3%, 781 | 29 / 75,9% / +11,95 | **26 giây** / 69% | Lãi tốt nhưng giữ quá ngắn, 108 swap/ngày, 4 SOL/lệnh |
| 10 | `2T5NgDDidkvhJQg8AHDi74uCFwgp25pYFMRZXBaCUNBH` | KOL Explorer ("IdontPayTaxes") | 7D +847 USD; 30D +5.072 USD; WR 72,5% / 70,6%; giữ 49 giây | 7 / 85,7% / +2,33 | 90 giây / 29% | Mới 7 vị thế. Lệnh lớn nhất chiếm 97% lãi |
| 11 | `51XcBjxnhAPUrDPZkdef1kUK3TJwAwCsCPRkCVJhTUbV` | dEdge | 15D: +28, 56,9%, 211 | 45 / 46,7% / +2,35 | 92 giây / 36% | WR < 50%. Lệnh lớn nhất chiếm 54% lãi |
| 12 | `5d3jQcuUvsuHyZkhdp78FFqc7WogrzZpTtec1X9VNkuE` | KOL Explorer ("Tech") | 7D +3.351; 30D +6.028 USD; WR 75,0% / 69,2% | 3 / 66,7% / +31,87 | 20 giây / 67% | Chỉ 3 vị thế. 1 lệnh chiếm 99% lãi |
| 13 | `A3W8psibkTUvjxs4LRscbnjux6TFDXdvD4m4GsGpQ2KJ` | KOL Explorer ("Numero") | 7D +6.387; 30D +25.304 USD; WR 78,0% / 79,5%; giữ 1 phút | 3 / 33,3% / +4,55 | 427 giây / 0% | Chỉ 3 vị thế đọc được: 89/120 giao dịch gần nhất là giao dịch lỗi của người khác (spam) |
| 14 | `7GPgfyK7YR1AJWL76Wi3snaVnUFc57vJ2xpRWoLh8czn` | dEdge | 15D: +36, 71,1%, 479 | 26 / 80,8% / +4,09 | 28 giây / 73% | Giữ quá ngắn. 39 swap có tip Jito. 127 swap/ngày: hành vi bot |

---

## 3. CÁC VÍ BỊ LOẠI VÀ LÝ DO

### 3.1 Loại theo số liệu leaderboard (293 ví)

| Lý do | Số lần gặp |
|---|---|
| Win rate 30D < 45% | 114 |
| > 3.000 giao dịch/30 ngày (> 100/ngày: bot, market maker) | 82 |
| PnL 7D hoặc 30D không dương | 77 |
| KOL giữ lệnh trung vị < 45 giây (không copy kịp) | 76 |
| > 30 token/ngày | 43 |
| dEdge xếp vào danh sách bot/sniper | 30 |
| Không hoạt động > 3 ngày | 44 |
| dEdge copy check "caution"/"skip" | 32 |
| Chỉ 1–5 token trong 30D (cá voi, insider) | 13 |
| dEdge trạng thái SLOWING/STALE hoặc < 30 lệnh đóng | 29 |

Một ví có thể có nhiều lý do.

- **Top Solana Tracker PnL 30D** (`6ug4s5uo…` $67,9 triệu, `52Xv94ze…`, `9nXDunV8…` 6,2 triệu giao dịch…) đều là cá voi, market maker hoặc ví 1–5 token. **Loại hết.**
- **7 ví xuất hiện ở nhiều nguồn đều bị loại:**
  - `Bi4rd5FH…` (Theo), `CyaE1Vxv…` (Cented), `4vw54Bm…` (Decu), `JDd3hy3g…`: KOL lãi lớn, nhưng giữ lệnh trung vị 15–27 giây, 210.000–600.000 giao dịch/30 ngày.
  - `7ufmve7Z…`, `F5XvCe42…`, `GeUnv1jm…`: dEdge xếp vào danh sách bot.

### 3.2 Loại sau kiểm chứng on-chain (28 ví)

| Nhóm | Ví | Bằng chứng |
|---|---|---|
| Lỗ hoặc hòa trên chain | `43E8Uor7…` (−1,46), `BtMBMPko…` (−1,03, giữ 9 giây), `J1PzRcDW…` (−0,27; 951 swap/ngày), `DADDY96n…` (−0,75; 538 swap/ngày), `5HM96vkn…` (−0,81, cụm A), `AtM5yLyD…` (−6,86), `F2SuErm4…` (−0,13), `DKgvpftt…` (−7,81), `FLb8koi2…` (−0,05) | PnL sau phí từ số dư on-chain |
| Bot tần suất cao | `9s4ji7MR…` (1.335 swap/ngày; dEdge "elite" +1.007 SOL), `7mZzd7cC…` (356), `6ppZ7YeG…` (463), `G7SGtBeh…` (214, WR 43%) | Đếm swap tự ký trong khung |
| Ví trùng trong cụm | `E69vrqyf…` (cụm A), `6AZAXbi3…` (cụm B), `Bup46FaX…` và `BvkazozQ…` (cụm C: cùng slot 41–42/42 token, giữ 18 giây), `HzKH3s8K…` (copy `3kxc`) | Cùng slot / cách 2 giây |
| Mẫu quá nhỏ | `2NMRJ33r…` (15 vị thế, giữ 11 giây), `4F1EWVHb…` (1 vị thế), `GJA1HEbx…` (3), `BoYHJoKn…` (0) | |
| Không phải trader memecoin | `ENxbGG25…` | Chủ yếu chuyển USDC, 0 vị thế memecoin |
| **Không xác minh được** | `J9TYAsWW…` (Johnson), `DYAn4XpA…` (Kay The Doc), `4BdKaxN8…` (Jijo), `CEUA7zVo…` (Tdmilky), `ApRnQN2H…` (Solkrow) | 120 giao dịch gần nhất **toàn là giao dịch lỗi do người khác ký** (bị spam). Ví dụ `4BdKaxN8` có khoảng 726.000 giao dịch/ngày. Không đọc được lệnh thật của ví trong giới hạn RPC miễn phí |

---

## 4. KIỂM CHỨNG CHẤT LƯỢNG DỮ LIỆU

### 4.1 Cách kiểm chứng [OC] với mỗi ví

1. Đọc 1.000 chữ ký gần nhất để đo tần suất.
2. Đọc đầy đủ **120 giao dịch gần nhất**.
3. Chỉ tính giao dịch **ví tự ký**. Mua/bán xác định bằng thay đổi số dư token và SOL của chính ví.
4. Một vị thế "đóng" khi số dư token trước lệnh mua đầu = 0 và sau lệnh bán cuối = 0.
5. PnL = tổng SOL thực nhận/chi, **đã gồm phí mạng, phí ưu tiên, tip Jito**.

### 4.2 Hạn chế

| Hạn chế | Ảnh hưởng |
|---|---|
| Khung on-chain ngắn: 120 giao dịch = **0,6–4,5 ngày** tùy ví | PnL [OC] **không phải** PnL 7D/30D. Chỉ dùng để xác nhận leaderboard không sai về hướng |
| dEdge chỉ có cửa sổ 15 ngày; Solana Tracker chỉ có 30D (bảng 7D trên site báo "No data yet") | Thiếu 7D/30D của các ví dEdge |
| Lệnh đi qua USDC hoặc qua nhiều token trong 1 giao dịch không tính vào vị thế | Vài ví có 1–26 giao dịch "multi_token" chưa tính |
| Chưa mô phỏng trượt giá khi copy trễ | Khả năng copy đánh giá qua thời gian giữ lệnh, chưa có lãi/lỗ copy thực tế |
| Không ghép ví theo nguồn cấp vốn | Cụm ví chỉ phát hiện qua giao dịch cùng slot |

### 4.3 Đối chiếu leaderboard và on-chain

- **Cả 14 ví được chọn:** PnL on-chain trong khung đều **dương**, cùng hướng với leaderboard.
- **Bị on-chain bác bỏ:** 15 ví dEdge đánh "pass" bị loại sau kiểm chứng on-chain: PnL âm, bot tần suất cao, ví trùng trong cụm hoặc mẫu quá nhỏ. Ví dụ `9s4ji7MR…` được dEdge xếp "elite" +1.007 SOL, nhưng trên chain là 1.335 swap/ngày.

---

## 5. KẾT LUẬN

1. **Đã có ví đáng theo dõi:** 4 ví nhóm 1. Ví đáng theo dõi nhất là **`BvtZeURZMcvvQVBr5k9jDT53E2xuFq2Da6d6RTDP7gM4`**:
   - giữ lệnh khoảng 5 phút;
   - 26 vị thế đóng, WR 69%, +5,96 SOL sau phí trong 1,5 ngày;
   - lệnh lớn nhất chỉ 26% tổng lãi;
   - không tip Jito, không phát hiện cụm ví.
2. **Chưa đủ để copy trade thật.** Bằng chứng on-chain mới phủ 1–4 ngày; số liệu 15 ngày là của dEdge.
3. **Hướng tiếp theo nằm trong phạm vi hệ thống hiện có:** đưa 4 ví nhóm 1 và ví `B8bvkQ7U…` (giữ lệnh 2,5 giờ) vào **theo dõi giấy** trên Radar, không phát tín hiệu giao dịch. Dùng bộ đếm PnL sẵn có để tích lũy ≥ 30 ngày trước khi kết luận.
   - Việc này **chưa làm**: chưa sửa scoring, chưa tích hợp vào Radar, theo đúng yêu cầu.
4. **Ví KOL nổi tiếng** (Decu, Cented, Theo…) có lãi thật lớn, nhưng giữ lệnh 15–27 giây: **không copy được** với độ trễ thông thường.

---

## 6. XÁC NHẬN

| Nội dung | Xác nhận |
|---|---|
| Helius credit | **0**. Bộ đếm ngày 23/11 vẫn 106.878 |
| Chi phí khác | Không mua API, không thuê VPS. Dùng trang công khai (khoảng 260 lượt tải trang, 1 trang/giây) và RPC công khai Solana (khoảng 6.100 lần gọi) |
| Mạng | Đã thêm 3 host leaderboard vào danh sách cho phép của môi trường (đã duyệt) |
| Hệ thống | Không sửa schema, scoring, PnL; `trading_enabled = false`. Chỉ thêm script nghiên cứu chỉ đọc |

---

## 7. FILE ĐỐI CHIẾU

**`Doi chieu leaderboard.xlsx`** gồm 4 sheet:

| Sheet | Nội dung |
|---|---|
| **Xep hang** | 14 ví, đầy đủ cột [LB] và [OC] |
| **Kiem chung 42 vi** | Số liệu on-chain của 42 ví |
| **Tat ca 335 vi** | Nguồn, số liệu leaderboard, lý do loại |
| **Vi the da dong** | 827 vị thế với chữ ký mở/đóng để tra trên Solscan |

**Dữ liệu thô:** `winner_data/leaderboards/`:
- `leaderboards.json`, `merged.json`
- `onchain/*.json.gz`, `onchain/summary.json`
- trang HTML Solana Tracker đã lưu
