# PHƯƠNG ÁN TÌM VÍ SMART MONEY THẬT – SMART MONEY RADAR

**Ngày:** 23/11/2026.

**Phạm vi nghiên cứu:**
- đọc code và database;
- phân tích lại dữ liệu đã lưu, không gọi API tính credit;
- tra cứu tài liệu công khai.

Không chạy batch mới. Không sửa code, database, scoring hay scheduler.

**Trạng thái worker trong lúc nghiên cứu:** ba worker dùng Helius (sync, verifier, discovery) đang **tạm dừng** để không tốn credit. Stream (WebSocket công khai), giá, analytics và dashboard vẫn chạy.

---

## 0. KẾT LUẬN NGẮN

1. **Hệ thống chưa tìm được Smart Money không phải vì thiếu API, mà vì cách chọn ví đầu vào sai hướng.**
   - 76% số ví (590/781) đến từ nguồn `big_buyer`: "ví từng có 1 lệnh mua lớn".
   - Nguồn này chọn theo khối lượng, nên kéo về chủ yếu bot: 347 ví BOT/LIKELY_BOT, 252 ví tần suất cao.
   - Nguồn `early_buyer` có trong code nhưng **chưa tạo ra ví nào**.
2. **Cần đổi sang hướng "truy ngược từ token thắng":**
   1. Tìm token tăng trưởng thật.
   2. Lấy những ví mua sớm (nhưng không phải sniper hay bundler) **và đã bán có lãi**.
   3. Giữ lại ví lặp lại được điều đó trên nhiều token.
   4. Kiểm chứng toàn bộ lịch sử của ví, **kể cả các lệnh thua**, bằng on-chain sau phí.

   Đây cũng là định nghĩa mà nghiên cứu học thuật gần đây dùng: kỹ năng và lợi nhuận phải nhất quán, không phải mua trúng một lần.
3. **Nguồn dữ liệu tốt nhất và rẻ nhất đã có sẵn trong hệ thống:**
   - stream on-chain miễn phí nhận **mọi** giao dịch pump.fun/PumpSwap;
   - Helius gTFA đã kiểm chứng, khoảng 280 credit/ví.

   Vấn đề duy nhất: stream đang **bỏ** các lệnh dưới 1 SOL của ví chưa theo dõi. Vì vậy không dựng lại được toàn bộ người mua sớm của một token.
4. **Bằng chứng từ dữ liệu hiện có** (khoảng 23 giờ stream, 588.049 lệnh, 23.441 ví, phân tích lại miễn phí):
   - 67 token đạt "thắng" (≥ 5 lần, vốn hóa đỉnh ≥ 400 SOL);
   - qua bộ lọc nghiêm còn **3 ví ứng viên**;
   - **0 ví** vừa vào sớm vừa có lãi trên token thắng.

   ⇒ Với 1 ngày dữ liệu thiếu lệnh nhỏ, không đủ để kết luận. Cần **7–14 ngày dữ liệu đầy đủ** của các token thắng.

---

## A. NGUỒN DỮ LIỆU – XẾP THEO MỨC PHÙ HỢP VÀ CHI PHÍ

