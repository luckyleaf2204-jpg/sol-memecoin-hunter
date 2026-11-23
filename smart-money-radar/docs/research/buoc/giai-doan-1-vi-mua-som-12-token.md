# BÁO CÁO GIAI ĐOẠN 1 – TÌM VÍ SMART MONEY TRÊN 12 TOKEN THƯỜNG

**Ngày:** 23/11/2026 (giờ UTC).

**Phạm vi:** 12 token thường đã xác định, chỉ xét lệnh mua trên PumpSwap (sau tốt nghiệp) ở vốn hóa 100.000 – 300.000 USD. Không đọc 5 token bot đã loại.

**Ngân sách Giai đoạn 1:** 6.000 credit. **Đã dùng 5.340 credit**, không vượt trần.

**Nguyên tắc:** không sửa schema, scoring, PnL; `trading_enabled = false`; API key không xuất hiện trong file nào (kiểm tra rò rỉ: 0 lần).

---

## 1. TRẢ LỜI 6 CÂU HỎI

| # | Câu hỏi | Kết quả |
|---|---|---|
| 1 | Số ví mua sớm (≥ 0,5 SOL/lệnh, vốn hóa 100–300k USD) | **2.101 ví** (2.130 lượt ví–token) trên 11 token định giá được |
| 2 | Số ví còn lại sau lọc bot, sniper, bundler, dev, giao thức, wash | **2.068 ví** (lọc miễn phí từ dữ liệu đã đọc) |
| 3 | Số ví xuất hiện trên ≥ 2 token thắng | **12 ví**. 4 ví bị gắn cờ bot ở token khác, còn **8 ví**. Thăm dò hoạt động 8 ví này (80 credit) cho thấy **cả 8 đều > 100 giao dịch/ngày**, nên **0 ví** đạt tiêu chí "giống người" |
| 4 | Danh sách ví và bằng chứng | Mục 3 và mục 4 |
| 5 | Dự toán chi phí kiểm tra lịch sử từng ví | Mục 5 |
| 6 | Dữ liệu còn thiếu hoặc chưa xác minh | Mục 6 |

**Kết luận ngắn:** Giai đoạn sau tốt nghiệp cũng cho kết quả giống giai đoạn bonding curve. Ví mua lặp lại trên nhiều token thắng đều là ví hoạt động tần suất cao (bot hoặc ví giao dịch tự động). Hơn 99% ví mua ≥ 0,5 SOL chỉ xuất hiện **một lần** trên một token. Chưa có ví nào đủ điều kiện để kết luận là Smart Money.

---

## 2. CREDIT THEO TỪNG TÁC VỤ

| Tác vụ | Trần đặt trước | Thực tế | Bộ đếm |
|---|---|---|---|
| Thử bộ lọc thời gian (blockTime) | 20 | 10 | `pumpswap-zone` |
| Đọc giao dịch vùng 100–300k của 12 token, đọc từ token rẻ nhất; trần riêng từng token = số chữ ký đã đếm + 10 | 5.990 | 5.250 | `pumpswap-zone` |
| Phân tích, lọc, tìm ví lặp lại (dữ liệu đã lưu, giá SOL theo giờ GeckoTerminal) | 0 | 0 | – |
| Thăm dò hoạt động 8 ví lặp lại (1 lần gọi danh sách chữ ký/ví) | 100 | 80 | `phase1-probe` |
| **Tổng Giai đoạn 1** | **6.000** | **5.340** | còn 660 |

Không token nào bị chạm trần riêng. Trần toàn bộ A+B: 16.000, đã dùng 5.340, còn **10.660**.

---

## 3. PHỄU LỌC

### 3.1 Sửa cách xác định "vùng 100–300k" trước khi đếm

Khi kiểm tra kết quả đọc lần đầu, cách tính vốn hóa theo từng lệnh bị sai ở 3 chỗ. Đã tính lại **offline (0 credit)**:

