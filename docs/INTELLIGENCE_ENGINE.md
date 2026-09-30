# MEMECOIN POTENTIAL INTELLIGENCE ENGINE — Tài liệu kỹ thuật (V2, Phase 1)

> Điểm số chỉ tổng hợp tín hiệu từ dữ liệu đã kiểm tra để hỗ trợ nghiên cứu. **Không phải dự đoán giá.**
> Không ví, không private key, không giao dịch.

## Trạng thái triển khai

| Phase | Nội dung | Trạng thái |
|---|---|---|
| **1** | Market + Momentum + Liquidity + Holder + Early Signal | **Đã triển khai, dữ liệu thật** |
| 2 | Dev ✓ (đã có từ V1, xác minh on-chain) · Whale ✓ (tính từ snapshot holder) · Wallet cluster ✗ · Smart money ✗ | Một phần — phần ✗ hiển thị **CHƯA CÓ / NOT AVAILABLE** |
| 3 | X / Telegram / Social / Narrative score | ✗ NOT AVAILABLE (chỉ có link trong metadata + nhãn narrative theo từ khoá = DỮ LIỆU, không chấm điểm) |
| 4 | Security (mint/freeze authority…) / Scam engine | ✗ NOT AVAILABLE — nhưng các cờ thao túng tính được từ dữ liệu Phase 1 đã có trong Risk |
| 5 | Backtest + kiểm chứng tín hiệu | Cơ bản: Opportunity **và** Early Signal, không nhìn trước tương lai |
| 6 | ML / anomaly | Chưa — cần dữ liệu lịch sử đủ lớn |

Module chưa triển khai **không bao giờ** được tính vào điểm như dữ liệu thật: nó bị loại khỏi mẫu số và hiển thị NOT AVAILABLE.

---

## 1. Architecture

```
                 ┌──────────── DISCOVERY ─────────────┐
 PumpPortal WS ─►│ new tokens (creator, dev initial buy)│
 Pump.fun API ──►│ latest + recently traded           │──► tracked tokens (dedup theo mint, max_tracked)
 DexScreener  ──►│ profiles (fallback khi Pump.fun chết)│
                 └────────────────────────────────────┘
                                   │ mỗi chu kỳ (mặc định 20 s)
                                   ▼
 DexScreener /tokens/v1 (batch 30) ─► DATA VALIDATION (validation/market.py) ─► HistoryStore (chỉ giá trị hợp lệ)
 Pump.fun /coins-v2 (curve reserve) ┘                                                │
                                   ▼                                                  │
 Deep worker (song song): ON-CHAIN VALIDATION ─ Helius DAS / Solana RPC ─► holder snapshot + dev balance ─┘
                                   ▼
 scanner/pipeline.evaluate():
   DATA QUALITY → MARKET STRUCTURE → LIQUIDITY → HOLDER → DEV → SMART MONEY(NA) → WHALE → SOCIAL(links)
   → NARRATIVE(tags) → SECURITY(NA) → RISK ENGINE → OPPORTUNITY ENGINE → EARLY SIGNAL ENGINE → LIFECYCLE
                                   ▼
 EventDetector (intel/events.py) ─► LIVE EVENTS + bảng events
                                   ▼
 RANKING (scoring/ranking.py): Top Opportunities = VALID only · Early Signals = không INVALID
                                   ▼
 SQLite snapshots/events/alerts · Telegram alert · PySide6 UI (vi/en) · API JSON nội bộ (127.0.0.1:8765)
```

Thư mục:

