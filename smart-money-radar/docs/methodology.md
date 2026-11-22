# Phương pháp tính — định nghĩa chính xác

Mọi công thức dưới đây có trong mã nguồn (đường dẫn trong ngoặc) và được kiểm thử bằng unit test.

## 1. Giải mã giao dịch (`radar/decoder/`)

Nguồn: `getTransaction` (encoding `jsonParsed`, `maxSupportedTransactionVersion = 1` — mainnet đã có transaction version 1,
kiểm tra ngày 22/11/2026). Event Anchor của pump.fun (`TradeEvent`, `CreateEvent`, `CompleteEvent`,
`CompletePumpAmmMigrationEvent`) và PumpSwap (`BuyEvent`, `SellEvent`) được đọc từ lệnh tự gọi `emit_cpi`
(dự phòng: dòng `Program data:` trong log). Layout đã đối chiếu với giao dịch thật (`tests/fixtures/tx`).

Cho mỗi lệnh của một ví:

| Trường | Ý nghĩa |
|---|---|
| `sol_amount` | SOL đi vào/ra curve hoặc pool **trước phí** — với lệnh mua đây là **vốn thực tế** |
| `fee_trading_sol` | phí protocol + creator (curve) hoặc LP + protocol + creator (PumpSwap) |
| `fee_network_sol` | phí giao dịch gồm priority fee — chỉ khi ví là người trả phí |
| `tip_sol` | SOL gửi tới tài khoản tip Jito ngoài phạm vi swap |
| `other_cost_sol` | SOL khác rời ví ngoài swap: phí bot/router, phí tạo token (mint, curve, metadata), nạp rent cho tài khoản protocol |
| `rent_sol` | lamports khoá vào (+) / nhận lại (−) từ **tài khoản token của chính ví** — hoàn lại được nên **không tính là chi phí** |

**Đối soát:** biến động tài sản SOL của ví `ΔW = Δlamports(ví) + Σ Δlamports(tài khoản token của ví)` (gồm WSOL) được so với
giá trị suy ra từ event: `Σ(−sol−phí cho lệnh mua, +sol−phí cho lệnh bán) − phí mạng − tip − chi phí khác + SOL nhận ngoài swap`.
Nếu |chênh lệch| ≤ 10 000 lamports **và** biến động token của ví khớp event ⇒ **Verified**. Ngược lại **Partially Verified** kèm
chênh lệch và lý do. Dữ liệu chỉ giải mã từ log (chưa tải giao dịch đầy đủ) là Partially Verified; dữ liệu bên thứ ba chưa đối chiếu là
**Unverified** và không bao giờ được dùng cho tín hiệu.

Các trường hợp đặc biệt đã gặp trên mainnet và cách xử lý:

| Trường hợp | Xử lý |
|---|---|
| Giao dịch thất bại (`err ≠ null`) | không tạo lệnh; lưu vào `failed_transactions` |
| Nhiều lệnh trong một tx (bundle) | mỗi event là một bản ghi `trade:<n>`; chi phí tx gắn vào lệnh đầu của ví |
| Mua bằng USDC/USD1 qua Jupiter | SOL lấy từ event curve; Partially Verified, ghi chú "paid with another asset" |
| Router: user trong event là tài khoản trung gian không ký | quy về ví ký có biến động token đúng bằng event; nếu không xác định được thì giữ và đánh dấu |
| Multi-hop qua curve định giá bằng token khác (`virtual_sol_reserves = 0`) | gộp thành **một** lệnh của token A với SOL thực của chặng B |
| PumpSwap "exact quote in" (phí trừ từ đầu vào) | SOL vào pool = max(quote_in, user_quote_in) − phí |
| Tài khoản WSOL tạm tạo và đóng trong tx | nhận diện qua `initializeAccount`/`closeAccount` |
| Pool "đảo" (base = WSOL) | đổi chiều mua/bán; phí tính bằng token nên không quy ra SOL (ghi chú) |
| Mayhem mode (tài khoản agent `BwWK17…`, phí 0) | lệnh vẫn được giải mã nhưng agent không bao giờ được xem là ví |

Khoá chống trùng: `(signature, event_key)`. Stream và verifier dùng cùng quy tắc đánh số event nên verifier ghi đè đúng dòng;
dòng mà bản giải mã đầy đủ không còn tạo ra sẽ bị xoá.

## 2. Vị thế (`radar/analytics/positions.py`)

* **Vị thế** = một chu kỳ nắm giữ một token: mở khi ví đang không giữ token đó và mua (hoặc nhận token); đóng khi lượng nắm giữ
  ≤ 0,1% lượng cao nhất của chu kỳ. Mua lại sau khi đã thoát ⇒ vị thế mới.
