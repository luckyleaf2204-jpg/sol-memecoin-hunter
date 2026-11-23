# BÁO CÁO KẾT QUẢ PHẦN C – KIỂM CHỨNG CÁC VÍ LẶP LẠI TRÊN TOKEN THẮNG

**Ngày:** 23/11/2026 (giờ UTC).

**Nguồn dữ liệu:**
- Dữ liệu đã lưu ở Giai đoạn 1.
- RPC công khai miễn phí của Solana (`api.mainnet-beta.solana.com`).
- Giá miễn phí từ GeckoTerminal.

**Helius credit dùng trong phần này: 0.**

---

## 1. TÓM TẮT ĐIỀU HÀNH

### Hạng mục đã hoàn thành

| # | Hạng mục | Kết quả |
|---|---|---|
| 1 | Định giá lại `89iRVoDV` | **Đã định giá.** Pool tính bằng token mihao (6 số thập phân, xác minh trên chain). Giá mihao lấy từ nến 5 phút GeckoTerminal. Token này có 120 ví mua ≥ 0,5 SOL trong vùng 100–300k USD. Thêm token này làm xuất hiện **1 ví lặp lại mới** (`7kgWHsUp…`), nâng số ví cần kiểm chứng từ 8 lên **9** |
| 2 | Nguồn cấp vốn, quan hệ giữa các ví | Đã làm cho 9 ví. 3 ví đọc được tới giao dịch đầu tiên (xác định được nguồn vốn). 6 ví có lịch sử quá dài: > 30.000 giao dịch trong vài ngày |
| 3 | Tách swap do ví ký với spam, transfer, giao dịch rác | Phân loại **1.080 giao dịch mẫu** (120 giao dịch ngẫu nhiên/ví) bằng số dư trước/sau và chữ ký |
| 4 | Số swap thực tế/ngày | Tính cho 9 ví, kèm khoảng tin cậy 95% |
| 5 | Rà soát vị thế, kiểm chứng lệnh bán và PnL | **21 vị thế / 70 giao dịch** đối chiếu trên chain. **12 vị thế** xác định đủ cả điểm mua lẫn điểm bán |

### Kết quả

| Nhóm | Số ví | Ví |
|---|---|---|
| **BOT** | **7** | `96JTtvBj`, `8zkgFGVZ`, `5t4Tz7qe`, `7kgWHsUp`, `GZDRqVfp`; cùng 2 tài khoản hạ tầng, không phải ví cá nhân: `6TYDxGmV`, `F7p3dFrj` |
| **NGHI VẤN** | **2** | `63JJwqP3`, `4oFkqkrG` |
| **ỨNG VIÊN SMART MONEY** | **0** | – |

**Có ví nào đủ điều kiện đưa vào danh sách theo dõi không?** **Không.**

**Dữ liệu hiện tại đã đủ để tìm Smart Money chưa?** **Chưa.**
- Phương pháp "ví mua lặp lại trên token thắng" chỉ tìm ra ví giao dịch tự động.
- Cả 9 ví đều bán rất sớm. Ví dụ token `EV7J2QaP`: 5 ví mua ở 100–130k USD, cách đỉnh 798.000 USD 27–32 giờ, rồi bán sau 7 phút đến 16 giờ. Lãi xác minh lớn nhất ở token này chỉ +0,77 SOL.
- Đây là hành vi lướt sóng nhanh trên rất nhiều token, không phải chọn token và giữ lệnh.

---

## 2. BẢNG ĐÁNH GIÁ CHI TIẾT 9 VÍ

