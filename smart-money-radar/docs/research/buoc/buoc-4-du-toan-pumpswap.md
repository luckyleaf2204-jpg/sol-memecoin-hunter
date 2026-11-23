# BƯỚC 4 – DỰ TOÁN ĐỌC PUMPSWAP SAU TỐT NGHIỆP: DỪNG VÌ VƯỢT NGÂN SÁCH

**Ngày:** 23/11/2026.
**Trạng thái:** DỪNG theo yêu cầu "dự toán vượt thì dừng và báo cáo". **Chưa đọc lịch sử giao dịch nào.**

## 1. Đã làm

| Việc | Kết quả |
|---|---|
| Khung vốn hóa 100.000–300.000 USD sau tốt nghiệp, trong 24 giờ đầu (giá 15 phút, miễn phí) | **17/24** token có đi qua vùng này; 7 token không (tốt nghiệp đã vượt 300.000 USD, hoặc không bao giờ chạm 100.000 USD) |
| Đếm chữ ký giao dịch của pool trong khung giờ | **2.239.177** chữ ký trên 17 pool |
| Dự toán đọc toàn bộ | **224.000 credit**, vượt xa phần còn lại ⇒ **dừng** |

## 2. Sai sót của tôi ở bước đếm: 28.150 credit

Bước đếm đã tốn **28.150 credit**, nhiều hơn mức cần thiết rất nhiều. Hai lỗi thiết kế:

1. **Không đặt trần chi phí đếm cho từng pool.** 5 pool có lưu lượng bot cực lớn (4.000–20.000 giao dịch/phút), riêng việc đếm đã tốn 3.300–9.400 credit mỗi pool.
2. **Đếm từ lúc tạo pool** thay vì từ đầu khung giờ, nên quét qua cả giao dịch nằm trước khung. Ví dụ `89iRVoDV`: 253 lần gọi cho 7.066 chữ ký nằm trong khung.

**Đã sửa:** bước đếm có trần 5 lần gọi (50 credit) mỗi pool. Pool vượt trần được đánh dấu "quá lớn" và không đọc. Có test (111 passed).

Ngân sách vẫn được tôn trọng (bộ chặn trần hoạt động), nhưng phần credit này lẽ ra đã tiết kiệm được.

## 3. Kết quả đếm từng token

| Token | Phút trong vùng | Chữ ký | Giao dịch/phút | Credit đọc ước tính |
|---|---|---|---|---|
| 6bUb1jX4 | 45 | 160 | 4 | 20 |
| ngwxpGYd | 120 | 493 | 4 | 50 |
| 82Z6CcE5 | 1.065 | 887 | 1 | 90 |
| 9okxCGsu | 195 | 1.149 | 6 | 120 |
| 8rjgC1Gs | 930 | 1.858 | 2 | 190 |
| VAYMViku | 285 | 3.306 | 12 | 340 |
| 4npJ3avx | 45 | 3.439 | 76 | 350 |
| BAKsQryn | 60 | 5.743 | 96 | 580 |
| dkpuQdje | 240 | 6.939 | 29 | 700 |
| 89iRVoDV | 180 | 7.066 | 39 | 710 |
| EV7J2QaP | 1.305 | 9.575 | 7 | 960 |
| CUg7AzWL | 960 | 11.395 | 12 | 1.140 |
| **12 token thường – cộng** | | **51.930** | | **≈ 5.250** |
| D7MyAD7k | 1.365 | 111.620 | 82 | 11.170 |
| 7R4MP6VX | 60 | 240.044 | 4.001 | 24.010 |
| FuKY3dk7 | 60 | 331.809 | 5.530 | 33.190 |
| FVzqGqRm | 1.110 | 564.641 | 509 | 56.470 |
| B34nXKzC | 45 | 939.053 | 20.868 | 93.910 |

**Nhận xét:** 5 token cuối có hàng nghìn đến hàng chục nghìn giao dịch mỗi phút. Đó là bot/wash trading, không thể là người giao dịch thủ công. Không nên tốn credit đọc toàn bộ.

## 4. Ngân sách

| Mục | Credit |
|---|---|
| Đã dùng trước bước 4 | 19.416 + khoảng 51 (lấy mẫu bước 1, đối chiếu bước 2) |
| Đếm bước 4 | 28.150 |
| **Tổng đã dùng / trần** | **47.617 / 77.760** |
| **Còn lại** | **30.143** |

## 5. Đề xuất (cần anh duyệt)

| Phương án | Nội dung | Chi phí tối đa |
|---|---|---|
| **A (đề xuất)** | Đọc **12 token thường**. Dự toán chính xác là 5.250 credit, vì số chữ ký đã đếm xong. Tìm ví mua 100.000–300.000 USD (≥ 0,5 SOL). Lọc bot rẻ (1 credit/ví). Ví mua lặp lại trên ≥ 2 token: kiểm chứng toàn bộ lịch sử (khoảng 300 credit/ví, thắng/thua/phí). | **Trần 16.000** (5.250 đọc + khoảng 10.000 kiểm chứng + dự phòng) |
| B | Bỏ hẳn 5 token lưu lượng bot | 0 |
| C | Dừng tại đây | 0 |

Phương án A + B dùng tối đa 16.000 trên 30.143 credit còn lại.