| Thư mục | Vai trò |
|---|---|
| `core/` | config, HTTP client (retry/backoff/throttle/health), models (Metric, SubScore, Event…) |
| `pumpfun/`, `dex/`, `solana_data/` | adapter nguồn dữ liệu (mỗi nguồn một module) |
| `validation/` | kiểm tra dữ liệu thị trường, Data Quality |
| `history/` | chuỗi thời gian trong bộ nhớ (≈2 h/token), snapshot holder, số dư dev |
| `intel/` | market, liquidity, holders, whales, early_signal, lifecycle, events, metrics (confidence) |
| `holders/`, `dev/` | lấy dữ liệu holder / dev on-chain |
| `scoring/` | sub-score, opportunity, ranking, filters |
| `risk/` | risk engine có danh mục |
| `scanner/` | engine (vòng quét, worker on-chain) + pipeline (thứ tự cố định) |
| `database/` | SQLite |
| `analytics/` | backtest, tổng hợp narrative |
| `api/` | API JSON chỉ đọc |
| `i18n/` | `vi.json`, `en.json` (sinh từ `tools/build_i18n.py`) |
| `ui/` | giao diện (không có chuỗi hard-code) |
| `smart_money/`, `social/`, `telegram_social/` | interface Phase 2/3 (NOT AVAILABLE) |

Hiệu năng: batch 30 token/request DexScreener, throttle theo host, retry + backoff (429/5xx), timeout 15 s,
cache lịch sử dev (10 phút) & ví nạp tiền, dedup theo mint, history có giới hạn (360 điểm), worker on-chain
tách khỏi vòng quét, publish bằng shallow copy, snapshot DB cho token ≥ ngưỡng MC.

---

## 2. Database schema (SQLite `data/hunter.db`)

```sql
tokens(mint PK, name, symbol, creator, created_at, first_seen, sources, twitter, telegram, website)

snapshots(id PK, ts, mint,
          price, mc, fdv, liquidity, liquidity_source,            -- giá trị ĐÃ KIỂM TRA (NULL nếu lỗi)
          vol_5m, vol_1h, buys_5m, sells_5m, txns_5m, pc_5m,
          holders, top10_pct, dev_pct, creator, top_holders(JSON),
          curve_progress, complete,
          score,            -- Opportunity tại thời điểm đó (NULL nếu INVALID)
          risk, early_signal, is_early, lifecycle,
          subscores(JSON),  -- {momentum, holder, liquidity, dev, whale, onchain, early_signal, ...}
          breakdown(JSON),  -- đóng góp Opportunity
          dq, dq_status,    -- Data Quality 0-100 + VALID/PARTIAL/INVALID
          liq_state, whale_state, holder_quality)
  INDEX (mint, ts), (score)

events(id PK, ts, mint, symbol, type, severity, params(JSON), source)   INDEX (ts), (mint, ts)
alerts(id PK, ts, mint, symbol, score, risk, mc, message, sent_telegram)
watchlist(mint PK, added_at, note)
```
Migration tự động (`ALTER TABLE ADD COLUMN`). Hàng cũ V1 có `dq_status = NULL` → không dùng làm tín hiệu backtest.

---

## 3. Data sources (kiểm tra thực tế 2026-09-29)

| Nguồn | Endpoint | Chính thức? | Dùng cho |
|---|---|---|---|
| PumpPortal | `wss://pumpportal.fun/api/data` `subscribeNewToken` | bên thứ 3, free | token mới realtime, creator, dev initial buy |
| Pump.fun | `frontend-api-v3.pump.fun/coins?sort=…`, `/coins-v2/{mint}`, `/coins?creator=` | **không chính thức** | discovery, reserve curve, ATH, lịch sử dev |
| DexScreener | `api.dexscreener.com/tokens/v1/solana/{≤30}` , `/token-profiles/latest/v1` | chính thức, free | giá, MC, FDV, liquidity, volume 5m/1h/6h/24h, buys/sells, Δ giá |
| Solana RPC | `getTokenAccountsByOwner`, `getBalance`, `getSignaturesForAddress`, `getTransaction`, `getTokenSupply` | chính thức | dev balance (xác minh), SOL, ví nạp tiền |
| Helius | DAS `getTokenAccounts` | cần key (free) | holder count, top 10/20/50, tập owner để tính churn/whale |
| Telegram Bot API | `sendMessage` | chính thức | alert |