| Hạng | Nguồn | Cung cấp gì | Chi phí | Phù hợp | Ghi chú |
|---|---|---|---|---|---|
| 1 | **Stream on-chain của hệ thống** (logsSubscribe pump.fun + PumpSwap, WebSocket công khai) | **Mọi** lệnh mua/bán, tạo token, migration, theo thời gian thực | **0** | ★★★★★ | Đã chạy. Chỉ cần **giữ lại** lệnh nhỏ của token sau này thành "thắng" (bộ đệm vòng, mục E) |
| 2 | **Helius gTFA** (đã kiểm chứng 23/11) | Toàn bộ lịch sử của một ví hoặc một token, giống getTransaction từng trường | Khoảng 280 credit/ví (lịch sử 60 ngày, trần 3.000 chữ ký); token: 10 credit / 100 giao dịch | ★★★★★ | Dùng để **kiểm chứng** ứng viên, không dùng để dò diện rộng |
| 3 | Helius RPC thường (getTransaction, getTokenLargestAccounts) | Đối chiếu, holder | 1 credit/lệnh | ★★★★ | Đã có |
| 4 | Nansen Smart Money (API Pro khoảng 49–69 USD/tháng, khoảng 2.000 credit) | Nhãn Smart Money, giao dịch của ví có nhãn | Trả phí, credit ít | ★★★ | Chỉ là **nguồn gợi ý ví**; mọi ví vẫn phải qua kiểm chứng của hệ thống. Dữ liệu chỉ dùng nội bộ |
| 5 | Birdeye `defi/v2/tokens/top_traders` (Solana, sắp xếp theo PnL, 2–90 ngày) | Top trader theo PnL của một token | Gói Starter trở lên (giá CU cần xác minh) | ★★★ | PnL của bên thứ ba **không thay thế** kiểm chứng on-chain; hữu ích để thêm ứng viên |
| 6 | Solana Tracker Data API (free 2.500 request/tháng) | PnL trader, leaderboard token pump.fun, phát hiện sniper/bundler | Free có giới hạn | ★★ | Có thể dùng thử để đối chiếu nhãn sniper/bundler |
| 7 | Bitquery Pump.fun API | Top traders, first-100-buyers | Khoảng 29–39 USD/tháng; dữ liệu lịch sử cần add-on | ★★ | Trùng với thứ hệ thống tự làm được |
| 8 | Dune (`dex_solana.trades`) | Truy vấn SQL lịch sử | Free chỉ xem; chạy query cần trả phí; trễ khoảng 3 giờ | ★★ | Hợp cho nghiên cứu một lần, không hợp chạy liên tục |
| — | GMGN, Axiom, Trojan, kolscan | Bảng xếp hạng, nhãn ví | Không có API chính thức xác minh được | Chỉ tham khảo | **Không** scraping |

**Khuyến nghị:** dùng nguồn 1 + 2, chi phí gần như chỉ là credit Helius đang có. Nguồn 4 hoặc 5 chỉ thêm khi cần mở rộng danh sách ứng viên, và phải được duyệt chi phí.

---

## B. PHƯƠNG PHÁP TÌM VÍ SMART MONEY TỪ ĐẦU

**Bước 1 – Chọn token thắng (miễn phí, từ stream).** Token đạt đủ:
- vốn hóa đỉnh ≥ 400 SOL (khoảng ≥ 70.000 USD);
- tăng ≥ 5 lần so với giá trong 10 phút đầu;
- **giữ được ≥ 30% giá đỉnh sau 1 giờ** (loại token bơm-xả);
- không bị cờ rủi ro nghiêm trọng (`risk.py`: dev giữ > 10%, bundle lớn, holder tập trung).

Lưu ý: chỉ dùng vốn hóa từ curve hoặc giao dịch SOL đã verified. Dữ liệu hiện có ghi nhận vài giá trị vốn hóa sai (ví dụ 800.000 SOL do pool không định giá bằng SOL).

**Bước 2 – Lấy người mua sớm của mỗi token thắng (miễn phí).** Lấy những người mua khi vốn hóa còn ≤ 30% đỉnh, **nhưng loại**:
- sniper: mua trong ≤ 2 slot kể từ lúc tạo token;
- bundler: ≥ 3 ví mua cùng slot đầu, cùng nguồn cấp vốn;
- ví dev và ví được dev cấp vốn (insider).

Tính PnL của ví **trên token đó**, sau phí pump, theo token đã khớp (mua vào = bán ra).

**Bước 3 – Lặp lại (miễn phí).** Trong 7–14 ngày, giữ ví **có lãi trên ≥ 3 token thắng khác nhau**. Một lần trúng không tính.

**Bước 4 – Kiểm chứng toàn bộ lịch sử (tốn credit, khoảng 280/ví).** Backfill 60 ngày bằng gTFA, rồi chạy `verify-pnl`. Bước này **bắt buộc** vì bước 1–3 chỉ nhìn các lệnh thắng; phải thấy **cả lệnh thua** mới biết ví có thật sự giỏi không.

