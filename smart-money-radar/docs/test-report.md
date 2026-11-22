# Báo cáo kiểm thử & backtest

Ngày chạy: 22/11/2026 (UTC). Môi trường: PostgreSQL 16, Redis 7, Python 3.13, Node 22, RPC công khai
`api.mainnet-beta.solana.com` + `solana-rpc.publicnode.com` (không có Helius key). Mọi số liệu dưới đây là dữ liệu mainnet thật.

## 1. Kiểm thử tự động

```
cd backend
pytest -q tests/unit                                     # 57 passed
TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest -q tests     # 65 passed, 3 skipped (live)
pytest -q --live tests/integration/test_live_sources.py  # 3 passed
```

| Yêu cầu (PHẦN XIV) | Test |
|---|---|
| 1. Unit test PnL | `test_positions_pnl.py`: multiple buys, partial sells, average cost, fees, transfer in/out, write-off |
| 2. Unit test Win Rate | `test_win_rate_counts_closed_qualifying_positions_only`, write-off = thua, vị thế mở không tính |
| 3. Unit test vốn SOL | `test_multiple_buys_are_one_position_and_entry_cost_is_their_sum`, `test_median_buy_resists_one_huge_buy`, `test_large_curve_buy_separates_fees_and_bot_fee` (rent không tính chi phí) |
| 4. Phân loại vị thế | rebuy sau khi thoát = vị thế mới, dust, orphan sell, transfer, write-off |
| 5. Smart Money Score | `test_score_is_bounded_and_explainable` (tính lại được bằng tay), tiêu chí bắt buộc, độ tin cậy, tần suất cao, cụm ví, Top chính thức ≥ 65 điểm và phải có lịch sử backfill |
| 6. Integration test với API | `test_live_sources.py` (RPC, tài khoản Global pump.fun, Jupiter, DexScreener, GeckoTerminal) + `test_db_pipeline.py::test_api_requires_login_and_validates_settings` |
| 7. Giao dịch mua/bán thực tế | 33 lệnh từ 32 giao dịch mainnet thật (`tests/fixtures/tx`) + 1 multi-hop |
| 8. Dữ liệu trùng | ghi cùng tập giao dịch 2 lần → số dòng không đổi; khoá `(signature, event_key)`; stream không ghi đè dòng đã Verified |
| 9. Giao dịch thất bại | 3 giao dịch lỗi thật → 0 lệnh, lưu `failed_transactions` |
| 10. Token chưa bán hết | vị thế mở, Unrealized PnL, write-off khi bị bỏ rơi |
| 11. Nhiều lệnh mua cùng token | gộp một vị thế; bundle 4 ví trong 1 tx tách 4 lệnh |
| 12. Nhiều ví cùng chủ | cùng funder → 1 cụm (`test_wallets_of_one_owner_end_in_one_cluster`); 5 ví một chủ = 1 người mua (`test_five_wallets_of_one_owner_are_one_buyer`); ví funding cho ≥ 20 ví (sàn) không tạo quan hệ |
| Tín hiệu & cảnh báo | WATCH/STRONG/VERY STRONG, cửa sổ 5 phút, vị thế ≥ 2 SOL cộng dồn, runup, xác minh bắt buộc cho STRONG, HIGH risk, chống gửi trùng + tăng cấp (`test_signal_storage_and_alert_dedupe`) |
| Backtest không look-ahead | ví có thành tích hình thành **sau** thời điểm tín hiệu không được coi là Smart Money; độ trễ phát hiện; slippage/phí/tác động giá |

## 2. Decoder trên giao dịch thật

Bộ fixture: 32 giao dịch mainnet thật (mua/bán curve, ≥ 2 SOL, tạo token, PumpSwap mua/bán, migration, complete, thất bại, bundle) +
1 giao dịch multi-hop. Kết quả: **25/33 lệnh Verified** (đối soát đến từng lamport), 8 Partially Verified có lý do cụ thể:

| Lý do Partially Verified | Số lệnh | Diễn giải |
|---|---:|---|
| ví trả bằng USDC/USD1 qua Jupiter | 3 | SOL đi vào curve đúng theo event, nhưng ví không chi SOL |
| token có phí chuyển (Token-2022) / pool không chuẩn | 2 | lượng token ví nhận khác event 0,3% |
| user là ví trung gian chuyển tiếp tiền bán sang ví khác | 1 | ví ký và bán, tiền bán đổi USDC gửi ví khác |
| SOL do một người ký khác trả | 1 | PumpSwap, 2 người ký; ví nhận token không chi SOL |
| lệnh hoàn tất curve kèm token/SOL từ một thao tác chưa giải mã | 1 | chênh 2,64 SOL được giữ nguyên làm cảnh báo, **không** cộng vào chi phí |

Ngoài ra 2 event trên curve định giá bằng token khác không tìm được chặng SOL duy nhất ⇒ không tính (ghi chú trong `notes`).

Trong lúc chạy thật, decoder đã được sửa cho 6 trường hợp chỉ xuất hiện trên dữ liệu thật (đều có test):
transaction **version 1**; PumpSwap "exact quote in"; tài khoản WSOL tạm; SOL nạp rent bên trong pump.fun (650 240 lamports);
**multi-hop** qua curve định giá bằng token khác (`virtual_sol_reserves = 0`); **router** (user không ký → quy về ví ký).

## 3. Chạy thật toàn hệ thống (≈ 45 phút đầu)

| Chỉ số | Giá trị |
|---|---:|
| Thông báo log nhận qua `logsSubscribe` | > 160 000 lệnh pump.fun/PumpSwap đã giải mã |
| Lệnh được lưu (≥ 1 SOL hoặc ví theo dõi) | 45 763 |
| — Verified (giao dịch đầy đủ, đối soát số dư) | 6 827 |
| — Partially Verified từ giao dịch đầy đủ | 1 355 |
| — Partially Verified chỉ từ log (chưa tải giao dịch) | 37 581 |
| Giao dịch thất bại của ví theo dõi (bị loại) | 925 |
| Giao dịch thô lưu để kiểm toán | 5 500 |
| Token quan sát / thấy lệnh tạo / migrate | 2 998 / 922 / 7 |
| Nến giá 1 phút từ lệnh on-chain | 13 549 (2 618 token) |
| Nến GeckoTerminal (1 phút / 1 giờ) | 1 146 / 101 (51 token) |
| Ví ứng viên tìm được / đã backfill xong | 340+ / 8 |
| Ví bị bỏ qua vì tần suất ≥ 1 000 tx/ngày | 40+ |
| Vị thế dựng được (đóng / mở) | 972 / 943 |
| Quan hệ ví phát hiện | 14 mua đồng bộ, 1 chuyển SOL, 1 chuyển token, 1 cùng creator |

Ví dụ phát hiện thật: một bot launch tạo token rồi bundle-mua bằng **đúng 6 ví với cùng số SOL** ở nhiều token khác nhau
(ví `E4BMFSG1JQSTPkZUpAtH2YL6kfaN7J9sTD8k3Hs5Jt6Q` …). Hệ thống gom 5 ví thành một cụm (`sync_buys`), Win Rate 52%, PnL −0,4 SOL
→ "Không ưu tiên". Token của bot này bị xếp **HIGH RISK**: 7 ví mua trong slot tạo token (27,3% supply), dev đã bán 100%, thanh khoản 0.

## 4. Đối chiếu độc lập (≥ 20 giao dịch)

Công cụ `python -m radar crosscheck` — kết quả đầy đủ có liên kết Solscan từng giao dịch: [reports/crosscheck.md](reports/crosscheck.md).

| Đối chiếu | Kết quả |
|---|---:|
| Giao dịch khớp theo chữ ký với GeckoTerminal `/pools/{pool}/trades` | **40** |
| Chiều mua/bán trùng | 40/40 |
| Lượng token trùng tuyệt đối | 40/40 |
| SOL trùng (GeckoTerminal dùng SOL trước phí như hệ thống) | 40/40 (chênh 0,000000) |
| Ví giao dịch trùng | 37/40 — 3 khác biệt là bundle: GeckoTerminal ghi ví trả phí, hệ thống ghi ví nhận token |
| Dữ liệu thô giống hệt giữa 2 nhà cung cấp RPC | 36/40 — 4 không tải được do 429, **0 sai khác** |