**Cách đọc số liệu:**
- **Swap thực tế/ngày** = (tỷ lệ swap do ví ký trong 120 giao dịch mẫu) × (tổng giao dịch/ngày đo được). Khoảng tin cậy 95% theo phương pháp Wilson.
- **"Đã loại"** = giao dịch trong mẫu không phải swap do ví ký: token rác gửi đến, SOL gửi đến, giao dịch bên thứ ba, giao dịch lỗi.
- **"Mua trước đỉnh"** = số giờ từ lệnh mua đầu đến giờ vốn hóa đạt đỉnh (dữ liệu giờ GeckoTerminal).
- **PnL xác minh** = tổng SOL ví thực nhận/chi trên chain. Đã bao gồm phí mạng, phí ưu tiên và tip Jito.

### 2.1 `96JTtvBj3PP2WTsXrCSaSH3Ga4aboutJX2s4ZHNjENcA` – **BOT** (tin cậy **cao**)

| Mục | Số liệu |
|---|---|
| Token thắng tham gia | 4: `82Z6CcE5`, `89iRVoDV`, `CUg7AzWL`, `EV7J2QaP` |
| Mua sớm | 11 lệnh mua.<br>`CUg7`: 17/11 01:40, vốn hóa 101.890 USD, **135 giờ** trước đỉnh 733.229.<br>`EV7J`: 21/11 02:47, vốn hóa 103.169, 27 giờ trước đỉnh 798.441.<br>`82Z6`: 16/11 03:25, vốn hóa 174.774, 150 giờ trước đỉnh 499.050.<br>`89iR`: 12/11 14:40, vốn hóa 111.317, **sau đỉnh** (đỉnh 11/11) |
| Bán | 11 lệnh bán.<br>`89iR`: giữ 78 phút, **+3,098 SOL**.<br>`CUg7`: giữ 14,5 giờ, **−0,824**.<br>`EV7J`: giữ 16,4 giờ, **+0,774**.<br>`82Z6`: bán cả token mua trước khung dữ liệu, PnL không xác định |
| Swap thực tế/ngày | **4.458** (KTC 95%: 4.058–4.746). 101/120 giao dịch mẫu là swap tự ký, trên **111 token khác nhau** |
| Đã loại | 19/120: 15 token rác, 2 SOL gửi đến, 1 giao dịch bên thứ ba, 1 lỗi |
| Nguồn vốn, quan hệ | Không đọc tới giao dịch đầu (30.000 giao dịch chỉ phủ 5,7 ngày). Không có giao dịch ký chung với 8 ví còn lại. Trùng 11 giao dịch với `8zkgFGVZ` nhưng đều do ví spam `HckQ93Xq…` ký, **không phải quan hệ** |
| Win rate, PnL | 2/3 vị thế xác minh thắng, PnL **+3,048 SOL** sau phí. 76% đến từ 1 lệnh (`89iR`) |
| Dấu hiệu | Bot giao dịch tần suất rất cao: trung bình 19 giây/swap. Không thể copy |

### 2.2 `6TYDxGmVxkBPBmEfnmLXx6jVff9LknsjRHqdTjVyZmG8` – **BOT / tài khoản hạ tầng** (tin cậy **cao**)

| Mục | Số liệu |
|---|---|
| Token thắng tham gia | 3: `82Z6`, `89iR`, `9okx` |
| Mua | 10 lệnh mua (82Z6 15/11 11:15; 89iR 12/11 13:29; 9okx 17/11 01:35). **0 lệnh bán** trong dữ liệu |
| Swap thực tế/ngày | **0 swap do ví này ký.** 117/120 giao dịch mẫu do khóa khác ký, 3 lỗi. Tổng 16.353 giao dịch/ngày |
| Đã loại | 120/120 |
| Bản chất | Tài khoản dùng chung: **98 khóa ký khác nhau** gọi chương trình `B3111yJC…` thông qua tài khoản này. Số dư 8,51 SOL. Lệnh mua trong pool do người khác ký: **không phải một trader** |
| Win rate, PnL | Không xác định (ví không ký lệnh) |
| Kết luận | Loại. Không phải ví cá nhân, không thể copy |

### 2.3 `8zkgFGVZrDLieViwqiXFCydSX6WL5hsxmUu55yBdsNsZ` – **BOT** (tin cậy **cao**)