**Bước 5 – Chấm và giữ.** Áp bộ tiêu chí mục C. Ví đạt được đưa vào **bộ mẫu** (mục D), theo dõi thời gian thực, và kiểm tra lại sau 14 ngày bằng dữ liệu mới (out-of-sample) trước khi coi là Smart Money chính thức.

**Vì sao cách này đúng hơn cách cũ:**

| | Cách cũ (`big_buyer`) | Cách mới |
|---|---|---|
| Hỏi gì | "Ai mua nhiều?" | "Ai mua sớm token thật sự thắng, bán có lãi, và lặp lại được?" |
| Tần suất | Ví giao dịch càng nhiều càng dễ lọt | Ví phải đúng nhiều lần, trên nhiều token khác nhau |
| Thao túng | Không phân biệt | Loại sniper, bundler, insider ngay từ bước 2 |

---

## C. BỘ TIÊU CHÍ CHỌN VÀ LOẠI VÍ

### C1. Bắt buộc về dữ liệu (không đạt = "chưa đủ dữ liệu", không đánh giá)

- Lịch sử đã đọc qua gTFA; **0 giao dịch thiếu**.
- Tỷ lệ verified **≥ 95%** trên lệnh mua/bán.
- Vị thế bán token có từ trước cửa sổ lịch sử **không tính** vào PnL/Win Rate.
- Lệnh định giá USDC chưa verified **không tính** vào PnL (đã chứng minh sai lệch lớn trong báo cáo điều tra).

### C2. Loại ngay (bot / sniper / bundler / insider)

| Loại | Dấu hiệu (ngưỡng đề xuất) | Dữ liệu đã có |
|---|---|---|
| Bot tần suất cao | > 100 lệnh/ngày; hoặc giữ lệnh trung vị < 30 giây; hoạt động đều 24/24 giờ | `behavior.py`, wallet_transactions. Ví dụ `8whR2Fm1`: 1.123 lệnh / 5,5 ngày (khoảng 204/ngày), 147 token, hoạt động 17/24 khung giờ ⇒ BOT |
| Sniper | > 30% lần vào lệnh ở ≤ 2 slot sau khi tạo token | tokens.created_slot, slot giao dịch |
| Bundler | Cùng slot đầu với ≥ 3 ví khác, cùng nguồn cấp vốn, số tiền giống nhau | slot, sol_transfers, wallet_age (funder) |
| Insider / ví dev | Được creator cấp vốn (trực tiếp hoặc 1–2 bước); nhận token chuyển từ dev | relationships.py (funding, creator edges) |
| Copy-trader ăn theo | Thường vào lệnh 1–3 giây sau một KOL hoặc ví đã biết | stream + watched_wallets |
| Phụ thuộc 1 lệnh | Lệnh lãi lớn nhất > 40% tổng PnL | wallet_positions |

### C3. Điều kiện để là Smart Money (30–60 ngày, sau mọi phí)

| Tiêu chí | Ngưỡng |
|---|---|
| Số vị thế đã đóng ≥ 1 SOL | ≥ 20 |
| PnL thực sau phí (pump, mạng, tip) | > 0 và ≥ 10 SOL |
| Profit factor | ≥ 1,5 |
| Win Rate | ≥ 45% (memecoin có thể thắng ít nhưng thắng lớn; PF quan trọng hơn) |
| Lặp lại | Lãi trên ≥ 3 token thắng khác nhau, vào sớm (≤ 30% vốn hóa đỉnh) |
| Ổn định | Có lãi ở **cả hai nửa** thời gian (`repeatable_both_halves` đã có) |
| Copy được | Mô phỏng copy trễ 10–30 giây vẫn dương (`simulate_copy` đã có) |
| Hành vi | Không dính C2; thời gian giữ trung vị ≥ 2 phút |

### C4. Trạng thái của ví

| Trạng thái | Điều kiện |
|---|---|
| ứng viên | Qua bước 3 |
| đang kiểm chứng | Đang backfill + verify |
| Smart Money tạm thời | Đạt C1 + C2 + C3 |
| **Smart Money chính thức** | Tiếp tục đạt sau 14 ngày dữ liệu mới |
| loại | Có lý do ghi rõ |