Không có từ nguồn: volume 1m/15m, volume tách mua/bán, LP concentration → hiển thị **UNKNOWN** kèm lý do.

---

## 4. Scoring formula

Mọi sub-score: `round(100 × Σ điểm factor khả dụng / Σ max factor khả dụng)`. Factor thiếu dữ liệu → *unavailable*,
bị loại khỏi mẫu số (không bao giờ = 0 hay = tốt). Không còn factor nào → sub-score = `None` (UNKNOWN / NOT AVAILABLE).

**MOMENTUM** — vol_accel_1h 25 (1×→3×, nhịp 5m trung bình trong min(tuổi pair, 60 phút); pair < 10 phút → UNKNOWN) ·
vol_trend_5m 15 (vol5m hiện tại / vol5m 5 phút trước, 1×→3×) · buy_pressure 20 (B/S 1.0→2.0, 0 nếu < 10 GD) ·
txn_accel 15 · price_momentum 15 (Δ5m 0→+30%) · abs_volume 10 (log, 20%→200% mức lọc).
→ **khối lượng tuyệt đối và tăng tốc là hai factor riêng**; tuyệt đối chỉ chiếm 10/100.

**HOLDER** — distribution 30 (top10 <15/25/35/50 → 30/20/10/3/0) · growth_15m 25 · holder_accel 15 · early_retention 15 · holder_quality 15 (ORGANIC 15 / SUSPICIOUS 0).
**LIQUIDITY** — depth 30 · liq/MC 25 (AMM) · liq_trend 25 (GROWING 25 / STABLE 15 / FALLING, SHOCK 0) · slippage $1K 20.
**DEV** — dev_holding 40 · dev_selling 20 · dev_history 40 (chỉ dữ liệu đã xác minh).
**WHALE** — ACCUMULATION 80–100 · NEUTRAL 50 · DISTRIBUTION 0–20.
**ON-CHAIN** — trung bình HOLDER, DEV, WHALE, LIQUIDITY khả dụng.
**SMART MONEY / SOCIAL / NARRATIVE / SECURITY** — `None` (NOT AVAILABLE).

**OPPORTUNITY** (chỉ đo hoạt động/đà tăng; liquidity, tập trung holder, dev thuộc RISK):

```
parts: momentum 45 · holder_growth 25 (growth_15m, holder_accel, holder_quality) · whale 10
       · smart_money 10 (NA) · social 5 (NA) · narrative 5 (NA)
Opportunity = round( Σ w·S / Σ w )   trên các part khác None
coverage    = Σ w khả dụng
None nếu Data Quality = INVALID hoặc momentum không có.
```
Giải thích: mỗi factor đóng góp `points / Σmax × 100 × w / Σw` → danh sách "+18 Volume acceleration…" cộng lại ≈ tổng.

---

## 5. Risk formula

`RISK = min(100, Σ điểm)`; 0-30 THẤP · 31-60 TRUNG BÌNH · 61-80 CAO · 81-100 CỰC CAO (banner đỏ).
Risk theo danh mục = `min(100, 2 × Σ điểm trong danh mục)` → hiển thị **Rug risk**, **Manipulation risk**…

| Danh mục | Yếu tố (điểm) |
|---|---|
| data | invalid_market 20 |
| liquidity | liquidity_unknown 15 · low_liquidity 15/7 · mc_liq_high 15/7 (AMM) · bonding_curve 5 · liquidity_falling 8 · curve_drain 10 |
| rug | liquidity_shock 25 (AMM, giảm ≥30% trong ≤5 phút) · dev_dump 10 · dev_sold_recent 10 |
| holders | holders_unknown 10 · top10_high 25/15/7 · single_whale 10 · few_holders 8 · whale_distribution 10 · high_churn 10 |
| dev | dev_unknown 8 · dev_concentration 15/8 · serial_launcher 15 · dev_history_bad 8 · dev_snipe 10 |
| age | age_unknown 5 · new_token 5 (< 10 phút) |
| manipulation | volume_anomaly 10 · wash_pattern 8 · volume_collapse 10 · sudden_spike 8 · sell_pressure 8 · suspicious_holders 15 · volume_without_holders 10 · pump_without_liquidity 8 |
| social | no_socials 5 |