| Vấn đề | Cách sửa |
|---|---|
| Dùng một giá SOL cố định (72,15 USD) cho mọi lệnh trong 14 ngày, trong khi SOL dao động 66–108 USD | Dùng giá đóng cửa SOL/USD theo giờ, miễn phí từ GeckoTerminal |
| Token `82Z6CcE5…` có pool định giá bằng **USDC**, bộ giải mã coi là SOL (sai 1.000 lần do khác số thập phân) | Quy đổi USDC → USD → SOL theo giá giờ |
| Token `89iRVoDV…` có pool định giá bằng **token khác** (`4CxjZy…`, tên "mihao"), không phải SOL/USDC | **Loại khỏi phân tích**: không quy đổi được ra SOL/USD |

Vốn hóa từng lệnh = (giá trị lệnh USD ÷ số token) × 1.000.000.000 (tổng cung pump.fun).

### 3.2 Tiêu chí lọc (áp dụng theo từng cặp ví–token)

| Bộ lọc | Cách nhận diện |
|---|---|
| Tài khoản giao thức | Ví là pool, agent Mayhem `BwWK17cb…`, migration authority `39azUY…` |
| Dev | Ví tạo token (biết được ở 7/11 token có dữ liệu bonding curve) |
| Sniper / bundler | Mua ở slot tạo token hoặc ≤ 2 slot sau đó (7/11 token có dữ liệu) |
| Bot đã biết | Đã bị xác định là bot ở đợt kiểm chứng 14 ngày (56 ví) |
| Bot tần suất cao | ≥ 20 lệnh trên token trong khung, hoặc ≥ 6 lệnh với khoảng cách trung vị < 5 giây |
| Wash / MEV | Mua và bán cùng một slot |
| Wash vòng | ≥ 3 vòng mua–bán, bán hết, chênh lệch tiền < 3% |

### 3.3 Kết quả từng token

| Token | Định giá | Lệnh trong khung | Ví mua vùng 100–300k ≥ 0,5 SOL | Bị lọc | Còn lại |
|---|---|---|---|---|---|
| `4npJ3avx…` | SOL | 3.384 | 274 | 0 | 274 |
| `6bUb1jX4…` | SOL | 154 | 9 | 0 | 9 |
| `82Z6CcE5…` | USDC | 768 | 131 | 2 (1 dev, 1 bot) | 129 |
| `8rjgC1Gs…` | SOL | 1.278 | 60 | 4 bot | 56 |
| `9okxCGsu…` | SOL | 1.130 | 31 | 2 (1 dev, 1 bot) | 29 |
| `BAKsQryn…` | SOL | 2.693 | 557 | 1 bot | 556 |
| `CUg7AzWL…` | SOL | 2.974 | 45 | 5 (2 bot, 3 wash) | 40 |
| `EV7J2QaP…` | SOL | 2.975 | 185 | 28 (23 bot, 1 MEV, 4 wash) | 157 |
| `VAYMViku…` | SOL | 3.142 | 111 | 1 bot | 110 |
| `dkpuQdje…` | SOL | 6.115 | 716 | 1 bot | 715 |
| `ngwxpGYd…` | SOL | 446 | 11 | 3 bot | 8 |
| `89iRVoDV…` | token khác | 2.330 | – | loại cả token | – |
| **Tổng** | | | **2.130 lượt / 2.101 ví** | **47 lượt** | **2.068 ví** |

**Nhận xét quan trọng:**
- 4 token (`dkpuQdje`, `BAKsQryn`, `4npJ3avx`, `EV7J2QaP`) có hàng trăm ví, mỗi ví chỉ mua 1 lệnh rồi không xuất hiện ở token nào khác. Đây là dấu hiệu **ví dùng một lần** (nhóm ví phụ của cùng một người hoặc bot phân tán lệnh).
- Hai token có nhiều ví nhất (`dkpuQdje`, `BAKsQryn`) lại **không có dữ liệu bonding curve và không biết ví dev**, nên chưa lọc được sniper/bundler/dev ở đây (mục 6).

