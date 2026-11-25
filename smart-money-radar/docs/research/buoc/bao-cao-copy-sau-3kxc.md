# BÁO CÁO KIỂM CHỨNG COPY TRADE VÍ 3kxcF8wH… (REPLAY SÂU, ĐỘ TRỄ 1/3/5/8/10 GIÂY)

Ngày lập: 25/11/2026. Ví: 3kxcF8wHKm4sEtnxjXXeUvNGeDjTmo47LgtSRg71YfG5. Khoảng dữ liệu: 02/11/2026 – 23/11/2026 (1000 chữ ký giao dịch gần nhất của ví). Nguồn: RPC công khai Solana (api.mainnet-beta.solana.com), không dùng Helius credit, không mua API. Tổng số lệnh gọi RPC: 21.073 (lượt đọc chính 14.248 + đọc bổ sung cửa sổ giá 6.825); lượt tính lại cuối dùng checkpoint, không gọi mạng.

## I. KẾT LUẬN (TRẢ LỜI 5 CÂU HỎI)

| Câu hỏi | Trả lời |
|---|---|
| 1. Copy trade ví này có lãi không? | CÓ, trong mẫu 02/11/2026–23/11/2026: lãi ròng ở cả 5 mức trễ sau mọi chi phí; khoảng tin cậy 95% đều > 0. Nhưng lãi đến gần như hoàn toàn từ pool Meteora DAMM v2; pool Meteora DBC hòa/lỗ từ 5 giây trở lên. Lãi giảm mạnh theo tuần (xem mục V). |
| 2. Độ trễ nào có lãi ổn định? | ≤ 5 giây: ổn định (5 s: +11,14 SOL, CI95 4,12–18,98, bỏ 5 lệnh lãi nhất vẫn +5,62). 8 giây: còn lãi (+9,78, bỏ top 5 còn +3,94). 10 giây: lãi mỏng (bỏ top 5 còn +3,27). Độ trễ phát hiện thật đo trên RPC công khai: trung vị 5,16 s, p90 7,98 s, chưa cộng thời gian gửi lệnh → mức thực tế cần dùng là 8–10 giây, không phải 1–3 giây. |
| 3. PnL ròng sau toàn bộ chi phí? | Mức 8 giây (sát thực tế): +9,78 SOL trên 263 lệnh × 0,5 SOL (ROI 7,4%/lệnh). Mức 10 giây: +8,91 SOL. Nếu người copy phải trả thêm 0,003 SOL phí ưu tiên/Jito mỗi giao dịch: 8 s còn +8,07, 10 s còn +7,34 SOL. |
| 4. Drawdown tối đa và rủi ro lớn nhất? | Drawdown tối đa -3,78 SOL ở 8 s (-3,63 ở 10 s) ≈ 7–8 lệnh 0,5 SOL mất trắng liên tiếp. Rủi ro lớn nhất: lợi thế đang suy giảm – ROI copy theo tuần (8 s) 12,1% → 2,3% → 5,6%; số vị thế/tuần của ví giảm 140 → 117 → 39 → 6. Thứ hai: phụ thuộc đuôi phải (trung vị ROI/lệnh âm, win rate < 50%). |
| 5. Đủ điều kiện theo dõi Smart Money dài hạn? | ĐỦ ĐIỀU KIỆN THEO DÕI (watchlist + paper trade trực tiếp), CHƯA ĐỦ ĐIỀU KIỆN COPY TIỀN THẬT. Điều kiện nâng hạng: paper trade trực tiếp 2–4 tuần với độ trễ phát hiện thật, PnL 2 tuần trượt ở 8 s > 0 và ví duy trì ≥ 30 vị thế/tuần. Dừng theo dõi nếu 2 tuần liên tiếp PnL copy ≤ 0. |

## II. PHẠM VI DỮ LIỆU VÀ PHƯƠNG PHÁP

