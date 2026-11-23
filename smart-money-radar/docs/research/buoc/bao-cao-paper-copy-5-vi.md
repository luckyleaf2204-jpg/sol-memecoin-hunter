# BÁO CÁO PAPER TRADE – KIỂM CHỨNG KHẢ NĂNG COPY 5 VÍ

**Ngày:** 23/11/2026 (giờ UTC).

**Phương pháp:**
- Replay 25 vị thế đã đóng gần nhất của mỗi ví (dữ liệu on-chain thật).
- Theo dõi trực tiếp 5 ví để đo độ trễ phát hiện thật.
- Không phát lệnh thật, chỉ ghi nhận paper trade.

**Chi phí:** 0 Helius credit. Chỉ dùng RPC công khai Solana (khoảng 6.500 lần gọi cho replay, cộng phần theo dõi trực tiếp).

> **Ví B8bv:** địa chỉ anh/chị gửi bị thiếu 1 ký tự ("…TEX7s**g**wwmS"). Tôi dùng địa chỉ đầy đủ đã kiểm chứng trong báo cáo leaderboard: `B8bvkQ7UqykUXUkVSdxngFtFBnonHf4sJTEX7s3gwwmS`.

---

## 1. KẾT LUẬN

| Ví | Ví gốc (25 vị thế, on-chain) | Paper copy trễ 1 giây | Paper copy trễ 5 giây | Kết luận |
|---|---|---|---|---|
| `3kxcF8wHKm4sEtnxjXXeUvNGeDjTmo47LgtSRg71YfG5` | +8,27 SOL, WR 84% | **+2,09 SOL**, WR 67%, ROI trung bình +17%/lệnh | **+2,20 SOL**, WR 72% | **Duy nhất có lãi sau độ trễ**. Độ tin cậy **trung bình**: sàn Meteora, giá copy ước tính từ giao dịch thật kế tiếp, không mô hình được thanh khoản |
| `7Q8rvHKi1brA49epwVyinrqX6rMKZxZqW1FoUgpXTpTs` | +2,24 SOL, WR 56% | −0,42 SOL, WR 43% | −0,38 SOL, WR 42% | **Không copy có lãi**: lỗ nhẹ, ổn định |
| `ArF3BG43u4xakKDGQmyzcaLuxzZcY1L6SC8K6Miy4maz` | +0,13 SOL, WR 76% | −0,78 SOL, WR 40% | −0,81 SOL, WR 36% | **Không copy có lãi**: lãi của ví quá mỏng, mất hết vào phí và trượt giá |
| `B8bvkQ7UqykUXUkVSdxngFtFBnonHf4sJTEX7s3gwwmS` | +12,79 SOL, WR 52% | −0,89 SOL, WR 15%; **12/25 lệnh không copy được** | −0,86 SOL | **Không copy được**: giá chạy > 25% ngay sau khi ví mua |
| `BvtZeURZMcvvQVBr5k9jDT53E2xuFq2Da6d6RTDP7gM4` | +5,63 SOL, WR 68% | **−2,09 SOL**, WR 18%, ROI −19%/lệnh | −2,07 SOL | **Không copy được**: người copy thành thanh khoản thoát cho ví gốc |

**Trả lời mục tiêu:**
- Trong 5 ví, chỉ `3kxcF8wH…` cho paper trade có lãi sau độ trễ, phí và trượt giá. Mức tin cậy chưa cao (mục 4).
- 4 ví còn lại: PnL của ví gốc dương nhưng **người copy lỗ**. Đây là bằng chứng rõ rằng **không thể đánh đồng PnL của ví gốc với lợi nhuận copy**.
- `BvtZ…` (ưu tiên 1 trước đây) là ví tệ nhất để copy.

---

## 2. KẾT QUẢ CHI TIẾT TỪNG VÍ

**Giả định chung:**
- Mỗi lệnh copy mua cố định 0,5 SOL.
- Bán theo đúng tỷ lệ ví gốc bán.
- Phí mạng của người copy 0,000105 SOL/giao dịch.
- Phí sàn và trượt giá tính từ trạng thái pool/curve thật.
- Copy bị từ chối nếu:
  - giá copy cao hơn giá ví mua > 25%; hoặc
  - lệnh 0,5 SOL đẩy giá > 10%; hoặc
  - ví đã bán trước khi lệnh copy kịp vào.