### 3.4 Ví lặp lại trên ≥ 2 token

- 2.068 ví còn lại chỉ có **12 ví** xuất hiện trên ≥ 2 token.
- 4 ví bị gắn cờ bot/wash ở chính một token khác → loại, còn **8 ví**.
- Thăm dò 1 trang lịch sử (1.000 chữ ký gần nhất) của 8 ví:

| Ví | Số token | Giao dịch/ngày | Khoảng thời gian của 1.000 giao dịch | Đánh giá |
|---|---|---|---|---|
| `F7p3dFrjRTbtRp8FRF6qHLomXbKRBzpvBLjtQcfcgmNe` | 2 | 24.000 | 0,03 ngày | Bot |
| `6TYDxGmVxkBPBmEfnmLXx6jVff9LknsjRHqdTjVyZmG8` | 2 | 14.810 | 0,07 ngày | Bot |
| `96JTtvBj3PP2WTsXrCSaSH3Ga4aboutJX2s4ZHNjENcA` | **3** | 2.107 | 0,47 ngày | Bot |
| `8zkgFGVZrDLieViwqiXFCydSX6WL5hsxmUu55yBdsNsZ` | 2 | 1.235 | 0,81 ngày | Bot |
| `4oFkqkrGYZGhuUA5sY6AT1Z3kNdXt2byfnK8QqKPD5RU` | 2 | 378 | 2,65 ngày | Tần suất cao |
| `GZDRqVfpKLjTmDxf2Zh34WURCojSKbAoDJqxCi53m7ui` | 2 | 370 | 2,70 ngày | Tần suất cao |
| `5t4Tz7qewAHSpDC1YADAmogtiE3Cwud4juNjjVXaDs1p` | 2 | 299 | 3,34 ngày | Tần suất cao |
| `63JJwqP3YAqKB5FVwQotBsJdVJVJfLhKa8h9xmCTBvSq` | 2 | 137 | 7,27 ngày | Gần ngưỡng |

**Lưu ý:** "Giao dịch/ngày" ở đây đếm **mọi** giao dịch của ví, kể cả chuyển tiền và token rác người khác gửi đến. Nên 4 ví cuối (137–378/ngày) **chưa chắc là bot**, nhưng đều vượt ngưỡng 100 lệnh/ngày của tiêu chí đã duyệt. Chỉ `63JJwqP3…` là gần ngưỡng đáng xem xét thêm.

---

## 4. BẰNG CHỨNG GIAO DỊCH 12 VÍ LẶP LẠI

Thời điểm, giá mua và vốn hóa lấy từ **lệnh mua đầu tiên trong vùng 100–300k**. "Đã bán" = số token đã bán ÷ số token mua **trong khung đọc** (> 1 nghĩa là bán cả token mua trước khung, ví dụ mua từ bonding curve).