- Đọc 1000 chữ ký gần nhất của ví: 727 swap do ví tự ký; 155 giao dịch không do ví ký (spam/airdrop/chuyển vào); 46 giao dịch lỗi; 60 giao dịch tự ký khác; 12 giao dịch nhiều token (bỏ qua).
- Vị thế đã đóng: 310; định giá được: 302 (đạt yêu cầu ≥ 100); không định giá được: 8 (8 vị thế PumpSwap pool đảo chiều – token nằm ở phía quote, mô hình dự trữ không áp dụng; ví gốc lãi 8,38 SOL trên 8 vị thế này, KHÔNG tính vào kết quả copy). Vị thế còn mở cuối kỳ: 44 (không tính).
- Giữ nguyên cả lệnh lãi, lệnh lỗ và lệnh không copy được (không lọc kết quả đẹp).
- Lệnh copy: 0,5 SOL/lệnh mua đầu tiên của vị thế; bán theo đúng tỷ lệ ví gốc bán ở từng lệnh bán, mỗi lệnh bán cũng trễ đúng bằng độ trễ mô phỏng.
- Giá khớp: tính lại trên TRẠNG THÁI POOL THẬT tại slot (thời điểm lệnh ví + độ trễ; 1 slot = 0,4 s). Meteora DAMM v2/DBC: giải mã sự kiện EvtSwap2 của mọi giao dịch trong pool, mô hình thanh khoản tập trung (L, √giá), phí theo pool. Không dùng giá giao dịch kế tiếp làm giá copy.
- Kiểm chứng mô hình: chuỗi trạng thái pool Meteora dự đoán khớp trạng thái thật 18.715/18.719 cặp (99.98%).
- Cửa sổ giá: mỗi lệnh của ví đọc toàn bộ giao dịch của pool trong 25 slot (10 s) sau đó. 405 cửa sổ có > 30 giao dịch được đọc bổ sung (4.699 giao dịch) để mức 8/10 s cũng dùng trạng thái thật; 1 cửa sổ đọc bổ sung thất bại (ảnh hưởng 1 lệnh bán ở 8 s và 10 s – ghi nhận là ước tính).
- Chi phí người copy: phí mạng + phí ưu tiên + Jito tip đúng bằng ví gốc đã trả (trung vị 0,000028 SOL/vị thế – ví không trả Jito tip); phí sàn (phí pool) nằm trong mô hình khớp; trượt giá = tác động giá thật của 0,5 SOL lên pool. Kiểm tra độ nhạy thêm 0,001 và 0,003 SOL/giao dịch.
- Không khớp lệnh khi: giá đã tăng > 25% so với giá ví mua (ngưỡng đuổi giá); tác động giá của 0,5 SOL > 10% (thanh khoản mỏng); ví gốc đã bán trước khi lệnh copy vào. Độ nhạy ngưỡng đuổi giá 10% ở mục VI.

## III. KẾT QUẢ THEO ĐỘ TRỄ (302 TÍN HIỆU, 0,5 SOL/LỆNH, SAU MỌI CHI PHÍ)

| Chỉ tiêu | 1 giây | 3 giây | 5 giây | 8 giây | 10 giây |
|---|---|---|---|---|---|
| Lệnh copy được | 284/302 | 281/302 | 278/302 | 263/302 | 243/302 |
| Tỷ lệ thành công (khớp được) | 94,0% | 93,0% | 92,1% | 87,1% | 80,5% |
| Lệnh khớp dùng trạng thái pool thật | 284/284 | 281/281 | 278/278 | 262/263 | 242/243 |
| PnL ròng (SOL) | 15,05 | 12,54 | 11,14 | 9,78 | 8,91 |
| Vốn quay vòng (SOL) | 142,0 | 140,5 | 139,0 | 131,5 | 121,5 |
| ROI bình quân/lệnh | 10,6% | 8,9% | 8,0% | 7,4% | 7,3% |
| ROI trung vị/lệnh | -0,2% | -0,6% | -0,6% | -1,0% | -0,6% |
| Win rate | 49,6% | 48,4% | 47,5% | 46,0% | 48,1% |
| Drawdown tối đa (SOL) | -2,95 | -3,18 | -3,35 | -3,78 | -3,63 |
| Lệnh lỗ nặng nhất (SOL) | -0,45 | -0,45 | -0,50 | -0,50 | -0,50 |
| Khoảng tin cậy 95% PnL (SOL) | 7,67 – 23,24 | 5,32 – 20,41 | 4,12 – 18,98 | 2,36 – 18,09 | 1,90 – 16,66 |
| Xác suất PnL ≤ 0 (bootstrap) | 0,0% | 0,0% | 0,0% | 0,2% | 0,4% |
| Giá copy cao hơn giá ví (trung vị) | 6,6% | 7,4% | 7,6% | 7,6% | 8,4% |
| Tác động giá 0,5 SOL (trung vị) | 4,3% | 4,3% | 4,2% | 4,1% | 3,7% |
| Không khớp: giá tăng quá 25% | 11 | 15 | 18 | 26 | 38 |
| Không khớp: ví gốc bán trước | 2 | 2 | 3 | 10 | 17 |
| Không khớp: thanh khoản mỏng (>10%) | 5 | 4 | 3 | 3 | 4 |