Chưa đo được (liệt kê rõ): wallet cluster, bundle/sniper, social spam, contract security. **Thiếu dữ liệu = cờ "unknown", không bao giờ = an toàn.**

---

## 6. Early Signal formula (V2.1 — đã áp dụng D1–D8)

Mỗi tín hiệu so sánh token **với chính nó ở thời điểm trước** (snapshot đã kiểm tra). Giá trị tuyệt đối hiện tại một mình **không bao giờ** kích hoạt tín hiệu.
**D5 — data break:** volume / giao dịch / lệnh mua / thanh khoản chỉ so sánh với snapshot **cùng pair DexScreener**. Khi token graduate/migrate (đổi pair) các tín hiệu này = KHÔNG RÕ ("DATA BREAK") cho tới khi pair mới có lịch sử riêng; sự kiện `DATA_BREAK` được ghi. Giá / MC là cấp token nên vẫn so sánh qua pair.

| Tín hiệu | Trọng số | Kích hoạt khi | Độ mạnh (0→1) |
|---|---|---|---|
| volume_accel | 25 | vol5m / vol5m 5 phút trước (cùng pair) ≥ 2.0, vol5m ≥ $2K, **và chiều OK (D1)** | 1.5→5.0 |
| txn_accel | 15 | txns ratio (cùng pair) ≥ 1.8, txns ≥ 15, **và chiều OK (D1)** | 1.3→4.0 |
| buy_pressure | 15 | **tỉ trọng mua = mua/(mua+bán) (D2)** tăng ≥ +10 điểm % so với 5 phút trước và ≥ 55% | 5→30 điểm % |
| mc_accel | 15 | tăng MC 5 phút ≥ 15% và > 5 phút trước đó | 10→60% |
| holder_accel | 20 | holder +≥5% trong 5 phút, > 5 phút trước, **≥ +10 holder (D7)**; chỉ khi dữ liệu holder hợp lệ (D6) và **≥ 50 holder (D7)** | 3→20% |
| liquidity_growth | 10 | thanh khoản (cùng pair) +≥10% so với 10 phút trước | 5→50% |
| whale_accum | 10 | ACCUMULATION với dữ liệu hợp lệ, ≥ 50 holder và **≥ +10 holder cùng khung (D3/D7)**; **không cộng nếu holder ĐÁNG NGỜ hoặc token INVALID (D8)** | Δ 1→5% supply |
| smart_money / social_accel / narrative_accel | 0 | NOT AVAILABLE | — |

**Chiều OK (D1)** = tỉ trọng mua hiện tại ≥ 50% **hoặc** MC 5 phút ≥ 0%. Spike volume/giao dịch trong lúc bán tháo (mua < 50% và MC giảm) không bật. Không đo được cả hai → KHÔNG RÕ.
`fired = None` (KHÔNG RÕ / không đủ điều kiện) → loại khỏi mẫu số; `fired = False` → 0 điểm nhưng vẫn tính mẫu số.