| Mục | Số liệu |
|---|---|
| Token thắng tham gia | 2: `82Z6`, `8rjg` |
| Mua | 4 lệnh.<br>`8rjg`: 19/11 02:19, vốn hóa 100.981, 91 giờ trước đỉnh 630.313.<br>`82Z6`: 15/11 10:32, vốn hóa 107.180 |
| Bán | 4 lệnh.<br>`8rjg`: giữ 4,8 giờ, **−0,433 SOL**.<br>`82Z6`: trước khi vào khung đã giữ 3,88 triệu token; sau lệnh cuối còn 2,37 triệu ⇒ PnL không xác định |
| Swap thực tế/ngày | **771** (590–976). 40 swap mẫu trên 47 token |
| Đã loại | 80/120: 64 token rác, 11 SOL gửi đến, 5 bên thứ ba |
| Nguồn vốn, quan hệ | Không đọc tới giao dịch đầu. Không có quan hệ thật với ví khác (chỉ spam chung) |
| Win rate, PnL | 0/1 vị thế xác minh, −0,433 SOL |

### 2.4 `4oFkqkrGYZGhuUA5sY6AT1Z3kNdXt2byfnK8QqKPD5RU` – **NGHI VẤN** (tin cậy **trung bình**)

| Mục | Số liệu |
|---|---|
| Token thắng tham gia | 2: `82Z6`, `8rjg` |
| Mua | 2 lệnh.<br>`82Z6`: 16/11 02:01, vốn hóa 161.136, 151 giờ trước đỉnh.<br>`8rjg`: 19/11 02:00, vốn hóa 119.496, 91 giờ trước đỉnh |
| Bán | 1 lệnh.<br>`8rjg`: 19/11 14:00, giữ 12 giờ, **−0,139 SOL**.<br>`82Z6`: **còn giữ 460.309 token**, chưa thấy bán |
| Swap thực tế/ngày | **92** (KTC 62–132): **không khẳng định được** dưới hay trên ngưỡng 100. Tổng giao dịch thô 504/ngày, nhưng 81% là rác/giao dịch đến |
| Đã loại | 98/120: 68 token rác, 20 SOL gửi đến, 7 chuyển tiền tự ký, 3 bên thứ ba |
| Nguồn vốn | Giao dịch đầu tiên 16/09/2026: nhận 0,435 + 1,28 SOL từ `FRsTdvsfaTXeokX2zPKditkH2HYhg4bApRx37ZLFxCAt`. Ví nguồn nhỏ: 652 giao dịch/91 ngày, số dư 0,042 SOL. Không liên quan ví nào khác trong danh sách |
| Dấu hiệu | **22/22 swap mẫu đều trả tip Jito** qua Jupiter: dùng công cụ giao dịch tự động hoặc terminal. 18 token khác nhau trong 22 swap |
| Win rate, PnL | 0/1 vị thế xác minh, −0,139 SOL. **Không có bằng chứng lãi** |

### 2.5 `5t4Tz7qewAHSpDC1YADAmogtiE3Cwud4juNjjVXaDs1p` – **BOT** (tin cậy **cao**)

| Mục | Số liệu |
|---|---|
| Token thắng tham gia | 2: `82Z6`, `EV7J` |
| Mua | 2 lệnh.<br>`82Z6`: 16/11 03:25, vốn hóa 184.932.<br>`EV7J`: 20/11 21:40, vốn hóa 130.196, 32 giờ trước đỉnh |
| Bán | 3 lệnh.<br>`82Z6`: giữ **1,1 phút**, +0,614 SOL.<br>`EV7J`: giữ **7,8 phút**, −1,107 SOL |
| Swap thực tế/ngày | **250** (207–294). 61 swap mẫu trên 54 token |
| Đã loại | 59/120: 22 bên thứ ba, 18 SOL gửi đến, 17 token rác, 2 chuyển tiền |
| Dấu hiệu | Trả **1,18 SOL phí ưu tiên** cho 5 giao dịch: đặc trưng bot tranh khối |
| Win rate, PnL | 1/2 vị thế xác minh, tổng **−0,493 SOL** |