### 2.1 `3kxcF8wHKm4sEtnxjXXeUvNGeDjTmo47LgtSRg71YfG5`

**Giao dịch đã theo dõi:**
- 300 giao dịch: 149 swap ví tự ký, 97 không do ví ký (spam/chuyển đến), 8 lỗi, 46 tự ký khác.
- 146/149 swap trên Meteora (DAMM v2/DBC).

| Trễ | Tín hiệu mua | Copy được | Bắt kịp | Paper PnL (SOL) | WR | ROI/lệnh | Drawdown | Giá copy cao hơn ví (trung vị) |
|---|---|---|---|---|---|---|---|---|
| 1 giây | 25 | 24 | 96% | **+2,09** | 66,7% | +17,4% | −0,53 | +2,2% |
| 3 giây | 25 | 25 | 100% | **+2,10** | 68,0% | +16,8% | −0,47 | +2,2% |
| 5 giây | 25 | 25 | 100% | **+2,20** | 72,0% | +17,6% | −0,47 | +0,9% |
| 10 giây | 25 | 25 | 100% | +2,77* | 76,0% | +22,2% | −0,46 | 0% |

**Không copy được:** 1 lệnh ở trễ 1 giây (giá đã tăng quá ngưỡng).

**\* Độ tin cậy giảm khi trễ tăng.** Chỉ tính các vị thế có cả giá vào lẫn giá ra lấy từ giao dịch thật:

| Trễ | Số vị thế | Paper PnL | WR | ROI/lệnh |
|---|---|---|---|---|
| 1 giây | 22 | +2,11 SOL | 68% | +19% |
| 3 giây | 13 | +1,52 SOL | 54% | — |
| 5 giây | 10 | +1,31 SOL | 60% | — |
| 10 giây | 3 | không đủ mẫu | — | — |

**Đánh giá:**
- Lãi copy vẫn dương ở mọi mức trễ, lệnh lớn nhất chiếm ≤ 40% tổng lãi.
- Ví vào khoảng 1 SOL/lệnh, giữ trung vị 49 giây.
- Đã biết có bot khác copy ví này chậm khoảng 2 giây (báo cáo leaderboard).

### 2.2 `7Q8rvHKi1brA49epwVyinrqX6rMKZxZqW1FoUgpXTpTs`

**Giao dịch đã theo dõi:** 300 giao dịch, 173 swap tự ký (37 ở sàn khác), 46 không do ví ký.

**Lưu ý vốn:** vào 331,7 SOL cho 25 vị thế (khoảng 13 SOL/lệnh), lớn hơn nhiều so với lệnh copy 0,5 SOL.

| Trễ | Copy được | Bắt kịp | Paper PnL (SOL) | WR | ROI/lệnh | Drawdown |
|---|---|---|---|---|---|---|
| 1 giây | 23/25 | 92% | −0,42 | 43% | −3,7% | −0,49 |
| 3 giây | 23/25 | 92% | −0,32 | 43% | −2,8% | −0,43 |
| 5 giây | 24/25 | 96% | −0,38 | 42% | −3,2% | −0,38 |
| 10 giây | 24/25 | 96% | −0,49 | 38% | −4,1% | −0,53 |

**Không copy được:** 1 lệnh thiếu dữ liệu giá; 1 lệnh giá đã tăng quá ngưỡng (trễ 1–3 giây).

**Nhận xét:** ví gốc lãi chỉ +0,7% trên vốn (2,24/331,7 SOL). Copy trễ mất phần này cộng phí, nên **không có biên lợi nhuận để copy**.

### 2.3 `ArF3BG43u4xakKDGQmyzcaLuxzZcY1L6SC8K6Miy4maz`

**Giao dịch đã theo dõi:** 300 giao dịch, 246 swap tự ký (đều PumpSwap), 54 không do ví ký.

