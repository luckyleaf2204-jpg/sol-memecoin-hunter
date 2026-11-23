# BÁO CÁO TRUY NGƯỢC 14 NGÀY – TÌM VÍ SMART MONEY TỪ TOKEN THẮNG

**Ngày:** 23/11/2026 (giờ UTC).

**Phạm vi:** các token tốt nghiệp pump.fun → PumpSwap trong 14 ngày (từ 09/11 đến 23/11/2026).

**Ngân sách:** trần đã duyệt 77.760 credit, không nâng.

**Nguyên tắc:** không sửa scoring, PnL hay schema; `trading_enabled = false`.

---

## 1. KẾT QUẢ THEO 6 MỤC YÊU CẦU

| Mục | Kết quả |
|---|---|
| Tổng token đã lọc miễn phí | **11.425** token tốt nghiệp trong 14 ngày, đều đã chụp vốn hóa miễn phí. **139** token còn vốn hóa ≥ 100.000 USD được chấm tiếp bằng giá theo giờ (miễn phí), ra **24 token thắng**. |
| Số token thực sự được đọc lịch sử | **14** token, theo thứ tự ưu tiên. 10 token ra mắt bằng bundle (tốt nghiệp trong ≤ 60 giây) được **bỏ qua không tốn credit**. Trong 14 token đã đọc, 4 token đọc ra 0 lệnh (mục 4). |
| Số ví ứng viên | **56**, gồm 2 nhóm: **31** ví mua sớm, chốt lãi trong giai đoạn bonding curve trên ≥ 3 token thắng; **25** ví mua sớm (≥ 0,5 SOL) và **giữ qua lúc tốt nghiệp** ở ≥ 2 token thắng. |
| Số ví vượt qua kiểm chứng | **0 / 56** |
| Credit thực tế đã dùng | **19.416 / 77.760** (bộ đếm nội bộ, theo bảng giá Helius). Còn 58.344. |
| Dữ liệu còn thiếu | Xem mục 4 |

**Chi tiết credit theo bước** (nhật ký: `winner_data/progress.jsonl`):

| Bước | Credit |
|---|---|
| Lấy danh sách lần 1 (lỗi đọc log bị cắt, đã sửa) | 2.240 |
| Lấy danh sách lần 2 (cũng chưa lưu được kết quả) | 2.280 |
| Lấy danh sách lần 3 (đúng, **đã lưu**) | 2.240 |
| Đọc lịch sử nhóm 1 (8 token) | 5.010 |
| Đọc lịch sử nhóm 2 (6 token) | 1.770 |
| Đọc lịch sử nhóm 3 (10 token bundle, chỉ đếm chữ ký) | 40 |
| Kiểm chứng 31 ví (nhóm chốt lãi) | 2.131 |
| Kiểm chứng 25 ví (nhóm giữ token) | 3.705 |
| **Tổng** | **19.416** |

---

## 2. KẾT LUẬN CHÍNH (CÓ BẰNG CHỨNG)

### 2.1 Ví "mua sớm và có lãi lặp lại trên nhiều token thắng" gần như toàn bộ là bot

**Nhóm 31 ví** (chốt lãi trong giai đoạn bonding curve, trên 3–8 token thắng):
- 24 ví bị phát hiện là bot ngay bằng một lần kiểm tra rẻ: ≥ 1.000 giao dịch/ngày.
- 7 ví đã đọc toàn bộ lịch sử: 834–2.697 giao dịch/ngày.
- Nhiều ví có địa chỉ "vanity" (`sssss…`, `STorre…`), đặc trưng của bot.
- Lãi rất mỏng so với vốn, ví dụ +3,9 SOL trên 70,8 SOL vốn.

**Nhóm 25 ví** (giữ token qua lúc tốt nghiệp):
- 12 ví là bot ngay ở bước kiểm tra nhanh.
- 10 ví có 134–1.343 giao dịch/ngày.
- Chỉ **3 ví có hành vi giống người** (17–34 giao dịch/ngày), nhưng không ví nào đạt:

| Ví | Kết quả kiểm chứng |
|---|---|
| `4Hq9UkSXjEwDZDBMAo9Lnri7VnbQCoomRAdrT6NGMh1c` | **Lỗ 37,0 SOL** trên 14 vị thế đã xác minh; Win Rate 21%, PF 0,11 |
| `FfztZq1outrYfyYbKw5P4LGo4xUc4XYEdf7o91UdS7qu` | Chỉ 4,9% lệnh xác minh được (đi qua USDC); 3 vị thế dùng được, +7,6 SOL ⇒ **không đủ dữ liệu** |
| `5rZtaPkghhMg6VDGGCm1YJ6LVn4NX9CEn6LJEADe35S9` | 0% lệnh xác minh được ⇒ **không đủ dữ liệu** |

**Ví có PnL lớn nhất trong cả hai nhóm:** `jmemehQbZXX7QqNE7Eyi81MdTZw6cEAT6TU4Kinwtru`.
- 679 vị thế, **+143,8 SOL**, PF 1,54.
- Nhưng Win Rate chỉ 17,8% và 134,7 giao dịch/ngày.
- Đây là kiểu bot "rải lệnh, ăn vài lệnh lớn". Bị loại vì là bot. Không thể copy được vì phải vào hàng trăm lệnh mỗi ngày.

### 2.2 Ý nghĩa

Trong 14 ngày dữ liệu này, **người mua sớm token thắng ở giai đoạn bonding curve chủ yếu là bot**. Ví người thật lọt vào nhóm này thì lỗ, hoặc không kiểm chứng được. Bộ lọc **đã làm đúng việc của nó**: loại bot và loại ví may mắn, nên không đưa ra ví giả mạo "Smart Money".

**Kết luận trung thực:**
- Cách tìm Smart Money qua **giai đoạn bonding curve** của pump.fun **không cho ra ví người thật có lãi ổn định** trong 14 ngày này.
- Vùng chưa được khảo sát là **giai đoạn sau tốt nghiệp** (PumpSwap, vốn hóa khoảng 100.000 – 1.000.000 USD). Đây là nơi trader thường vào lệnh với khối lượng lớn và giữ lâu hơn (mục 5).

---

## 3. PHỄU CHI TIẾT

| Bước | Số lượng |
|---|---|
| Token tốt nghiệp 14 ngày | 11.425 |
| Còn vốn hóa ≥ 100.000 USD hôm nay (lọc miễn phí) | 139 |
| Chấm giá theo giờ: tăng ≥ 2,5 lần sau tốt nghiệp, đỉnh ≥ 300.000 USD, 1 giờ sau đỉnh còn ≥ 30% | **24 token thắng** |
| Bỏ qua: ra mắt bằng bundle | 10 |
| Đọc lịch sử giai đoạn bonding curve | 14 (10 token có dữ liệu, 4 token 0 lệnh) |
| Người mua "sớm" (vào ở ≤ 30% vốn hóa đỉnh) trên 10 token có dữ liệu | 3.744 |
| Sau khi loại sniper, bundler, creator, ví nhỏ < 0,2 SOL, ví chưa chốt, ví lỗ | 699 lượt có lãi |
| Ví có lãi lặp lại trên ≥ 3 token | 31 → kiểm chứng: **0 đạt** |
| Ví giữ token qua lúc tốt nghiệp trên ≥ 2 token (≥ 0,5 SOL) | 25 → kiểm chứng: **0 đạt** |

---

## 4. DỮ LIỆU CÒN THIẾU / HẠN CHẾ

1. **Giai đoạn sau tốt nghiệp (PumpSwap) chưa được đọc.** Ví giữ token qua lúc tốt nghiệp không có phần bán ra trong dữ liệu đợt này. Đây là hạn chế lớn nhất.
2. **4 token đọc ra 0 lệnh** dù đã đọc 2–75 trang (khoảng 1.310 credit): `FVzqGqRm…`, `Cmf39V6X…`, `82Z6CcE5…`, `89iRVoDV…`.
   - Giả thuyết: giao dịch kiểu pump.fun mới, ghi sự kiện ở inner instruction thay vì log, nên bộ giải mã từ log chưa đọc được.
   - **Chưa xác minh.**