### 2.6 `7kgWHsUphcprHepdKTrrKPnwG6fhdRdJnxZDAtRoQBi` – **BOT** (tin cậy **trung bình – cao**)

Ví này mới xuất hiện sau khi định giá lại `89iRVoDV`.

| Mục | Số liệu |
|---|---|
| Token thắng tham gia | 2: `89iR`, `CUg7` |
| Mua | 4 lệnh.<br>`89iR`: 12/11 13:29, vốn hóa 275.873, **sau đỉnh**.<br>`CUg7`: 17/11 01:40, vốn hóa 112.246 |
| Bán | 2 lệnh.<br>`89iR`: giữ 22 phút, −0,375 SOL.<br>`CUg7`: giữ 9,6 phút, −0,505 SOL |
| Swap thực tế/ngày | **224** (202–242). 96 swap mẫu trên 73 token |
| Đã loại | 24/120 |
| Nguồn vốn | Giao dịch đầu tiên 29/09/2026: nhận **7,89 SOL từ `F7p3dFrj…`**. Ví nguồn là ví nóng hạ tầng (mục 2.7): đây là quan hệ nạp/rút qua dịch vụ, **không chứng minh cùng chủ** |
| Dấu hiệu | Một số giao dịch có đồng ký của khóa `AxiomRYA…` (nền tảng giao dịch Axiom) |
| Win rate, PnL | 0/2 vị thế xác minh, **−0,880 SOL** |

### 2.7 `F7p3dFrjRTbtRp8FRF6qHLomXbKRBzpvBLjtQcfcgmNe` – **BOT / ví nóng hạ tầng** (tin cậy **cao**)

| Mục | Số liệu |
|---|---|
| Token thắng tham gia | 2: `9okx`, `EV7J` (3 lệnh "mua") |
| Bản chất | **39.136 giao dịch/ngày**, số dư **4.556,8 SOL**. Mẫu: 68 chuyển tiền + 49 giao dịch khác do ví ký, **0 swap mà token về ví này**. Lệnh mua trong pool là swap hộ (Jupiter/DFlow kèm Memo), token không về ví. Ví này cấp vốn cho `7kgWHsUp` |
| Kết luận | Ví nóng của sàn/dịch vụ, **không phải trader**. Loại |

### 2.8 `GZDRqVfpKLjTmDxf2Zh34WURCojSKbAoDJqxCi53m7ui` – **BOT** (tin cậy **cao**)

| Mục | Số liệu |
|---|---|
| Token thắng tham gia | 2: `BAKs`, `EV7J` |
| Mua / bán | 4 mua, 4 bán.<br>`BAKs`: mua 22/11 21:46, vốn hóa 126.715; bán sau **3,3 phút**, −0,038.<br>`EV7J`: mua 20/11 21:44, vốn hóa 105.296; bán sau 20,7 phút, +0,131 |
| Swap thực tế/ngày | **562** (517–593). **105/105 swap trả tip Jito**, 102 token khác nhau, 13/120 giao dịch lỗi |
| Đã loại | 15/120 (13 lỗi, 2 SOL gửi đến) |
| Win rate, PnL | 1/2 vị thế xác minh, **+0,093 SOL** |

### 2.9 `63JJwqP3YAqKB5FVwQotBsJdVJVJfLhKa8h9xmCTBvSq` – **NGHI VẤN** (tin cậy **trung bình**)