---

## D. THIẾT KẾ BỘ DỮ LIỆU MẪU MỚI

### D1. Cấu trúc (giai đoạn đầu không cần đổi schema)

| Thành phần | Lưu ở đâu (sẵn có) | Nội dung |
|---|---|---|
| Token thắng | `api_sync_status` (source = `winner`, job = mint, details jsonb), hoặc `tokens` | mint, thời điểm tạo, vốn hóa đầu / đỉnh / sau 1 giờ, hệ số tăng, cờ rủi ro |
| Người mua sớm của token thắng | `wallet_transactions` (đã có, cần giữ cả lệnh nhỏ cho token thắng) | chữ ký, slot, khoảng cách slot so với lúc tạo, vốn hóa khi vào, PnL trên token |
| Ví ứng viên | `wallets` với `discovery_source = 'winner_reverse'`; bằng chứng trong `discovery_detail` | danh sách token thắng, chữ ký mua/bán, PnL từng token |
| Kết quả kiểm chứng | `discovery_detail.backfill_check` + `verify-pnl` (đã có) | đầy đủ, verified, PnL sau phí so với on-chain |
| Nhãn bộ mẫu | `discovery_detail.sample_label` | ứng viên / đang kiểm chứng / tạm thời / chính thức / loại + lý do |

Về sau, khi cần truy vấn nhanh và giữ bằng chứng lâu dài, có thể thêm bảng riêng `research_winners`, `research_early_buyers` (migration chỉ tạo bảng mới, **cần duyệt**).

### D2. Ví dụ minh họa (dữ liệu thật, từ `reverse_winners_result.json`)

**Ví dụ 1 – Ứng viên cần kiểm chứng: `Gtw5QKLnYazyApkzmCXo9GvXa5sCjwvx2bdRLaid8FHu`**

Số liệu trong 23 giờ stream:
- 17 vị thế đã đóng (token khớp), PnL +4,82 SOL sau phí pump;
- Win Rate 59%, PF 2,29, lệnh lãi lớn nhất chiếm 58% PnL;
- giữ lệnh trung vị 811 giây, mua trung vị 3,95 SOL, khoảng 66 lệnh/ngày;
- không phải sniper, bundler hay creator.

Bằng chứng (chữ ký giao dịch):

| Token | Vốn vào | Lãi | Vốn hóa lúc vào → đỉnh | Mua | Bán |
|---|---|---|---|---|---|
| `EV7J2QaP…` | 6,08 SOL | +2,81 SOL | 4.841 → 12.590 SOL | `2TKJfeLLqJjJczgk…` | `58qydFV9CdjhGy58…` |
| `7R4MP6VX…` | 9,79 SOL | +1,46 SOL | | `NszSsDUXgobqjvg6…` | `2QiWBN4yQ8k9mfLG…` |
| `3umHFkiM…` | 14,63 SOL | +1,40 SOL | | `5kLUJAB9Kmxthbms…` | `5HhEq4wkD4cLRnGa…` |

**Chưa phải Smart Money:**
- mới 23 giờ dữ liệu;
- lệnh lãi lớn nhất chiếm 58% PnL;
- chưa thấy lệnh thua dưới 1 SOL;
- **chưa vào sớm token thắng nào**.

⇒ Trạng thái: *ứng viên* → cần backfill khoảng 280 credit để thấy toàn bộ lệnh thua.

**Ví dụ 2 – Loại vì là bot dù Win Rate cao: `8whR2Fm1…`**

- Lịch sử đầy đủ 60 ngày, verified 100%, Win Rate 96%, PnL +56,2 SOL.
- Nhưng: 1.123 lệnh / 5,5 ngày (khoảng 204/ngày), 147 token, hoạt động 17/24 khung giờ.

⇒ BOT. Đây là ví dụ vì sao **không** được kết luận Smart Money chỉ từ PnL và Win Rate.