| Ví | Token | Mua đầu (SOL) | Vốn hóa lúc mua (USD) | Tổng mua / bán trong khung (SOL) | Đã bán | Chữ ký lệnh mua đầu | Chữ ký lệnh bán |
|---|---|---|---|---|---|---|---|
| `96JTtvBj…` | 82Z6CcE5 | 2,975 | 174.774 | 2,98 / 2,75 | 1,78 | `BvYm3ysri1bQbJWzSqbh…` | `4ZmsCufibz1XEWHhJS2d…` |
| | CUg7AzWL | 2,785 | 101.890 | 7,86 / 7,22 | 1,00 | `ryX77Tb7Xyee5mJdLgjz…` | `2EGUwofDWf7yjgRGnA3H…` |
| | EV7J2QaP | 2,925 | 103.169 | 14,79 / 15,91 | 1,00 | `2zAbB4qZv5sSXk82X15S…` | `2HW2hiyb9Q4yka4i6q5V…` |
| `3vKC1m1C…` (cờ bot) | 82Z6CcE5 | 1,009 | 127.766 | 5,96 / 2,36 | 0,45 | `5nPd4F4e7P4vSgQ7mDiD…` | `2WZvtjLTya1gcxfPehL7…` |
| | 8rjgC1Gs | 0,725 | 134.956 | 6,90 / 8,74 | 1,11 | `UZjj6k8NuX3XEAoWyMw4…` | `4xgz27THJ8evGkzUoDPR…` |
| | 9okxCGsu | 0,641 | 104.325 | 1,15 / 3,91 | 2,31 | `NXh8h5MZ46EgS4SjFXbE…` | `YsgmV3PrAgLDJW8KWDxZ…` |
| `sssssv2i…` (cờ bot, địa chỉ vanity) | 8rjgC1Gs | 1,077 | 145.512 | 5,37 / 6,81 | 1,18 | `2dTwDWRMbqCMEicCLNjo…` | `FY51wGxgRhqGTXMrKGCa…` |
| | 9okxCGsu | 0,951 | 118.330 | 0,95 / 0,94 | 1,00 | `2KuZM9aMakEqkSVTv5F8…` | `HwtHoe5oExeVrSMWMYKj…` |
| | CUg7AzWL | 0,966 | 115.719 | 8,82 / 9,61 | 1,00 | `3UVkpCPsQefGkdwasbBm…` | `2KsKWPyunwntK4qXKP4f…` |
| `kEFiAX3j…` (cờ bot) | 82Z6CcE5 | 2,140 | 100.739 | 22,04 / 24,28 | 1,07 | `2uwgp37hWCYF8NoLEvg3…` | `2WgEKXxgdzg7uKLk5pZz…` |
| | 8rjgC1Gs | 1,822 | 147.762 | 10,36 / 12,99 | 1,16 | `gBQsxBTAUgPXf1kMfkaT…` | `d6nmWyLKAkRYtPXvGuj4…` |
| `ARu4n5mF…` (cờ bot) | 82Z6CcE5 | 1,006 | 107.814 | 6,99 / 0,95 | 0,12 | `4M7N3Emn12BmQL3ny1aH…` | `2gBgU3d4gJB91hqj5R9t…` |
| | BAKsQryn | 3,394 | 145.330 | 3,44 / 3,60 | 1,02 | `5zEjD6vQ8L9DsUCT2J4Z…` | `3MDYevuWaUKZ6z22esAa…` |
| `8zkgFGVZ…` | 82Z6CcE5 | 4,380 | 107.180 | 14,96 / 14,44 | 1,19 | `3taZ7HxapaGUBLy3sti2…` | `63TLnXxL61BeQsN4sYnh…` |
| | 8rjgC1Gs | 3,025 | 100.981 | 3,03 / 2,66 | 1,00 | `Aex9p1nZ2YeNcbF7LBCT…` | `S42Rn6guQrTkbSKm4CiZ…` |
| `6TYDxGmV…` | 82Z6CcE5 | 0,980 | 117.415 | 0,98 / 0 | 0 | `3hyCE3T1uz7QrTxmxs7Z…` | chưa thấy |
| | 9okxCGsu | 9,567 | 100.029 | 10,52 / 0 | 0 | `4fXsMAexuNUHTsBcNeLu…` | chưa thấy |
| `4oFkqkrG…` | 82Z6CcE5 | 1,029 | 161.136 | 1,03 / 0 | 0 | `SV9MRjmP5BBnXdpPK9jg…` | chưa thấy |
| | 8rjgC1Gs | 1,009 | 119.496 | 1,01 / 0,89 | 1,00 | `2phHSZum4CERzhrY6AqW…` | `3qgVxNkmVrTGT9HgMWjw…` |
| `5t4Tz7qe…` | 82Z6CcE5 | 8,924 | 184.932 | 8,92 / 10,62 | 1,00 | `3gCs74NrDSHcx2RWN9p5…` | `Lr6jo6cgBK9GVUPv1adE…` |
| | EV7J2QaP | 5,932 | 130.196 | 5,93 / 5,51 | 1,00 | `8Q8BaN554mvGQB86nB2F…` | `4XAk18ZZEEkhCycaigxV…` |
| `F7p3dFrj…` | 9okxCGsu | 0,647 | 156.803 | 1,30 / 0 | 0 | `3JrnX7DAzqFP2KFSnRnE…` | chưa thấy |
| | EV7J2QaP | 0,721 | 180.393 | 0,72 / 0 | 0 | `3MNpPTnNXe1LaeYdcDWi…` | chưa thấy |
| `GZDRqVfp…` | BAKsQryn | 0,741 | 126.715 | 1,51 / 1,50 | 1,00 | `2tkJp3s5bV2HMB7wcDGM…` | `2HUkpPj652HszEqMBNTj…` |
| | EV7J2QaP | 0,651 | 105.296 | 1,26 / 1,43 | 1,00 | `39e5UcqbV9cUkVqk5in5…` | `3Ta34Kk1Z7h1ot94iseP…` |
| `63JJwqP3…` | BAKsQryn | 0,504 | 257.785 | 0,50 / 0 | 0 | `5vTwFFqZHtyTjB9ojMDT…` | chưa thấy |
| | EV7J2QaP | 0,501 | 112.604 | 1,00 / 0,81 | 1,00 | `3Bp8JXdBNNEQgnun3fcN…` | `445GtBGcUjgWQN3WBtCJ…` |