```
TRANSITION  = median(vol5m cùng pair, 10–30 phút trước; ≥3 điểm) < $10K  và  vol5m(now) ≥ 3 × median   (không đổi)
STRENGTH    = round(100 × Σ w·strength (tín hiệu đã bắn) / Σ w (tín hiệu tính được))
EARLY SIGNAL = TRUE ⇔ TRANSITION ∧ ≥3 tín hiệu ∧ STRENGTH ≥ 50 ∧ Data Quality ≠ INVALID ∧ KHÔNG BỊ KÌM (D4)
D4 KÌM khi: có cờ rủi ro nhóm RUG (liquidity_shock, dev_dump, dev_sold_recent) · top10 > 35% (dữ liệu holder hợp lệ) · Risk > 60
            (lý do luôn được ghi trong `suppressed` và hiển thị trong UI / audit)
UNKNOWN (strength = None) khi lịch sử < 10 phút, hoặc khi ÍT HƠN 4/7 nhóm tín hiệu tính được
        (volume, giao dịch, lực mua, MC, holder, thanh khoản, cá voi) → "không đủ dữ liệu"; không bao giờ = 0,
        không bao giờ cho điểm cao từ 1–3 thành phần.
```

**D6 — dữ liệu holder không hợp lệ** (bị loại, không vào lịch sử, không tính điểm; hiển thị "dữ liệu holder không hợp lệ: …"):
top10 > 100% nguồn cung · số holder giảm > 50% trong ≤ 6 phút · DAS thiếu trang giữa chừng.
**Momentum sub-score** cũng áp dụng D1 (vol/txn accel = 0 khi bán tháo: tỉ trọng mua < 50% và giá 5m < 0) và D2 (buy pressure = tỉ trọng mua 50% → 67%).
**Holder / Whale sub-score** áp dụng D6/D7/D8 (≥ 50 holder, tăng ≥ +10 holder, không ĐÁNG NGỜ, token không INVALID).
Hàng đợi kiểm tra on-chain: **15 token / vòng** (trước là 8).

**Lifecycle** (luật theo thứ tự): UNKNOWN → NEW (< 10 phút) → DECLINING (drawdown ≥ 60% & nhịp < 0.8×) →
DISTRIBUTION (≥ 30 phút & (cá voi xả hoặc B/S < 0.8, ≥ 20 GD, Δ1h ≤ +5%)) → EARLY_MOMENTUM (early = TRUE) →
BREAKOUT (giá ≥ 1.10 × đỉnh 2–30 phút trước, nhịp vol ≥ 2×) → MOMENTUM (nhịp ≥ 1.5× & Δ1h ≥ +20%) → EARLY (< 60 phút) → MATURE.

---

## 7. Data validation rules

Critical → field = NULL, Data Quality = **INVALID**, không Opportunity, không xếp hạng, không alert, không EARLY = TRUE, không làm tín hiệu backtest:

- thiếu pair address / DEX id
- price thiếu / ≤ 0 / NaN / chuỗi rác
- MC thiếu / ≤ 0 / < $1,000; MC lệch > ×3 so với price × supply
- volume 5m hoặc 1h thiếu / ≤ 0
- thiếu buys/sells 5m; có volume nhưng 0 giao dịch
- liquidity AMM thiếu / ≤ 0 / < $100
- bonding curve: không có dữ liệu Pump.fun; dữ liệu > 180 s; `virtual − real ≠ 30 SOL (±1)` (vd. mayhem mode); thiếu giá SOL; reserve < $100
- dữ liệu thị trường > max(60 s, 3 × chu kỳ quét)

Warning (giảm điểm DQ): FDV ≤ 0, thiếu buys/sells 1h, dữ liệu > 1.5 × chu kỳ, thiếu holder, dev chưa xác minh, không có nguồn social.
DQ: −40 mỗi critical, −10 mỗi warning, −5 thiếu social → VALID ≥ 80, PARTIAL còn lại. Chỉ VALID vào Top Opportunities.

Mỗi metric có **VALUE · SOURCE · TIMESTAMP · DATA AGE · CONFIDENCE**:
`confidence = (1.0 nếu API báo trực tiếp | 0.8 nếu chúng tôi tính) × freshness × coverage`,
freshness = 1 khi tuổi ≤ 1.5 × chu kỳ, giảm tuyến tính còn 0.3 ở 5 × chu kỳ. Giá trị NULL → confidence NULL.
Chỉ giá trị đã kiểm tra được đưa vào chuỗi thời gian → mọi phép tính tăng tốc/sự kiện đều dựa trên dữ liệu hợp lệ.