* Nhiều lần mua trong chu kỳ = **một** vị thế; **Actual Entry Cost** = tổng `sol_amount` các lần mua (không phải volume mua+bán).
* Giá vốn bình quân: bán k token ⇒ lãi gộp = tiền bán − k × giá vốn bình quân. Bán một phần giữ vị thế mở.
* **Chuyển token** không phải mua/bán: nhận token = thêm token giá vốn 0 (gắn cờ, vị thế mở bằng chuyển khoản không bao giờ đạt chuẩn);
  chuyển đi = rút token cùng phần giá vốn tương ứng (giá vốn được chuyển đi, không phải lỗ).
* Lệnh bán không có lệnh mua đã biết = "orphan sell": không tạo PnL, giảm độ phủ dữ liệu.
* **Write-off**: vị thế mở mà ví không giao dịch token đó ≥ 72 giờ **và** phần còn lại trị giá < 10% giá vốn (hoặc không định giá được)
  ⇒ đóng như một khoản **lỗ** (giá trị còn lại = 0). Không có quy tắc này, ví không bao giờ bán token chết sẽ có Win Rate ảo.

**Vị thế đạt chuẩn (qualifying)**: Actual Entry Cost ≥ `min_buy_sol` (mặc định 2 SOL). Mọi chỉ số Smart Money chỉ dùng vị thế đạt chuẩn.

## 3. PnL và hiệu suất (`radar/analytics/performance.py`)

* **Realized PnL** = SOL thu về từ bán − giá vốn của phần đã bán − toàn bộ phí (giao dịch + mạng + tip + bot) của vị thế.
* **Unrealized PnL** = token còn lại × giá on-chain gần nhất − giá vốn còn lại. **Total PnL** = Realized + Unrealized.
* **ROI** = Realized PnL / giá vốn của các vị thế đạt chuẩn đã đóng.
* **Win Rate** = số vị thế đạt chuẩn đã đóng (kể cả write-off) có Realized PnL > 0 / tổng số vị thế đạt chuẩn đã đóng. Vị thế mở không tính.
* **Average / Median / Largest / Total Buy SOL** = theo vốn mỗi vị thế đạt chuẩn. **Average position size** = trung bình mọi vị thế.
  Ngoài ra lưu trung bình/trung vị **từng lệnh mua** (`metrics.avg_buy_tx_sol`).
* **Profit Factor** = tổng lãi / |tổng lỗ|; **Max Drawdown** = mức giảm lớn nhất của đường Realized PnL tích luỹ theo thời điểm đóng.
* Lọc 7/30/90 ngày theo thời điểm mua đầu tiên của vị thế.

## 4. Khả năng chọn coin (`radar/analytics/price_hits.py`)

Chỉ dùng **giá lịch sử**: nến 1 phút dựng từ chính các lệnh on-chain đã giải mã (nguồn `onchain_trades`, chính xác theo giá khớp),
thiếu thì dùng nến GeckoTerminal (1 phút cho ~16 giờ đầu, 1 giờ cho 7 ngày). Không bao giờ dùng giá hiện tại cho mốc quá khứ.

* **ROI theo mốc 5m, 15m, 30m, 1h, 6h, 24h, 7d**: giá đóng cửa của nến cuối cùng ≤ (entry + mốc), nếu nến đó không quá cũ; so với giá vào lệnh
  bình quân của ví.
* **Mức tăng cao nhất sau entry (MFE)** trong 7 ngày, **có xác nhận thanh khoản**: một nến chỉ hợp lệ khi khối lượng ≥ 1 SOL và thanh khoản
  (SOL thật trong curve/pool) ≥ 3 SOL; mức giá của nến là giá cao nhất của các lệnh ≥ 0,3 SOL (nến on-chain) hoặc giá cao nếu nến
  GeckoTerminal có khối lượng ≥ 3 SOL, ngược lại là giá đóng cửa. Một mức chỉ được công nhận khi **ít nhất 2 nến hợp lệ trong 5 phút** đạt mức đó
  ⇒ loại bỏ "râu" do một lệnh nhỏ trên thanh khoản mỏng.
* **2x/3x/5x/10x Token Hit Rate** = tỷ lệ vị thế đạt chuẩn (có dữ liệu giá) mà token đạt N lần giá vào của ví.
* Tách bạch: **token tăng 5x sau khi ví mua** (`hit_5x`) ≠ **ví thực sự chốt 5x** (`realized_multiple ≥ 5`); cả hai được hiển thị,
  và `metrics.realized_5x_given_token_5x` cho biết ví chốt được bao nhiêu trong số token đã 5x.

## 5. Smart Money Score (`radar/analytics/scoring.py`)