Chữ ký đầy đủ của mọi lệnh nằm trong `winner_data/pumpswap/phase1_result.json` và `trades/<mint>.json.gz`.

**Kiểm tra đủ dữ liệu (yêu cầu 5):**

| Nội dung | Tình trạng |
|---|---|
| Thời điểm mua | **Có** cho mọi lệnh (blockTime, slot, chữ ký) |
| Giá mua | **Có** cho 11 token định giá bằng SOL/USDC (SOL ÷ số token). Không có cho `89iRVoDV…` |
| Khả năng thoát lệnh | **Một phần.** 19/27 vị thế đã bán trong khung đọc. 8 vị thế chưa thấy lệnh bán: lệnh bán nằm ngoài khung đọc, phải đọc lịch sử ví (Giai đoạn 2) |
| Lãi/lỗ | **Chưa tính.** Nhiều vị thế bán nhiều hơn số token mua trong khung (mua thêm trước khung), nên lãi/lỗ trong khung không đáng tin. Phí và lệnh thua chỉ có khi đọc toàn bộ lịch sử ví |

Không kết luận ví nào là Smart Money từ bảng này. Lãi mỏng, ví dụ `96JTtvBj…` mua 14,79 / bán 15,91 SOL, và tần suất giao dịch cao cho thấy đây là bot giao dịch theo động lượng, không phải người chọn token.

---

## 5. DỰ TOÁN CHI PHÍ KIỂM TRA LỊCH SỬ TỪNG VÍ (GIAI ĐOẠN 2)

Bảng giá Helius: đọc đầy đủ 10 credit / 100 giao dịch. Chỉ đếm chữ ký: 10 credit / 1.000. Ước tính theo tốc độ giao dịch vừa đo.

| Ví | Giao dịch/ngày | Đọc đầy đủ 60 ngày | Đọc đầy đủ 14 ngày | Khuyến nghị |
|---|---|---|---|---|
| `F7p3dFrj…` | 24.000 | ~144.000 | ~33.600 | Không đọc (bot) |
| `6TYDxGmV…` | 14.810 | ~88.900 | ~20.700 | Không đọc (bot) |
| `96JTtvBj…` | 2.107 | ~12.600 | ~2.950 | Không đọc (bot) |
| `8zkgFGVZ…` | 1.235 | ~7.400 | ~1.730 | Không đọc (bot) |
| `4oFkqkrG…` | 378 | ~2.270 | ~530 | Không đọc (vượt 100/ngày) |
| `GZDRqVfp…` | 370 | ~2.220 | ~520 | Không đọc (vượt 100/ngày) |
| `5t4Tz7qe…` | 299 | ~1.800 | ~420 | Không đọc (vượt 100/ngày) |
| `63JJwqP3…` | 137 | ~830 | ~190 | Có thể đọc 14 ngày nếu duyệt |