Token < 10 phút: DexScreener "h1" chỉ là toàn bộ đời token → `vol5m / (vol1h/12)` sẽ giả 12× ⇒ tăng tốc tính theo cửa sổ thực `min(tuổi pair, 60 phút)`, < 10 phút = UNKNOWN.

---

## 8. Event detection rules

Mỗi (token, loại) tối đa 1 lần / 10 phút. Chỉ tính trên giá trị hợp lệ.

| Sự kiện | Luật | Mức |
|---|---|---|
| VOLUME_SPIKE | vol5m ≥ 3 × vol5m 5 phút trước và ≥ $5K | positive |
| BUY_PRESSURE_SPIKE | B/S ≥ 2.0 (≥ 20 GD) và B/S 5 phút trước < 1.3 | positive |
| HOLDER_SPIKE | holder +15% và ≥ +20 so với snapshot ~5 phút trước | positive |
| WHALE_ENTRY / WHALE_EXIT | ví vượt / rơi khỏi 1% supply giữa hai snapshot holder | info / warning |
| DEV_SELL | số dư dev (RPC đã xác minh) giảm ≥ 5% giữa hai lần kiểm tra | warning |
| LIQUIDITY_ADD / REMOVE | **pool AMM ≥ $5K**: ±25% so với điểm trước (≤ 2 phút) | info / warning |
| BREAKOUT / DISTRIBUTION | lifecycle chuyển sang giai đoạn đó | positive / warning |
| RUG_WARNING | liquidity AMM −50% trong 5 phút; hoặc giá −60% trong 5 phút; hoặc dev bán hết + giá −40% | critical |
| DATA_BREAK | pair DexScreener thay đổi (graduate / migrate) — lịch sử theo pair bắt đầu lại | info |
| SMART_MONEY_ENTRY, SOCIAL_SPIKE, NARRATIVE_SPIKE | NOT AVAILABLE | — |

Bonding curve không có LP để rút: reserve giảm = bán tháo → cờ risk `curve_drain`, không phải LIQUIDITY_REMOVE.

---

## 9. API endpoints

API JSON **chỉ đọc**, `127.0.0.1:8765` (bật/tắt + cổng trong Cài đặt):

| Endpoint | Trả về |
|---|---|
| `GET /api/health` | tình trạng từng nguồn, số token, chu kỳ, thời điểm quét cuối, giá SOL |
| `GET /api/tokens?status=VALID&limit=100` | danh sách token (tóm tắt, lọc theo Data Quality) |
| `GET /api/tokens/{mint}` | chi tiết: mọi metric (value/source/timestamp/age_s/confidence), sub-score + factor, đóng góp Opportunity, risk, early signal, issue DQ, sự kiện, stamp nguồn |
| `GET /api/top?limit=20` | Top Opportunities (chỉ VALID) |
| `GET /api/early?limit=20` | xếp hạng Early Signal |
| `GET /api/events?limit=100` | sự kiện gần nhất |
| `GET /api/narratives` | tổng hợp narrative trên token đang theo dõi |

External endpoints: xem mục 3.

---

## 10. Test cases (`python -m pytest`, 112 offline + 3 live `--live`)