3. **Thiên lệch "token còn sống":** bộ lọc miễn phí dùng vốn hóa hiện tại, nên bỏ qua token từng thắng rồi chết sau vài ngày (11.286 token bị lọc ở bước này).
4. **Chưa kiểm tra quan hệ cấp vốn với creator** với dữ liệu quá khứ. Ví creator thì đã bị loại.
5. **Lệnh đi qua USDC không xác minh được PnL** (đã biết từ trước). 2 ví người thật rơi vào trường hợp này.
6. **Stream của hệ thống bỏ sót khoảng 85% giao dịch tốt nghiệp** (khoảng 112/ngày so với khoảng 800/ngày thực tế), cùng nguyên nhân log bị cắt. Đây là lỗi ở stream thời gian thực, **chưa sửa**.

---

## 5. ĐỀ XUẤT BƯỚC TIẾP THEO (CẦN DUYỆT)

| # | Việc | Chi phí | Lý do |
|---|---|---|---|
| 1 | **Khảo sát giai đoạn sau tốt nghiệp:** với 24 token thắng, đọc lệnh PumpSwap 6–24 giờ đầu sau tốt nghiệp. Tìm ví mua ở vốn hóa 100.000–300.000 USD, bán có lãi, lặp lại ≥ 3 token. Đếm số chữ ký trước (10 credit/token) để ước tính rồi mới đọc. | Chưa biết. Ước tính sau bước đếm (khoảng 240 credit); trần giữ trong phần còn lại 58.344 | Vùng chưa khảo sát, nơi trader người thật hoạt động nhiều hơn |
| 2 | Sửa bộ giải mã để đọc được sự kiện ở inner instruction (4 token 0 lệnh), và sửa stream nhận đủ giao dịch tốt nghiệp | 0 credit (sửa code) | Dữ liệu đầy đủ hơn |
| 3 | Mở rộng sang token đã chết (bỏ lọc "còn sống"), chấm bằng giá theo giờ | 0 credit, khoảng 10 giờ do API miễn phí giới hạn tốc độ | Giảm thiên lệch |
| 4 | Dùng nguồn nhãn bên ngoài (Nansen/Birdeye top traders) làm danh sách ví gợi ý, kiểm chứng bằng hệ thống | Phí dịch vụ ngoài | Bổ sung danh sách ví mà phương pháp on-chain chưa tìm ra |

**Khuyến nghị:** làm **#2** (miễn phí) và **#1** (có trần), vì đây là phần dữ liệu còn thiếu quan trọng nhất.

---

## 6. LƯU TRỮ ĐỂ TÁI LẬP (KHÔNG CẦN GỌI API LẠI)

**Vị trí:**
- Thư mục `winner_data/` trên máy chủ.
- Bản nén trong repo: `docs/research/data/winner_data_2026-11-23.tar.gz` (5,4 MB).

| File | Nội dung |
|---|---|
| `graduations.json` | 11.425 token tốt nghiệp: mint, pool, chữ ký migration, thời điểm |
| `snapshot.json` | Vốn hóa hiện tại và pool PumpSwap của từng token |
| `ranking.json` | Kết quả chấm giá, lý do loại, điểm ưu tiên |
| `estimates.json` | Số chữ ký giai đoạn bonding curve, thời điểm tạo |
| `trades/<mint>.json.gz` | Toàn bộ lệnh giai đoạn bonding curve đã giải mã: chữ ký, slot, ví, mua/bán, SOL, token, vốn hóa |
| `results.json` | Phễu người mua sớm và danh sách người mua hợp lệ của từng token |
| `candidates.json`, `candidates_held.json` | 31 + 25 ví ứng viên, kèm bằng chứng |
| `verification.json`, `verification_held.json` | Kết quả kiểm chứng toàn bộ lịch sử từng ví: số liệu, lý do loại, chữ ký các lệnh thắng/thua |
| `progress.jsonl`, `report.json`, `verify-*.json` | Credit trước/sau từng nhóm, báo cáo tổng |

**Công cụ** (đã commit):
- `python -m radar winner-history`: các bước 1–4.
- `python -m radar winner-verify`: bước kiểm chứng.
- Chạy lại sẽ dùng file đã lưu, chỉ gọi API cho phần còn thiếu.