| Mục | Số liệu |
|---|---|
| Token thắng tham gia | 2: `BAKs`, `EV7J` |
| Mua | 3 lệnh.<br>`EV7J`: 21/11 00:43, vốn hóa 112.604, 29 giờ trước đỉnh.<br>`BAKs`: 22/11 22:20, vốn hóa 257.785, 2,7 giờ trước đỉnh 732.604 |
| Bán | 2 lệnh.<br>`EV7J`: giữ 4,5 giờ, **−0,217 SOL**.<br>`BAKs`: **còn giữ 137.245 token** (mua 0,511 SOL), chưa thấy bán |
| Swap thực tế/ngày | **88,6** (KTC 78–98): **dưới ngưỡng 100**. Nhưng 86 swap mẫu trải trên **74 token**, 49/86 swap trên bonding curve pump.fun |
| Đã loại | 34/120: 24 SOL gửi đến, 5 lỗi, 4 chuyển tiền, 1 khác |
| Nguồn vốn | Ví tạo 08/06/2026. Vốn đầu **4,165 SOL** từ `DdY2uJD62cTNsCmsvvLa59PXyWTEX8Wr17sqJcqPnJwM` (ví nay đã đóng). 29 giây sau nhận 0,00001 SOL từ `DdY1Zso4…`: địa chỉ giả mạo giống ví nguồn (**address poisoning**), không phải quan hệ |
| Dấu hiệu | Gần 1 token mới cho mỗi swap: kiểu săn token mới / lướt nhanh. 14/86 swap có tip Jito |
| Win rate, PnL | 0/1 vị thế xác minh, −0,217 SOL. **Không có bằng chứng lãi** |

### 2.10 Quan hệ giữa các ví

- **Không có giao dịch nào** do một ví ký mà có mặt ví khác trong 9 ví.
- Không có cặp nào trong 9 ví mua cùng token trong vòng 2 slot.
- Các giao dịch "trùng" đều do ví spam `HckQ93Xqjjo8mwt5pNPWvyCTZXQZ858rvzmm7ZRrZg9t` ký. Ví này gửi rác tới hầu hết các ví (18–89/120 giao dịch mẫu).
- Quan hệ cấp vốn duy nhất: `F7p3dFrj` (ví nóng) → `7kgWHsUp`.

### 2.11 Bốn ví bị gắn cờ bot ngay từ Giai đoạn 1 (đối chiếu thêm)

| Ví | Swap thực tế/ngày | Ghi chú |
|---|---|---|
| `3vKC1m1CgNL3AU27NTYZ93zfpPeFV9JSAV9k9oMuD4HW` | 5.934 | 48/60 mẫu là swap, 52 token |
| `sssssv2ivFNk1ogpBnEkcijSHzvib5vKfhiJMhRT2NT` | 11.910 | Địa chỉ vanity |
| `kEFiAX3jo5NmemysQov342TZ9mGh6yp92GDRjhA8XDf` | 8.755 | |
| `ARu4n5mFdZogZAravu7CcizaojWnS6oqka37gdLT5SZn` | 0 tự ký | Tài khoản dùng chung: 96 khóa ký qua chương trình `proVF4pM…`, khoảng 230.000 giao dịch/ngày |

Cả 4 ví xác nhận là bot hoặc hạ tầng.

---

## 3. KIỂM CHỨNG CHẤT LƯỢNG DỮ LIỆU