| File | Chứng minh |
|---|---|
| `test_validation.py` | **17 kiểu dữ liệu lỗi** (gồm liquidity $0.00000071 và $0.000000024 quan sát được, MC $265.91) → INVALID, không Opportunity, **không vào Top Opportunities**; giá trị lỗi = NULL không phải 0; UNKNOWN không có confidence; risk thấp không cứu dữ liệu lỗi; **dữ liệu lỗi không bao giờ EARLY = TRUE**; history chỉ lưu giá trị hợp lệ; curve mayhem/stale/thiếu giá SOL; dữ liệu cũ → INVALID; mọi metric có source + timestamp + confidence |
| `test_early_signal.py` | **early signal dựa trên thay đổi theo thời gian**: nền thấp → tăng tốc = EARLY; khối lượng lớn nhưng phẳng = không EARLY; **cùng snapshot hiện tại, hai quá khứ khác nhau → kết quả khác nhau**; 1 snapshot = UNKNOWN (không phải False); lịch sử ngắn = UNKNOWN; đang giảm = không EARLY; module chưa có bị loại |
| `test_scoring.py` | đủ 14 chiều điểm; factor UNKNOWN bị loại khỏi mẫu số; Opportunity chỉ dùng trọng số khả dụng; đóng góp cộng lại = tổng; risk/liquidity/tập trung không đổi Opportunity; **$10K tăng tốc > $50K phẳng về momentum** |
| `test_risk.py` | mức rủi ro; cờ + danh mục + nguồn; UNKNOWN bị gắn cờ; dev chưa xác minh = UNKNOWN không phải 0% |
| `test_intel.py` | trạng thái liquidity (UNKNOWN/STABLE/GROWING/FALLING/SHOCK); slippage x·y=k; holder growth/accel/churn/retention/ORGANIC; airdrop bụi = SUSPICIOUS; whale ACCUMULATION/DISTRIBUTION; lifecycle; sự kiện + chống trùng; curve drain ≠ rút LP |
| `test_parsers.py` | parser Pump.fun/DexScreener/PumpPortal; trường thiếu = None; **token 4 phút không có tăng tốc giả 12×** |
| `test_holders_dev.py` | loại curve/pool khỏi tập trung; RPC lỗi → dev UNKNOWN; supply không rõ không bị giả định |
| `test_database_backtest.py` | backtest không nhìn trước; chỉ tín hiệu VALID; giá INVALID không làm kết quả; backtest Early Signal; migration; events |
| `test_i18n_api.py` | vi.json ≡ en.json (cùng key); mọi key dùng trong code tồn tại; template format đủ tham số cả 2 ngôn ngữ; API chỉ trả VALID ở `/api/top` |

## 11. Web-2: refresh theo tầng, 4 nhóm, hồ sơ coin

**Refresh theo tầng** (`scanner/scheduler.py`). Ưu tiên chỉ quyết định KHI NÀO lấy dữ liệu, không đổi điểm nào.

| Tầng | Ưu tiên (hot) | Thường | Yên | Nguồn |
|---|---|---|---|---|
| Phát hiện | ~5s (WS PumpPortal); Pump.fun latest 10s, recently-traded 20s | | | PumpPortal, Pump.fun |
| Giá/MC/Volume/Txn/Buy-sell/Liq | 5s | 10s | 30s | DexScreener `/tokens/v1` (batch 30) |
| Holder/Whale | 30s | 60s | — | Helius DAS (chỉ token ứng viên, tối đa `deep_max_per_min`=45/phút) |
| Dev | 60s | 120s | — | Solana RPC + Pump.fun (lịch sử cache) |
| Snapshot SQLite + alert | 20s | | | |

Token **hot** được xếp theo thứ tự: ⭐ đang theo dõi, mới (<15 phút), MC tăng nhanh (≥ +20%/5m), volume tăng (≥ 2×), buy pressure tăng (≥ +10pp), holder tăng (≥ +10/5m), nhóm Cơ hội, nhóm Theo dõi. **Yên**: volume 5m < $500 và < 10 txn.

**Early Signal không đổi.** `history.store` giữ một điểm neo ~20s (POINT_SPACING_S=18) và điểm mới nhất trượt; holder snapshot neo ~90s. Early Signal/D1–D8 vì vậy thấy chuỗi dữ liệu cùng mật độ, cùng độ sâu như trước. Test `test_faster_refresh_gives_identical_early_signal` chứng minh feed 5s và 20s cho kết quả giống hệt.