**Ví dụ 3 – Loại vì PnL bị đội: `8BmPoCUx…`**

- PnL 30 ngày 269,9 SOL, nhưng 97,3 SOL là tiền bán token mua từ trước cửa sổ lịch sử (không có giá vốn).
- Kèm nhãn LIKELY_BOT.

⇒ loại.

**Ví dụ 4 – Không đủ dữ liệu: `EGLhxVyq…` và `DSk4pb9Z…`**

- Win Rate 83–88%, nhưng chỉ 6–8 vị thế nhỏ trong 1 ngày.
- Với `DSk4pb9Z…`, vốn hóa đỉnh ghi nhận 800.000 SOL là **dữ liệu sai** (pool không định giá bằng SOL).

⇒ không dùng.

### D3. Kết quả phễu thật trên dữ liệu hiện có (23 giờ, chứng minh phương pháp lọc chặt)

| Bước | Số ví |
|---|---|
| Ví xuất hiện trong stream | 23.441 |
| ≥ 6 vị thế đã đóng ≥ 0,5 SOL (token khớp) | 407 |
| + không giống bot (< 150 lệnh/ngày, giữ trung vị ≥ 30 giây) | 77 |
| + không sniper/bundler/creator | 59 |
| + có lãi sau phí: PnL > 0, WR ≥ 50%, PF ≥ 1,5 | 13 |
| + không phụ thuộc 1 lệnh | **3** |
| + có lãi khi vào sớm token thắng | **0** |

Kết luận: phễu loại đúng bot và ví may mắn. Số ứng viên ít là do **thiếu dữ liệu** (23 giờ, thiếu lệnh dưới 1 SOL), không phải do tiêu chí sai.

---

## E. KẾ HOẠCH TÍCH HỢP VÀO SMART MONEY RADAR

Mỗi giai đoạn cần anh duyệt riêng. Không phá hệ thống hiện có. `trading_enabled = false`.

### Giai đoạn 1 – Thu dữ liệu đầy đủ cho token thắng (0 credit) – *đề xuất làm trước*

- **Stream:** thêm **bộ đệm 2 giờ** trong Redis giữ **mọi** lệnh, kể cả dưới 1 SOL. Khi token đạt ngưỡng "đang lên" (vốn hóa ≥ 150 SOL), ghi toàn bộ lệnh của token đó từ bộ đệm vào `wallet_transactions`; từ đó trở đi lưu mọi lệnh của token.
- File sửa: `services/stream.py`, `settings.py` (ngưỡng). Không đổi schema.
- Ước tính dung lượng: chỉ token đang lên (khoảng vài trăm/ngày).
- Nghiệm thu: với mỗi token thắng có đủ 100% người mua; đối chiếu số lệnh với gTFA của token đó trên 2–3 token mẫu (khoảng 50–200 credit).

### Giai đoạn 2 – Discovery mới "winner_reverse" (0 credit)

- File mới: `services/winner_reverse.py` (chạy trong worker discovery). Dùng lại `risk.py`, `behavior.py`, `relationships.py`.
- Chạy mỗi giờ: chọn token thắng (bước B1), lấy người mua sớm hợp lệ (B2), cộng dồn số lần thắng lặp lại theo ví (B3).
- Ví đạt bước B3 được thêm vào `wallets` với `discovery_source='winner_reverse'`, bằng chứng nằm trong `discovery_detail`.
- **Không** xóa `big_buyer`: chỉ thêm nguồn mới.

### Giai đoạn 3 – Thứ tự ưu tiên backfill (credit có kiểm soát)

- Scheduler ưu tiên ví `winner_reverse` (số token thắng lặp lại giảm dần), trước `big_buyer`. Ví bị loại do bot không chiếm suất trong hạn mức ngày.
- File sửa: `services/wallet_sync.py` (ORDER BY).
- Chi phí: khoảng 280 credit/ví; ví dụ 30 ví/ngày ≈ 8.400 credit/ngày, nằm trong trần 150.000.

### Giai đoạn 4 – Sửa 2 lỗi PnL đã phát hiện (giải mã lại tại chỗ, 0 credit)