| Trễ | Copy được | Paper PnL (SOL) | WR | ROI/lệnh | Drawdown |
|---|---|---|---|---|---|
| 1 giây | 25/25 | −0,78 | 40% | −6,2% | −0,80 |
| 3 giây | 25/25 | −0,78 | 36% | −6,2% | −0,81 |
| 5 giây | 25/25 | −0,81 | 36% | −6,5% | −0,84 |
| 10 giây | 25/25 | −0,82 | 36% | −6,5% | −0,85 |

**Nhận xét:** ví gốc +0,13 SOL trên 7 SOL vốn (+1,8%), WR 76% nhưng lãi mỗi lệnh rất nhỏ. Người copy mua đắt hơn khoảng 6% (trung vị), nên lỗ đều.

### 2.4 `B8bvkQ7UqykUXUkVSdxngFtFBnonHf4sJTEX7s3gwwmS`

**Giao dịch đã theo dõi:** 300 giao dịch, 135 swap tự ký, **107 giao dịch lỗi do ví ký** (thử lệnh qua terminal), 34 không do ví ký.

| Trễ | Copy được | Bắt kịp | Paper PnL (SOL) | WR | ROI/lệnh |
|---|---|---|---|---|---|
| 1 giây | 13/25 | 52% | −0,89 | 15% | −13,6% |
| 3 giây | 13/25 | 52% | −0,87 | 15% | −13,4% |
| 5 giây | 12/25 | 48% | −0,86 | 17% | −14,3% |
| 10 giây | 12/25 | 48% | +0,95** | 25% | +15,9% |

**Không copy được:** 12–13/25 lệnh vì giá đã tăng > 25% so với giá ví mua (giá copy trung vị cao hơn 23–25%).

**\*\*** Kết quả dương ở trễ 10 giây đến từ **1 lệnh duy nhất**: ví lãi +480%, copy +369%. Lệnh này lọt vào vì giá copy cao hơn 20,7% (dưới ngưỡng 25%); ở trễ 5 giây nó bị loại. **Không đáng tin.**

**Nhận xét:** ví mua sớm trên bonding curve, giá chạy ngay trong vài slot. Lãi của ví (+12,8 SOL) đến từ việc vào trước, **người copy không lấy được**.

### 2.5 `BvtZeURZMcvvQVBr5k9jDT53E2xuFq2Da6d6RTDP7gM4`

**Giao dịch đã theo dõi:** 300 giao dịch, 146 swap tự ký, 91 không do ví ký, 50 tự ký khác.

| Trễ | Copy được | Bắt kịp | Paper PnL (SOL) | WR | ROI/lệnh | Drawdown |
|---|---|---|---|---|---|---|
| 1 giây | 22/25 | 88% | −2,09 | 18% | −19,0% | −2,28 |
| 3 giây | 22/25 | 88% | −2,07 | 18% | −18,8% | −2,26 |
| 5 giây | 22/25 | 88% | −2,07 | 18% | −18,8% | −2,26 |
| 10 giây | 20/25 | 80% | −1,81 | 20% | −18,1% | −1,99 |

**Không copy được:** 2–3 lệnh giá đã tăng quá ngưỡng; 1–3 lệnh ví đã bán trước.

**Nguyên nhân đã xác minh trên chain** (ví dụ 1 vị thế):
- **Vào lệnh:** ví mua ở giá 32,3; 1 slot sau, các ví khác mua dồn và giá lên 34,9–35,2 (+8–10%).
- **Thoát lệnh:** ngay trong cùng slot ví bán, thêm 8 lệnh bán khác xả theo, giá rơi từ 48 xuống 30 (−40%).

Ví này có lãi vì đi trước đám copy bot. **Người copy thành thanh khoản thoát.**

---

## 3. THEO DÕI TRỰC TIẾP (ĐANG CHẠY)

| Chỉ số | Giá trị |
|---|---|
| Giao dịch mới ghi nhận | 25 (16 swap ví tự ký, 5 không do ví ký) |
| Độ trễ phát hiện qua RPC công khai | Trung vị **5,0 giây**, p90 **8,0 giây** |
| Phân bổ | `ArF3` 14, `BvtZ` 2, 3 ví còn lại chưa giao dịch trong lúc theo dõi |