Đọc cả 8 ví 60 ngày sẽ tốn khoảng **260.000 credit**, vượt xa trần A+B còn lại (10.660). **Không đề xuất** chi credit cho nhóm này.

---

## 6. DỮ LIỆU CÒN THIẾU HOẶC CHƯA XÁC MINH

1. **`89iRVoDV…` bị loại:** pool định giá bằng token "mihao", không quy đổi được ra SOL/USD. Có thể bổ sung miễn phí bằng giá theo giờ của pool mihao, nhưng chưa làm.
2. **Chưa lọc được dev/sniper/bundler ở 5 token** (`4npJ3avx`, `BAKsQryn`, `EV7J2QaP`, `VAYMViku`, `dkpuQdje`); riêng `82Z6CcE5` biết dev nhưng không có dữ liệu bonding curve. Nhóm 5 token này là nhóm token có nhiều ví nhất (khoảng 1.800 ví).
3. **Ví dùng một lần:** khoảng 2.050 ví chỉ mua 1 lần trên 1 token. Chưa kiểm tra nguồn cấp vốn (ví mẹ). Nếu nhiều ví cùng một nguồn tiền thì có thể ghép thành một "người" và xuất hiện lặp lại. Việc này cần đọc giao dịch nạp tiền đầu tiên của từng ví: khoảng 10 credit/ví, tổng khoảng 20.000 credit, vượt trần.
4. **"Giao dịch/ngày" khi thăm dò** đếm cả giao dịch rác. Chưa tách số lệnh mua/bán thật của 4 ví 137–378/ngày.
5. **Lệnh thoát ngoài khung đọc** (8/27 vị thế) và **lệnh thua, phí** chưa có. Chỉ có khi đọc lịch sử ví (Giai đoạn 2).
6. **Vốn hóa dùng tổng cung 1 tỷ token** (chuẩn pump.fun). Chưa trừ token đã đốt (nếu có).

---

## 7. ĐỀ XUẤT (CẦN DUYỆT TRƯỚC GIAI ĐOẠN 2)

| Phương án | Chi phí | Ý kiến |
|---|---|---|
| A. Đọc 14 ngày ví `63JJwqP3…` (ví duy nhất gần ngưỡng) | ~190 credit | Chi phí thấp nhưng khả năng đạt thấp: chỉ 2 lệnh 0,5 SOL |
| B. Dừng hướng "ví lặp lại trên token thắng" | 0 | Hai lần khảo sát (bonding curve và PumpSwap) đều cho kết quả giống nhau: ví lặp lại là bot |
| C. Bổ sung miễn phí: định giá `89iRVoDV`, gom ví theo nguồn cấp vốn với dữ liệu đã có | 0 | Có thể làm trước khi quyết định chi thêm |

**Khuyến nghị:** **B + C**. Không chi credit Giai đoạn 2 cho 8 ví hiện có. Chờ anh/chị quyết định.

---

## 8. LƯU TRỮ

| File (trong `winner_data/pumpswap/`) | Nội dung |
|---|---|
| `trades/<mint>.json.gz` | Toàn bộ lệnh PumpSwap trong khung đọc của 12 token |
| `pool_quotes.txt` | Đồng định giá của từng pool (GeckoTerminal) |
| `sol_usd_hourly.json` | Giá SOL/USD theo giờ dùng để tính vốn hóa |
| `phase1_result.json` | Phễu từng token, lý do lọc, 12 ví lặp lại kèm chữ ký |
| `probe_result.json` | Kết quả thăm dò 8 ví |
| `progress.jsonl` | Credit trước/sau từng token |