| Nội dung | Kết quả |
|---|---|
| Token đã định giá | **12/12.**<br>10 token định giá bằng SOL.<br>`82Z6CcE5` bằng USDC (sửa lệch 1.000 lần do số thập phân).<br>`89iRVoDV` bằng mihao (giá GeckoTerminal 5 phút × giá SOL theo giờ) |
| Giao dịch vị thế đối chiếu trên chain | **70** giao dịch của 9 ví. **57** khớp hoàn toàn: ví ký, số token khớp ≤ 1%, đúng chiều mua/bán. 13 không khớp: 10 của `6TYD` (ví không ký) và 3 của `F7p3` (token không về ví). **Bộ giải mã đúng 57/57** ở các giao dịch ví tự ký và tự nhận token |
| Giao dịch mẫu phân loại | 1.080 (9 ví × 120) + 240 (4 ví cờ bot × 60). Tổng khoảng 2.230 lần gọi RPC công khai |
| Vị thế đủ điểm mua và bán | **12/21.** Điều kiện: số dư token = 0 trước lệnh đầu và = 0 sau lệnh cuối, mọi lệnh khớp |
| PnL đáng tin cậy | 12 vị thế: tổng **+0,979 SOL**, 4 thắng / 8 thua (33%). Bỏ 1 lệnh lớn nhất (+3,098) thì tổng **−2,119 SOL** |
| PnL chưa thể kết luận | 9 vị thế:<br>2 bán cả token mua trước khung (`96JT`, `8zkg` ở `82Z6`).<br>2 còn giữ token (`4oFk` `82Z6`, `63JJ` `BAKs`).<br>5 của tài khoản hạ tầng (`6TYD`, `F7p3`) |
| Sai lệch còn lại | Có 4 nguồn sai lệch: (a) lịch sử bị cắt, (b) quy đổi USDC/mihao, (c) tiền thuê tài khoản token, (d) mẫu nhỏ. Chi tiết bên dưới |

**Chi tiết các sai lệch còn lại:**
- **(a) Lịch sử bị cắt.** Chỉ đọc vị thế trên token thắng trong khung 100–300k. Lãi/lỗ trên các token khác của ví (hàng nghìn lệnh) **chưa có**.
- **(b) Quy đổi USDC/mihao.** Ra SOL theo giá 5 phút/giờ, sai số ước tính ±1–2%.
- **(c) Tiền thuê tài khoản token** (khoảng 0,002 SOL) có thể nằm trong dòng tiền.
- **(d) Mẫu nhỏ.** 120 giao dịch/ví, nên số swap/ngày có khoảng tin cậy (đã ghi rõ).
- **(e) Nguồn vốn.** 6/9 ví không đọc được tới giao dịch đầu tiên trong giới hạn 30.000 chữ ký.

---

## 4. XẾP HẠNG ỨNG VIÊN SMART MONEY

Tiêu chí: (1) mua sớm trước tăng giá; (2) lãi thực sau phí; (3) lặp lại thành công trên nhiều token độc lập; (4) không bot hay thao túng; (5) copy được.

| Ví | (1) Mua sớm | (2) Lãi sau phí | (3) Lặp lại thành công | (4) Không bot | (5) Copy được | Xếp loại |
|---|---|---|---|---|---|---|
| `63JJwqP3…` | Có (27–29 h trước đỉnh) | **Không** (−0,217; 1 vị thế còn mở) | **Không** | Chưa chắc (88,6 swap/ngày, 74 token/86 swap) | Khó | **Không phù hợp hiện tại** |
| `4oFkqkrG…` | Có (91–151 h) | **Không** (−0,139; 1 vị thế còn mở) | **Không** | Chưa chắc (92/ngày, 100% tip Jito) | Khó | **Không phù hợp hiện tại** |
| `GZDRqVfp…` | Có | +0,093 (không đáng kể) | Không | **Không** (562/ngày) | Không | Không phù hợp |
| `96JTtvBj…` | Có | +3,048 (76% từ 1 lệnh) | Một phần | **Không** (4.458/ngày) | Không | Không phù hợp |
| `5t4Tz7qe…`, `8zkgFGVZ…`, `7kgWHsUp…` | Có | **Lỗ** | Không | **Không** | Không | Không phù hợp |
| `6TYDxGmV…`, `F7p3dFrj…` | – | – | – | Hạ tầng | Không | Không phù hợp |

| Phân loại | Số ví |
|---|---|
| **Đủ bằng chứng để theo dõi** | **0** |
| **Có tiềm năng nhưng cần thêm dữ liệu** | **0**: không ví nào có dù chỉ một bằng chứng lãi lặp lại. Hai ví NGHI VẤN không bị loại vì là bot chắc chắn, nhưng cũng **không có dấu hiệu tích cực** |
| **Không phù hợp** | **9** |