**Ý nghĩa:** với hạ tầng miễn phí hiện tại, mức trễ **thực tế là 5–10 giây**. Mức 1–3 giây chỉ đạt được với WebSocket/RPC tốc độ cao, tức là trả phí.

Ở mức 5–10 giây, chỉ `3kxc…` còn có lãi trong mô phỏng.

---

## 4. CHẤT LƯỢNG MÔ HÌNH VÀ HẠN CHẾ

| Nội dung | Kết quả |
|---|---|
| Thanh khoản ảo PumpSwap | Phát hiện và đo được **17,584 SOL** trên các pool mới tốt nghiệp, ổn định qua mọi giao dịch của pool. Đã đưa vào mô hình |
| Số dư pool trong sự kiện PumpSwap | Là số dư **trước** lệnh: khớp 2.149/2.150 cặp giao dịch liên tiếp |
| Kiểm tra mô hình giá **ngoài mẫu** (dự đoán lệnh mua thật của người khác) | Sai số trung vị 0,006–0,019%, p90 ≤ 0,2%, trên 1.227 lệnh thật (`BvtZ` 309, `7Q8r` 75, `ArF3` 802, `B8bv` 41) |
| pump.fun bonding curve | Dùng dự trữ ảo trong sự kiện, chính xác |
| Sàn khác (Meteora/Raydium) | Giá vào/ra = giá khớp thật của giao dịch kế tiếp sau độ trễ. **Không mô hình được tác động giá** của lệnh 0,5 SOL. Khi không có giao dịch trong cửa sổ, dùng giá của ví (được đánh dấu) |
| Mẫu | 25 vị thế/ví, trong 1–5 ngày gần nhất. Đủ để thấy xu hướng, **chưa đủ** để kết luận chắc chắn cho `3kxc` |
| Chưa mô phỏng | Lệnh copy thất bại do trượt giá, tranh slot với bot khác, phí ưu tiên cao hơn khi tranh lệnh. Các yếu tố này làm kết quả **xấu hơn**, không tốt hơn |

---

## 5. TRIỂN KHAI TRÊN HỆ THỐNG

| Nội dung | Trạng thái |
|---|---|
| Công cụ | `radar/tools/paper_copy.py`. Dùng lại bộ giải mã sự kiện pump.fun/PumpSwap và hàm PDA của hệ thống. Chế độ `replay` / `live` / `report` |
| Lưu trữ | File JSON trong `winner_data/paper/`. **Không** tạo bảng, **không** sửa schema, scoring, PnL |
| Giao dịch | Không phát tín hiệu, không gửi lệnh; `trading_enabled = false` |
| Phân loại giao dịch | Swap ví tự ký / không do ví ký (spam, hộ, chuyển đến) / tự ký khác (transfer) / lỗi / swap ở sàn khác |
| Theo dõi trực tiếp | Đang chạy đến khoảng 21:40 UTC hôm nay. Chạy lại bằng `python -m radar.tools.paper_copy live ../winner_data/paper <số giờ>` |
| Hệ thống Docker của Radar | Đang **dừng**: máy chủ khởi động lại, Docker daemon tắt. Tôi **chưa khởi động lại**, để tránh worker dùng Helius tiêu credit ngoài kế hoạch. Công cụ paper trade không phụ thuộc vào Docker |

---

## 6. XÁC NHẬN

| Nội dung | Xác nhận |
|---|---|
| Helius credit | **0**. Công cụ chỉ gọi `api.mainnet-beta.solana.com` (có kiểm tra cứng trong code) |
| Chi phí khác | Không mua API, không thuê VPS |
| Hệ thống | Không sửa schema, scoring, PnL; không bật trading; không thêm ví ngoài 5 ví đã duyệt |

**File đối chiếu `Paper copy 5 vi.xlsx`:**

| Sheet | Nội dung |
|---|---|
| **Tong hop** | 5 ví × 4 mức trễ |
| **Vi the** | 125 vị thế: chữ ký mua/bán, kết quả từng mức trễ, lý do không copy được |
| **Theo doi truc tiep** | Từng giao dịch phát hiện trực tiếp và độ trễ |
| **Gia dinh** | Các giả định mô phỏng |