**Đối chiếu thủ công:** mở các liên kết Solscan trong [reports/crosscheck.md](reports/crosscheck.md) (40 giao dịch) và so cột SOL/token.
Lưu ý: việc mở Solscan cần trình duyệt của người dùng — môi trường kiểm thử không truy cập solscan.io (không scrape), nên phần thủ công
được thay bằng đối chiếu tự động với một indexer độc lập + hai RPC độc lập như trên.

## 5. Giải thích sai số PnL

| Nguồn chênh lệch | Cách hệ thống xử lý |
|---|---|
| Phí protocol + creator pump.fun (0,95% + 0,30%) | tách vào `fee_trading_sol`, trừ khỏi PnL |
| Phí giao dịch + priority fee | `fee_network_sol` chỉ khi ví là người trả phí; bundle do ví khác trả phí ⇒ 0 |
| Tip Jito | `tip_sol`, trừ khỏi PnL |
| Phí bot/router (Axiom, Photon… chuyển SOL ngoài swap) | `other_cost_sol`, trừ khỏi PnL |
| Rent tài khoản token (≈ 0,0015–0,0020 SOL) | `rent_sol`, **không** trừ (hoàn lại khi đóng tài khoản) — đây là khác biệt so với cách tính "SOL ví giảm bao nhiêu" |
| Chi phí tạo token (mint, curve, metadata ≈ 0,02 SOL) | `other_cost_sol` của lệnh mua đầu của dev |
| Trả/nhận bằng USDC qua router | dùng SOL vào/ra curve theo event; Partially Verified |
| Chuyển SOL/token sang ví khác | không phải lỗ/lãi: token chuyển đi mang theo giá vốn |

Đối soát: với lệnh Verified, |ΔW thực tế − ΔW suy ra từ event và các khoản phí| ≤ 10 000 lamports (thực tế = 0 ở mọi lệnh Verified trong fixture).

## 6. Backtest

Công cụ `python -m radar backtest` (walk-forward, không look-ahead, train/validation/out-of-sample).
Kết quả trên dữ liệu thật thu được trong phiên chạy thử: **8 ví đã backfill · 1 436 lệnh · 123 token (66 có giá lịch sử) → 0 tín hiệu**,
vì tại mọi checkpoint chưa ví nào đạt tiêu chí Smart Money (≥ 5 vị thế ≥ 2 SOL đã đóng, Win Rate ≥ 65%, PnL/ROI dương, lịch sử đã backfill,
Score ≥ 65). Kết quả thô: [reports/backtest.json](reports/backtest.json).

**Kết luận trung thực:** chưa đủ dữ liệu để trả lời "3 Smart Money cùng mua thì bao nhiêu token tăng 2x/5x/10x". Cần hệ thống chạy liên tục
ít nhất 2–4 tuần (hoặc backfill nhanh hơn với Helius) để có hàng trăm ví đã backfill và đủ tín hiệu cho cả ba tập train/validation/test.
Logic backtest đã được kiểm chứng bằng test có kịch bản biết trước (`test_backtest_has_no_look_ahead_wallet_selection`,
`test_simulation_applies_costs_and_detects_liquidity_loss`). Chạy lại định kỳ: `docker compose run --rm api python -m radar backtest`;
kết quả hiển thị ở trang **Backtest** của dashboard.

## 7. Lỗi phát hiện & đã sửa trong lúc chạy thật