- Vị thế bán token có từ trước cửa sổ lịch sử: không tính vào PnL/Win Rate (gắn cờ `opened_before_window`).
- Lệnh định giá USDC qua router: chỉ tính khi đã verified; hoặc sửa định giá rồi kiểm chứng lại bằng `verify-pnl`.
- File: `analytics/positions.py`, `analytics/performance.py`. **Cần duyệt vì là logic PnL.**

### Giai đoạn 5 – Bộ mẫu và theo dõi

- Gắn nhãn mẫu (C4) trong `discovery_detail.sample_label`.
- Trang Top Wallets lọc thêm theo "winner_reverse / tạm thời / chính thức".
- Sau 14 ngày: kiểm tra out-of-sample (ví còn có lãi trên token mới hay không) trước khi coi là chính thức.

### Giai đoạn 6 (tùy chọn) – Nguồn ngoài

- Birdeye top_traders hoặc Nansen chỉ để **thêm ứng viên**; mọi ví vẫn qua bước 4.
- Phát sinh chi phí ⇒ cần duyệt.

### Lịch dự kiến

| Thời điểm | Mốc |
|---|---|
| Ngày 1–2 | Giai đoạn 1 + 2 (code, test, chạy). Bắt đầu tích lũy dữ liệu. |
| Ngày 3–9 | Tích lũy 7 ngày token thắng; GĐ 4 có thể làm song song |
| Ngày 10 | Danh sách ứng viên lặp lại ≥ 3 token thắng ⇒ backfill có trần ⇒ bộ mẫu tạm thời đầu tiên kèm bằng chứng |
| Ngày 24 | Kiểm tra out-of-sample ⇒ danh sách Smart Money chính thức |

**Điều kiện thực tế cần lưu ý:** máy chủ thử nghiệm hiện tại tự khởi động lại nhiều lần trong ngày (đã thấy ít nhất 4 lần chỉ trong ngày 23/11). Giai đoạn 1–2 cần dữ liệu liên tục 7 ngày, nên **phải chạy trên VPS hoặc máy chạy 24/7** thì mới có kết quả.

---

## NGUỒN THAM KHẢO

**Nghiên cứu:**
- [arXiv 2601.08641 – copy-trading memecoin Solana, định nghĩa smart money](https://arxiv.org/html/2601.08641v2)
- [arXiv 2607.02795 – Coordinated Sniper Cohorts on Pump.fun](https://arxiv.org/abs/2607.02795)
- [Pine Analytics – Exit Liquidity Machines (deployer cấp vốn cho sniper)](https://pineanalytics.substack.com/p/exit-liquidity-machines)
- [MemeTrans dataset](https://arxiv.org/html/2602.13480)

**Phát hiện sniper/bundler:**
- [Solana Tracker – sniper detection](https://docs.solanatracker.io/guides/sniper-detection)
- [Solana Tracker – bundlers](https://www.solanatracker.io/blog/bundlers)
- [Mobula – detecting snipers & bundlers](https://docs.mobula.io/almanac/detecting-snipers-bundlers)

**Nhà cung cấp dữ liệu:**
- [Birdeye – Token Top Traders](https://docs.birdeye.so/docs/token-top-traders)
- [Birdeye – thay đổi top traders 07/2026](https://docs.birdeye.so/changelog/20260723-release-wallet-identity-apis-and-solana-top-traders-improvements)
- [Bitquery Pump.fun API](https://docs.bitquery.io/docs/blockchain/Solana/Pumpfun/)
- [Solana Tracker Pump.fun API](https://www.solanatracker.io/pumpfun-api)
- [Dune – Solana DEX trades](https://docs.dune.com/data-catalog/curated/dex-trades/solana/overview)
- [Dune – credits](https://docs.dune.com/resources/credits-billing/how-credits-work)

**Nội bộ:**
- `docs/handover/bao-cao-dieu-tra-sau-batch-1.md`
- `docs/handover/bao-cao-batch-2.md`
- `docs/research/reverse_winners_analysis.py`, `docs/research/reverse_winners_result.json`