**Chống lỗi API** (`core/http.py`): timeout riêng mỗi request, retry với exponential backoff (tôn trọng Retry-After), throttle theo host, cooldown theo nguồn 5s → 10s → … → 120s sau lỗi 429/5xx/mạng/timeout (4xx không kích hoạt). Tầng bị lỗi tự lùi lịch (×2, tối đa 120s); dữ liệu cũ được giữ, không bị xoá.

**4 nhóm** (`scoring/groups.py`, chỉ đọc kết quả có sẵn): ⛔ Loại (INVALID, Risk > 60, cờ rug, liquidity SHOCK, holder bất thường, top10 > `max_top10_pct`) → ⏳ Chưa đủ dữ liệu (chưa có market hoặc Early UNKNOWN) → 🔥 Cơ hội (VALID + holder đã xác minh + không bị kìm + [Early TRUE hoặc ≥3 tín hiệu, độ mạnh ≥50, Opportunity ≥60]) → 👀 Theo dõi (≥1 tín hiệu hoặc Opportunity ≥40) → yên (ẩn). Không phải khuyến nghị mua/bán.

**MC ban đầu** (`intel/mc_track.py`, bảng `mc_track`): MC hợp lệ đầu tiên (không có lỗi critical) sau khi phát hiện; ghi một lần, không bao giờ ghi đè (SQL `COALESCE`), khôi phục sau restart. Lịch sử MC = các mốc thay đổi ≥30%. **MC kịch bản** = 3 mốc MC chuẩn kế tiếp kèm hệ số cần đạt, và tham chiếu thật (đỉnh đã ghi nhận, ATH Pump.fun, token tốt nhất của dev nếu lịch sử đã xác minh). Không có xác suất, không phải dự đoán. Không có MC hợp lệ thì hiển thị "Chưa đủ dữ liệu".

API mới: `GET /api/home` (4 nhóm + hồ sơ coin), `GET /api/status` → `refresh` (chu kỳ mục tiêu và thực đo). Ví liên quan của dev và hoạt động X/Telegram: NOT AVAILABLE, không đoán.

## 12. Canonical token identity (`validation/identity.py`)

Sự cố 2026-09-30: CA `XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB` (Tesla xStock, TSLAx, Token-2022) bị hiển thị là `$APEWIF`. APEWIF thật là mint `FUgEk…aDtT`, một curve Pump.fun được định giá bằng TSLAx. Feed discovery đã gắn tên APEWIF vào CA của đồng quote.

- **Nguồn tra theo CA (canonical):** Helius `getAsset` (metadata on-chain; gọi 1 lần/token, chỉ với token trong nhóm quét sâu), bản ghi Pump.fun có `mint` = CA, pair DexScreener có `baseToken.address` = CA.
- **Chỉ là khai báo (claim):** PumpPortal.
- **Trạng thái:**
  - `UNVERIFIED`: chưa có nguồn canonical → không bao giờ vào 🔥.
  - `VERIFIED`: mọi nguồn cùng một symbol.
  - `CONFLICT`: symbol khác nhau → Data Quality critical `identity_conflict` → INVALID, nhóm ⛔; không quét holder (Helius), không neo MC.
- **So sánh symbol:** chuẩn hoá NFKC, bỏ khoảng trắng, bỏ "$", không phân biệt hoa/thường. Name không được so.
- **Hiển thị:** luôn dùng symbol/name canonical (ưu tiên Helius > Pump.fun > DexScreener).
- **MC ban đầu:** `marketCapSol` của PumpPortal tính bằng đơn vị quote của curve. Chỉ được dùng khi quote đã được xác nhận là SOL (qua Pump.fun `quote_mint` hoặc quote của pair DexScreener). Nếu quote khác SOL thì huỷ.
- **Test:** `tests/test_identity.py` là regression cho đúng CA này, có cả test live.