| Lỗi | Ảnh hưởng nếu không sửa | Sửa |
|---|---|---|
| `getTransaction` với `maxSupportedTransactionVersion: 0` | bỏ sót mọi tx version 1 | dùng 1 |
| Rate limiter phạt 429 quá lâu | thông lượng RPC giảm 4× | back-off nhân 1,5, hồi phục nhanh |
| Lưu toàn bộ lệnh PumpSwap | > 90% dung lượng là bot wash/arbitrage | chỉ lưu lệnh mua ≥ 2 SOL của ví lạ |
| Multi-hop qua curve định giá bằng token khác | lệnh "bán 0 SOL" + bán token trung gian không có vốn | gộp thành một lệnh với SOL thật |
| Swap qua router | ví thật chỉ thấy "chuyển token", mất PnL | quy về ví ký |
| Ví chưa backfill đạt Medium Confidence | có thể vào Top 10 chính thức với dữ liệu thiếu | chưa backfill ⇒ Low Confidence |
| Top 10 chính thức nhận ví < 65 điểm | ví "Không ưu tiên" xuất hiện trong Top | yêu cầu Score ≥ 65 |
| Backtest chỉ lấy ví có trạng thái hiện tại tốt | survivorship / look-ahead bias | lấy mọi ví đã backfill |
| Verifier xác minh cả ví ứng viên | tranh ngân sách RPC với backfill | ứng viên được xác minh qua backfill |
| Đếm tổng ví trong API | lỗi 500 danh sách ví | sửa tham số truy vấn |

## 8. Giới hạn còn lại

* Tốc độ backfill phụ thuộc RPC (công khai: vài phút/ví). Khuyến nghị Helius hoặc RPC trả phí cho vận hành thật.
* Không có Helius: không có phân bố holder (`top10_concentration = unknown`, cộng điểm rủi ro).
* Nến GeckoTerminal không có số lệnh/thanh khoản: xác nhận đỉnh dựa trên khối lượng ≥ 3 SOL/nến.
* Funding "đầu tiên" chỉ chắc chắn khi lịch sử ví nằm gọn trong cửa sổ backfill (`funding_is_first_ever`).
* Cảnh báo Telegram đã kiểm thử ở chế độ dashboard (không có bot token trong môi trường thử); gửi thật cần `TELEGRAM_BOT_TOKEN`
  (Cài đặt → Gửi tin nhắn thử).

## 9. Bổ sung Giai đoạn 1 (đánh giá phương pháp copy Smart Money)

| Tính năng | Kiểm thử |
|---|---|
| Average Sell SOL, tỷ lệ lợi nhuận đã chốt, 3x hit rate | chỉ số trong `performance.py`, hiển thị trang ví |
| Kiểm tra lặp lại theo hai nửa kỳ | `test_repeatability_split_halves` (4 thắng rồi 4 thua ⇒ không đạt) |
| Bảng kiểm Top 20 → Top 10, mỗi cụm một suất | `test_checklist_blocks_unrepeatable_or_inactive_wallets`, `test_one_slot_per_cluster_in_official_top` |
| Giá / MC / thời điểm vào của từng ví trong tín hiệu | `test_signal_records_each_buyers_entry_price_and_mc` |
| Cảnh báo thoát lệnh EXIT ≥ 50% / ≥ 90% | `test_exit_progress_weights_by_position_and_ignores_pre_signal_sells`, integration `test_exit_alert_when_signal_wallets_sell` (một lần mỗi mức) |

Kết quả bảng kiểm trên dữ liệu thật (kỳ 30 ngày, các ví đã backfill):

| Ví | Vị thế đóng | Win Rate | Realized PnL | Điểm | Trượt mục |
|---|---:|---:|---:|---:|---|
| `78FmcvKtbWEMTeZ8Z5jc1DjfskQ8oJNf6XgAyaf6tSPE` | 58 | 76% | +50,7 SOL | 64,5 | điểm < 65 (median buy 2,17 SOL, > 30 vị thế) |
| `ArkdCAPE…` | 31 | 94% | +24,6 SOL | 50,9 | điểm < 65: kiểu sniper lướt sóng, 0/12 token có giá đạt 2x sau khi mua |
| `4oN6EAEw…` | 9 | 89% | +1,8 SOL | 51,5 | không lặp lại ở 2 nửa kỳ, phụ thuộc một lệnh thắng |
| `499jWrZj…`, `8UGcQnU7…`, `E4BMFSG1…` | 54–55 | 42–50% | −2,4 … +21,3 SOL | < 42 | Win Rate < 65% (nhóm bot bundle) |

Chưa ví nào qua đủ bảng kiểm ⇒ Top 10 chính thức đang trống — đúng theo yêu cầu "không xếp ví thiếu năng lực/thiếu dữ liệu".