---

## 5. KẾT LUẬN VÀ ĐỀ XUẤT

- **Đã tìm được Smart Money thực sự chưa?** **Chưa.** Không ví nào đạt đủ 5 tiêu chí.
- **Ví nào đáng theo dõi nhất?** Không có ví nào đáng theo dõi. Ví "ít xấu nhất" là `63JJwqP3…`: swap dưới 100/ngày, ví lâu năm, nguồn vốn rõ. Nhưng vị thế duy nhất xác minh được thì lỗ, và hành vi là lướt rất nhiều token mới, nên **không đủ cơ sở** để gọi là Smart Money.
- **Có thể đưa ví nào vào Radar để phát tín hiệu thử nghiệm?** **Không.** Đưa vào sẽ tạo tín hiệu dựa trên bot hoặc ví lỗ.
- **Hạn chế còn tồn tại:**
  1. Chỉ có lãi/lỗ trên các token thắng. Muốn chấm toàn bộ một ví phải đọc lịch sử đầy đủ: với các ví này là hàng chục nghìn giao dịch.
  2. Phương pháp tìm ví từ "người mua lặp lại trên token thắng" thiên về bot, vì bot có mặt ở mọi token. Kết luận này lặp lại ở cả 3 lần khảo sát: bonding curve, PumpSwap, và lần kiểm chứng này.
  3. Khoảng 2.050 ví chỉ mua 1 lần trên 1 token chưa được ghép theo nguồn vốn, vì cần đọc giao dịch đầu tiên của từng ví.
  4. 5 token không có dữ liệu bonding curve, nên chưa lọc được dev/sniper ở đó.

---

## 6. XÁC NHẬN AN TOÀN VÀ CHI PHÍ

| Nội dung | Xác nhận |
|---|---|
| Helius credit dùng trong phần C | **0**. Bộ đếm nội bộ ngày 23/11 vẫn **106.878**, không đổi trước và sau phần C |
| Helius credit A+B đến nay | **5.340 / 16.000** (Giai đoạn 1: 5.340 / 6.000). Không vượt ngân sách |
| API trả phí, VPS, chi phí khác | **Không.** Chỉ dùng RPC công khai miễn phí của Solana (khoảng 2.230 lần gọi) và GeckoTerminal miễn phí (khoảng 16 lần gọi) |
| Schema, scoring, PnL, trading | **Không thay đổi.** `trading_enabled = false`. Chỉ thêm script nghiên cứu đọc dữ liệu |
| API key | Không xuất hiện trong file nào (kiểm tra rò rỉ: 0) |

---

## 7. FILE ĐÍNH KÈM ĐỂ ĐỐI CHIẾU

| File | Nội dung |
|---|---|
| `Doi chieu 9 vi.xlsx` | Sheet **Vi**: số liệu và kết luận 9 ví. Sheet **Vi the**: 21 vị thế. Sheet **Giao dich**: 70 giao dịch với chữ ký đầy đủ, số token giải mã và trên chain, dòng tiền, phí |
| `winner_data/pumpswap/onchain/analysis.json` | Toàn bộ kết quả phân tích |
| `winner_data/pumpswap/onchain/<ví>.json.gz` | Dữ liệu thô từ RPC công khai: chữ ký, mẫu giao dịch, giao dịch vị thế |
| `winner_data/pumpswap/phase1b_result.json` | Phễu 12 token sau khi định giá lại `89iRVoDV` |
| `winner_data/pumpswap/mihao_usd_5m.json`, `sol_usd_hourly.json`, `pool_quotes.txt` | Dữ liệu giá dùng để định giá |
| Script | `public_rpc_collect.py` (thu thập, không gọi Helius), `onchain_analyze.py` (phân tích offline), `phase1_analysis_b.py` |