Đối chiếu ví gốc cùng 302 vị thế: PnL on-chain +176,30 SOL trên 527,9 SOL vốn vào, win rate 74,8%, thời gian giữ trung vị 35 giây. Người copy chỉ giữ lại khoảng 5–8% lợi nhuận/vốn của ví vì vào sau và phải chịu tác động giá của chính lệnh mình.

## IV. PHÂN TÍCH THEO SÀN

| Sàn | Vị thế | PnL ví gốc (SOL) | Copy 1 s: PnL (lệnh) | Copy 3 s: PnL (lệnh) | Copy 5 s: PnL (lệnh) | Copy 8 s: PnL (lệnh) | Copy 10 s: PnL (lệnh) |
|---|---|---|---|---|---|---|---|
| Meteora DAMM v2 | 222 | 152,61 | 13,70 (205) | 11,74 (203) | 11,25 (200) | 10,74 (187) | 9,75 (172) |
| Meteora DBC (bonding curve) | 80 | 23,69 | 1,35 (79) | 0,80 (78) | -0,11 (78) | -0,96 (76) | -0,84 (71) |
| PumpSwap (pool đảo chiều) | 8 | 8,38 | không định giá | không định giá | không định giá | không định giá | không định giá |

Toàn bộ lợi nhuận copy đến từ DAMM v2. DBC: chỉ dương ở 1–3 giây, từ 5 giây trở lên âm (CI95 ở 8 s: -3,34 – 1,23). Không có vị thế pump.fun curve hay PumpSwap thường trong mẫu; nhóm 'sàn khác' bằng 0.

## V. MỨC ĐỘ PHỤ THUỘC VÀO MỘT VÀI LỆNH LÃI LỚN VÀ ĐỘ ỔN ĐỊNH THEO THỜI GIAN

| Chỉ tiêu | 1 giây | 3 giây | 5 giây | 8 giây | 10 giây |
|---|---|---|---|---|---|
| PnL ròng | 15,05 | 12,54 | 11,14 | 9,78 | 8,91 |
| Bỏ 1 lệnh lãi nhất | 13,27 | 11,05 | 9,77 | 8,05 | 7,30 |
| Bỏ 3 lệnh lãi nhất | 10,49 | 8,41 | 7,10 | 5,51 | 4,87 |
| Bỏ 5 lệnh lãi nhất | 9,00 | 6,92 | 5,62 | 3,94 | 3,27 |
| Tỷ trọng 3 lệnh lãi nhất | 30,3% | 33,0% | 36,3% | 43,7% | 45,3% |

Kết quả vẫn dương khi bỏ 5 lệnh lãi lớn nhất ở mọi độ trễ, nhưng ở 8–10 giây 5 lệnh này chiếm khoảng 60–63% lợi nhuận.

| Tuần | Bắt đầu | Vị thế ví | PnL ví gốc | Copy 5 s: PnL | Copy 5 s: ROI/lệnh | Copy 8 s: PnL | Copy 8 s: ROI/lệnh |
|---|---|---|---|---|---|---|---|
| 2026-W45 | từ 02/11/2026 | 140 | 103,3 | 7,27 | 12,0% | 6,74 | 12,1% |
| 2026-W46 | từ 09/11/2026 | 117 | 60,0 | 1,84 | 3,3% | 1,23 | 2,3% |
| 2026-W47 | từ 16/11/2026 | 39 | 9,6 | 1,21 | 6,4% | 1,07 | 5,6% |
| 2026-W48 | từ 23/11/2026 | 6 | 3,4 | 0,83 | 27,6% | 0,74 | 24,6% |

Tuần 45 tạo ra phần lớn lợi nhuận; tuần 46 ROI chỉ còn khoảng 2–3%/lệnh; hoạt động của ví giảm mạnh từ tuần 47 (tuần 48 mới có 1 ngày dữ liệu, 6 vị thế – không đủ để kết luận).