| Thành phần (trọng số) | Công thức hệ số ∈ [0,1] |
|---|---|
| Win Rate (20) | Win Rate làm trơn Laplace `(thắng+1)/(đóng+2)`, tuyến tính 50% → 0, 85% → 1 |
| Median Buy SOL (20) | cỡ = 0,25 + 0,75·log(median/2)/log(25) (2 SOL → 0,25; 50 SOL → 1) × (0,6 + 0,4·độ ổn định); độ ổn định = 0,4·tỷ lệ vị thế ≥ 2 SOL + 0,4·min(1, median/mean) + 0,2·(vốn so với thanh khoản lúc vào) |
| Realized PnL (15) | log10(1+PnL)/log10(101) (100 SOL = 1), PnL ≤ 0 → 0 |
| ROI thực tế (15) | ROI/100%, giới hạn [0,1] |
| Mua trước khi tăng (20) | 0,45·hit2x/0,6 + 0,35·hit5x/0,3 + 0,20·hit10x/0,15 (mỗi phần giới hạn 1) |
| Ổn định & độc lập (10) | 0,4·(1 − (tỷ trọng lệnh thắng lớn nhất − 0,4)/0,5) + 0,3·[PnL khi bỏ lệnh thắng lớn nhất > 0] + 0,3/√(kích thước cụm ví) |

Ưu tiên ví ít giao dịch: > 30 vị thế đóng ⇒ hệ số giảm dần (tối thiểu ×0,8); > 300 vị thế/kỳ ⇒ ×0,85 (giống bot).
Phân loại: 85–100 Excellent · 75–84 Strong · 65–74 Watchlist · < 65 Không ưu tiên.

**Tiêu chí bắt buộc** (không đạt ⇒ không vào Top chính thức): ≥ 5 vị thế đạt chuẩn đã đóng, Win Rate ≥ 65%, Median Buy ≥ 2 SOL,
Realized PnL > 0, ROI > 0, không bị loại trừ, phần lớn lệnh do chính ví ký.

**Độ tin cậy dữ liệu:** High = ≥ 10 vị thế đóng, ≥ 80% Verified, lịch sử backfill đủ cả kỳ, ≥ 60% vị thế có dữ liệu giá ·
Medium = ≥ 5 vị thế đóng, ≥ 50% Verified · còn lại Low. **Top 10 chính thức chỉ gồm High/Medium.**

## 6. Ví độc lập (`radar/analytics/relationships.py`)

Cạnh quan hệ (kèm bằng chứng chữ ký): nạp SOL trực tiếp (0,9), chuyển token qua lại (0,9), chuyển SOL ≥ 0,1 (0,85), cùng ví funding
đầu tiên (0,75 — ví funding cấp cho ≥ 20 ví bị coi là sàn/dịch vụ và bỏ qua), liên quan cùng creator (0,7), mua cùng token trong ≤ 2 slot
trên ≥ 3 token (0,4 + 0,1/token). Cụm = thành phần liên thông qua cạnh ≥ 0,6. Tín hiệu đếm **số cụm**. Ví là creator hoặc được creator
nạp tiền thì không được tính khi mua token của chính creator đó.

## 7. Tín hiệu cùng mua (`radar/analytics/signals.py`)

Trong cửa sổ 5 phút, ví Smart Money (điểm ≥ 65, đạt tiêu chí, độ tin cậy ≥ Medium) có vị thế hiện tại ≥ 2 SOL:
WATCH ≥ 2 cụm & ≥ 5 SOL · STRONG ≥ 3 & ≥ 10 SOL · VERY STRONG ≥ 5 & ≥ 20 SOL. Huỷ nếu giá đã > 2× entry Smart Money đầu tiên.
STRONG trở lên đòi hỏi các lệnh được tính đều Verified; token HIGH RISK bị giới hạn ở WATCH. Mọi ngưỡng chỉnh được trong Cài đặt.

## 8. Rủi ro token (`radar/analytics/risk.py`)

11 kiểm tra: dev nắm giữ, top 10 holder (trừ curve/pool), ví mua sớm cùng funding, bundle trong slot tạo token, thanh khoản,
volume 5 phút / thanh khoản, wash trading (vòng mua-bán ≤ 60 giây cùng cỡ), dev đã bán, mint authority, freeze authority, khả năng bán
(curve còn hoạt động hoặc có route Jupiter). Dữ liệu thiếu cộng 0,5 điểm (thiếu dữ liệu ≠ an toàn).
HIGH ≥ 5 điểm hoặc có lỗi nghiêm trọng · MEDIUM 2–4,5 · LOW < 2. **Không bao giờ tuyên bố token an toàn tuyệt đối.**

## 9. Backtest (`radar/analytics/backtest.py`)

Walk-forward: tập Smart Money tại thời điểm t chỉ dùng vị thế **đã đóng trước checkpoint gần nhất ≤ t** (6 giờ) và giá **trước** checkpoint.
Tín hiệu phát ra tại lệnh mua kích hoạt + độ trễ phát hiện (5 giây). Entry = giá mở cửa của nến đầu tiên **sau** thời điểm phát hiện
× (1 + slippage 1% + tác động giá ước tính `size/(thanh khoản + 30 SOL ảo)`) × (1 + phí 1,25% curve / 0,4% PumpSwap).
Thoát tại 1h/6h/24h theo giá đóng cửa đã quan sát, trừ slippage/phí/tác động; thanh khoản < 1 SOL ⇒ lỗ toàn bộ. Quy tắc TP 2x / SL −50%.
Chia theo thời gian: train 60% → chọn tham số, validation 20%, out-of-sample test 20% (báo cáo một lần).