## VI. ĐỘ NHẠY CHI PHÍ VÀ NGƯỠNG ĐUỔI GIÁ

| Kịch bản | 1 giây | 3 giây | 5 giây | 8 giây | 10 giây |
|---|---|---|---|---|---|
| Cơ sở (phí bằng ví gốc) | 15,05 | 12,54 | 11,14 | 9,78 | 8,91 |
| +0,001 SOL phí ưu tiên/Jito mỗi giao dịch | 14,44 | 11,94 | 10,55 | 9,21 | 8,39 |
| +0,003 SOL phí ưu tiên/Jito mỗi giao dịch | 13,22 | 10,73 | 9,35 | 8,07 | 7,34 |
| Ngưỡng đuổi giá 10% (thay 25%): PnL | 11,92 | 9,09 | 10,13 | 9,20 | 6,26 |
| Ngưỡng 10%: số lệnh khớp | 214 | 185 | 191 | 165 | 162 |
| Ngưỡng 10%: drawdown tối đa | -1,86 | -1,82 | -1,53 | -1,55 | -1,78 |

Ngưỡng đuổi giá 10% bỏ khoảng 30–40% số lệnh, giảm PnL ít hơn và giảm drawdown còn khoảng −1,5 đến −1,9 SOL: đây là cấu hình an toàn hơn nếu sau này copy thật.

## VII. ĐỐI CHIẾU ĐỘ TRỄ PHÁT HIỆN THẬT VỚI ĐỘ TRỄ MÔ PHỎNG

| Nguồn đo | Số lần | Trung vị (s) | p90 (s) | Tối đa (s) |
|---|---|---|---|---|
| Theo dõi trực tiếp 5 ví qua RPC công khai (polling) | 25 | 5,16 | 7,98 | 10,31 |
| Riêng ví 3kxc | 4 | 5,89 | – | 6,80 |

Độ trễ mô phỏng là thời gian từ lệnh của ví đến lúc lệnh copy KHỚP. Thực tế = độ trễ phát hiện (trung vị ~5 s, p90 ~8 s) + thời gian ký, gửi và vào khối (thêm ~0,5–2 s). Vì vậy với hạ tầng miễn phí hiện tại, kịch bản sát thực tế là 8 giây (trung vị) đến 10 giây (p90); mức 1–3 giây chỉ đạt được với stream trả phí (đo trước đây: Helius WebSocket trung vị 2,18 s, p90 3,11 s).

## VIII. HẠN CHẾ CỦA KIỂM CHỨNG

- Mẫu 21 ngày, 1 ví, 302 vị thế: đủ cho thống kê mô tả, chưa đủ để khẳng định lợi thế bền vững (lợi nhuận tập trung ở tuần đầu).
- Không mô phỏng MEV/sandwich nhắm vào lệnh copy và tỷ lệ giao dịch thất bại của người copy (giao dịch rơi, hết hạn blockhash).
- Lệnh bán của người copy được tính trên pool thật KHÔNG có lệnh mua của chính người copy (thận trọng: thực tế giá bán có thể cao hơn một chút).
- 8 vị thế PumpSwap pool đảo chiều và 44 vị thế còn mở không được tính; 1 lệnh bán ở 8/10 s dùng trạng thái ước tính.
- Chi phí người copy lấy bằng chi phí ví gốc (rất thấp); trong thực tế cạnh tranh khối cần phí ưu tiên cao hơn – xem độ nhạy mục VI.

## IX. KIẾN NGHỊ

- Giữ ví 3kxc trong danh sách theo dõi Smart Money ở trạng thái 'theo dõi – paper trade', không bật copy tiền thật (trading_enabled vẫn = false).
- Chạy paper trade trực tiếp (độ trễ phát hiện thật) 2–4 tuần; chỉ xem xét nâng hạng khi PnL 2 tuần trượt ở 8 s > 0, ví duy trì ≥ 30 vị thế/tuần và ROI/lệnh không tiếp tục giảm.
- Nếu sau này copy: chỉ copy pool Meteora DAMM v2, bỏ DBC; ngưỡng đuổi giá 10%; giới hạn tác động giá 5%.
- Đánh giá lại hàng tuần bằng tool replay (dữ liệu checkpoint đã lưu, chỉ đọc phần mới).
