# SOL MEMECOIN HUNTER — Memecoin Potential Intelligence Engine (V2)

**Tài liệu kỹ thuật đầy đủ (architecture, schema, nguồn dữ liệu, công thức score / risk / early signal,
luật kiểm tra dữ liệu, luật sự kiện, API, test): [docs/INTELLIGENCE_ENGINE.md](docs/INTELLIGENCE_ENGINE.md)**

V2 thêm: chuỗi thời gian mỗi token, Early Signal engine (dựa trên thay đổi theo thời gian), lifecycle,
14 chiều điểm, intelligence thanh khoản / holder / cá voi, LIVE EVENTS, mỗi metric có nguồn + thời gian + tuổi + độ tin cậy,
giao diện 🇻🇳 Tiếng Việt / 🇺🇸 English (`src/i18n/vi.json`, `en.json`, sửa qua `tools/build_i18n.py`),
API JSON chỉ đọc tại `http://127.0.0.1:8765/api/health`.

```
run_windows.bat --check <MINT> --lang vi     # báo cáo đầy đủ 1 token
run_windows.bat --backtest early             # backtest Early Signal
```

Phần mềm **research / scanner** tìm memecoin Solana mới (ưu tiên Pump.fun) trên Windows.

> **Không giao dịch thật.** Có một bot **PAPER** (giấy): mua/bán giả lập trên báo giá Jupiter thật, không private
> key, không seed phrase, không ký transaction, không gửi lệnh nào lên chain. Chế độ AUTO (lệnh thật) bị khoá cứng.
> Score chỉ là **công cụ xếp hạng để bạn tự nghiên cứu**, không phải dự đoán giá.

---

## 0. Bot PAPER trên server (Render) — chạy, cấu hình, đọc báo cáo

**Bot này là PAPER bot.** Mọi lệnh khớp trên giấy: BUY/SELL dùng báo giá Jupiter thật (chỉ quote), phí mạng + phí
ưu tiên được trừ như thật, thiếu báo giá bán thì xử lý theo luật (retry, haircut 30 % chỉ khi không có đường đi
hoặc lệnh HARD khẩn cấp sau 60 s). Không có ví, không có giao dịch thật.

Chạy server cục bộ (giao diện PWA + scanner + bot paper trong cùng một tiến trình):
```
python src/main.py --web --host 127.0.0.1 --port 8765
```
Trên Render: `render.yaml` (gói free, `autoDeploy` từ `main`). Mỗi lần deploy làm theo
[docs/deploy_checklist.md](docs/deploy_checklist.md). Commit nào không được deploy mang `[skip render]`.

### Biến môi trường

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `APP_ACCESS_CODE` | có | mã truy cập `/api/*` (nhập một lần trên điện thoại) |
| `HELIUS_API_KEY` | khuyên dùng | holder / whale / RPC (chỉ ở server, không bao giờ xuống frontend) |
| `SNAPSHOT_URL` + `SNAPSHOT_TOKEN` | để mẫu BỀN | kho snapshot HTTP (GET/PUT, token Bearer); hoặc `SNAPSHOT_DIR` trên đĩa cố định. Thiếu -> "MẪU KHÔNG BỀN - sẽ mất khi restart" |
| `SNAPSHOT_EVERY_S` | không | chu kỳ snapshot (mặc định 3600) |
| `RENDER_EXTERNAL_URL` | tự có trên Render | keep-alive: tự ping 10 phút/lần để gói free không ngủ |
| `LIFECYCLE_ENGINE` | không (`1`) | engine vào lệnh Lifecycle (production) |
| `EXPERIMENTAL_MODE`, `LATENCY_PROBE`, `LATENCY_SLIPPAGE_MODEL` | không | chế độ thử nghiệm / đo trễ (paper) |
| `RESEARCH_LOG`, `MONEYFLOW`, `RESEARCH_ONCHAIN` | không (`1`) | ghi dữ liệu nghiên cứu `research.db` (chỉ đọc, có ngân sách) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | không | cảnh báo Telegram |

Không bao giờ đưa key / token vào repo, log, `render.yaml` hay ảnh chụp màn hình.

### Đọc báo cáo mẫu

* `GET /healthz` (công khai): `ok`, `role` (ACTIVE / STANDBY / HALTED / LEASE LOST), commit, `params`
  (fingerprint tham số), `constants` (hash hằng chiến lược), `sample_epoch`, trạng thái snapshot + cảnh báo bền.
* Báo cáo mẫu (`/api/bot` -> `sample_report`, hoặc `python tools/sample_report.py --book .. --epoch ..`):
  dòng đầu `SAMPLE <INSUFFICIENT|PRELIMINARY|OK> n=.. · <kết luận> · net expectancy @5% / @7% / @10% [CI 95 %]`.
  * **Thước đo chính:** net expectancy mỗi lệnh sau chi phí 5 / 7 / 10 %, CI 95 % bootstrap và CI theo token
    (các lệnh cùng token tương quan). INSUFFICIENT < 30, PRELIMINARY 30-199, OK >= 200.
  * **Kết luận:** "Chưa đủ dữ liệu để kết luận có lãi" cho tới khi n >= 200 VÀ mọi CI (thường + theo token, mọi
    mức chi phí) nằm hẳn một phía của 0.
  * Kèm: win rate, R:R, max drawdown, kịch bản SL gap -20 %, nhóm haircut (và kết quả khi bỏ nhóm này), phí cố định
    % mỗi lệnh, tỉ lệ token bị cổng chặn theo lý do, forward return nhóm bị chặn vs nhóm được vào vs baseline cùng
    tuổi / thanh khoản, GAP (bot không chạy > 5 phút: lệnh trùng GAP bị loại khỏi mẫu).
  * Chỉ lệnh của epoch hiện tại được tính (`STRATEGY_VERSION` + fingerprint); TP/(TP+SL) chỉ để tham khảo.
* Bản tóm tắt cho người review (không cần vào server): `tools/export_review_bundle.py` ->
  [docs/review_bundle.md](docs/review_bundle.md). Kế hoạch mẫu: [docs/sample_plan.md](docs/sample_plan.md).
* Replay walk-forward + holdout: `python tools/replay_tp_sl.py --db data/research.db --horizon 30m ...`.

---

## 1. Kiểm tra API (thực hiện ngày 2026-09-29, đã gọi thử từng endpoint)

| Nguồn | Endpoint | Kết quả | Dùng cho |
|---|---|---|---|
| **PumpPortal** (bên thứ 3, free) | `wss://pumpportal.fun/api/data` → `subscribeNewToken` | ✅ Hoạt động, realtime, có ví creator + **initial buy của dev** | Phát hiện token mới ngay lập tức |
| **Pump.fun** frontend API (**không chính thức**) | `frontend-api-v3.pump.fun/coins?sort=created_timestamp…` | ✅ (rate-limit 429 nếu gọi nhanh) | Discovery, bonding curve |
| | `/coins-v2/{mint}` | ✅ | Chi tiết coin, pool PumpSwap, ATH |
| | `/coins?creator={wallet}` | ✅ | Lịch sử token của dev |
| | `/coins/{mint}` (endpoint cũ trên mạng) | ❌ 404 | **Không dùng** |
| | `frontend-api-v2`, `frontend-api`, `advanced-api-v2` | ❌ 403 / 530 | **Không dùng** |
| | `/trades/all/{mint}` | ❌ 400 (yêu cầu tham số mới, không có tài liệu) | Không dùng |
| **Pump.fun API key** | — | ❌ Không tồn tại API chính thức / API key | `PUMPFUN_API_KEY` để trống |
| **DexScreener** (chính thức, free) | `api.dexscreener.com/tokens/v1/solana/{≤30 mint}` | ✅ Có cả pair bonding curve (`dexId=pumpfun`) và PumpSwap | Giá, MC, FDV, liquidity, volume, buys/sells |
| | `/token-profiles/latest/v1` | ✅ | Discovery dự phòng khi Pump.fun chết |
| **DexScreener API key** | — | ❌ Không cần | `DEXSCREENER_API_KEY` để trống |
| **Solana public RPC** | `getBalance`, `getTokenAccountsByOwner`, `getSignaturesForAddress`, `getTransaction`, `getTokenSupply` | ✅ | Dev wallet, funding |
| | `getTokenLargestAccounts` | ❌ 429 trên RPC public (publicnode, drpc cũng chặn) | → cần Helius |
| **Helius** (free tier đủ dùng) | DAS `getTokenAccounts` | Cần `HELIUS_API_KEY` | Holder count + top 50 holder |
| **Birdeye** | — | Cần key trả phí; Phase 1 không cần | Phase 2 |
| **X / Twitter** | API v2 recent search | Cần gói trả phí. **Không scrape x.com** (vi phạm ToS) | Phase 2 |
| **Telegram** | Bot API `sendMessage` | ✅ chính thức | Alert |
| | MTProto (Telethon, tài khoản của bạn) | Cần `API_ID/HASH` | Phase 2 social |

**Lưu ý quan trọng:** Pump.fun frontend API là API nội bộ của website, có thể đổi bất kỳ lúc nào.
Vì vậy mọi nguồn đều nằm trong adapter riêng và scanner **vẫn chạy khi một nguồn chết**:
Pump.fun chết → PumpPortal websocket; cả hai chết → DexScreener discovery; DexScreener lỗi → retry
+ giữ dữ liệu cũ; RPC lỗi → thử endpoint tiếp theo (Helius → SOLANA_RPC_URL → public).

---

## 2. Cài đặt & chạy trên Windows

### Cách A: chạy từ source (khuyên dùng)
1. Cài **Python 3.11+** từ python.org (tick *Add python.exe to PATH*).
2. Double-click **`run_windows.bat`** → tự tạo `.venv`, cài thư viện, tạo `.env`, mở dashboard.
3. (Khuyên dùng) Lấy Helius key free tại https://dashboard.helius.dev → điền `HELIUS_API_KEY=` trong `.env` → chạy lại.

Chế độ dòng lệnh:
```
run_windows.bat --check 9pMXEbTjQ5HHiYifGbNBwkMkrQxGyhuSmB8ndKNXpump   # báo cáo 1 token
run_windows.bat --headless                                              # scanner trong console
run_windows.bat --backtest                                              # backtest dữ liệu đã lưu
```

### Cách B: file EXE
1. Double-click **`build_windows.bat`** → chạy test → build **`dist\SOL_Memecoin_Hunter.exe`** (~70 MB, 1 file).
2. Đặt file `.env` cạnh file exe. Database + settings lưu ở `dist\data\`.

### Telegram alert
1. Chat với **@BotFather** → `/newbot` → copy token vào `TELEGRAM_BOT_TOKEN`.
2. Gửi 1 tin nhắn bất kỳ cho bot, mở `https://api.telegram.org/bot<TOKEN>/getUpdates`, lấy `chat.id` → `TELEGRAM_CHAT_ID`.
3. Không có Telegram, alert vẫn hiện trong tab **ALERTS**.

---

## 3. Kiến trúc

```
src/
  main.py                 entry: GUI | --check | --headless | --backtest
  core/config.py          .env keys + settings.json (filters, interval, alerts)
  core/http.py            httpx client chung: retry, backoff 429/5xx, throttle theo host, health từng nguồn
  core/models.py          dataclass: TokenInfo, MarketData, HolderStats, DevReport, Score/Risk
  pumpfun/client.py       adapter Pump.fun frontend-api-v3
  pumpfun/stream.py       adapter PumpPortal websocket (token mới realtime)
  dex/dexscreener.py      adapter DexScreener
  solana_data/rpc.py      adapter Solana RPC + Helius DAS (fallback nhiều endpoint)
  holders/analyzer.py     top 5/10/20/50, loại trừ bonding curve / pool / burn
  dev/analyzer.py         creator → funding wallet → SOL → initial buy → balance → sold % → token cũ
  scoring/opportunity.py  Opportunity Score minh bạch
  scoring/filters.py      bộ lọc (chỉ đánh dấu, không xoá coin; giá trị không rõ = không đạt)
  scoring/ranking.py      xếp hạng: chỉ token VALID
  validation/market.py    kiểm tra dữ liệu thị trường (null hoá giá trị lỗi)
  validation/quality.py   Data Quality 0-100 + VALID/PARTIAL/INVALID
  scanner/pipeline.py     thứ tự pipeline cố định
  risk/engine.py          Risk Score riêng, từng yếu tố có giải thích
  database/db.py          SQLite: tokens, snapshots, alerts, watchlist
  analytics/backtest.py   backtest không look-ahead
  alerts/report.py        báo cáo DATA vs INTERPRETATION
  alerts/telegram.py      Telegram Bot API
  scanner/engine.py       vòng quét chính + worker phân tích sâu chạy song song
  smart_money/ social/ telegram_social/   interface Phase 2 (chưa có dữ liệu, ghi rõ "Phase 2")
  ui/                     PySide6 dashboard (10 tab) + pyqtgraph chart
tests/                    67 unit test offline + 3 live test (--live)
```

**Luồng mỗi chu kỳ (mặc định 20s):** discover (PumpPortal queue + Pump.fun latest & recently-traded)
→ DexScreener batch 30 token/lần (+ giá SOL) → prune (token > 6h, token chết < $5K sau 15 phút)
→ score + risk + filter → lưu snapshot SQLite → alert → cập nhật UI.
**Worker phân tích sâu** (holder + dev) chạy riêng, ưu tiên watchlist và token có volume lớn,
nên RPC chậm không làm chậm vòng quét giá.

---

## 4. Pipeline & kiểm tra dữ liệu (V1.1)

```
DISCOVERY → MARKET DATA VALIDATION → DATA QUALITY → ON-CHAIN VALIDATION → SOCIAL VALIDATION
          → OPPORTUNITY SCORE → RISK SCORE → RANKING
```
Code: `scanner/pipeline.py` (thứ tự cố định), `validation/market.py`, `validation/quality.py`, `scoring/ranking.py`.
Không bao giờ tính score trên dữ liệu thô rồi "sửa" sau.

**Market validation** (`validation/market.py`). Field lỗi → `None` (không bao giờ 0) + ghi Issue:

| Kiểm tra | Mức |
|---|---|
| price thiếu / ≤0 / NaN / chuỗi rác | critical |
| MC thiếu / ≤0 / < $1,000 (curve Pump.fun bắt đầu ≈ $3K) | critical |
| MC lệch > ×3 so với price × supply | critical |
| volume 5m hoặc 1h thiếu / ≤0 | critical |
| buys/sells 5m thiếu, hoặc có volume nhưng 0 giao dịch | critical |
| pair address / DEX id thiếu | critical |
| liquidity AMM thiếu / ≤0 / < $100 | critical |
| bonding curve: không có dữ liệu Pump.fun, dữ liệu > 180s, **virtual − real ≠ 30 SOL (±1)** (vd. mayhem mode), không có giá SOL, reserve < $100 | critical |
| DexScreener không cập nhật > 3 × chu kỳ quét | critical |
| FDV ≤0, buys/sells 1h thiếu | warning |

Liquidity token trên curve = **SOL thực trong curve do Pump.fun báo × giá SOL** (có nhãn "curve reserve"),
chỉ dùng khi qua hết kiểm tra ở trên. Không còn ước tính "2 × curve".

**Nguyên nhân lỗi "liquidity $0.00000071":** token Pump.fun *mayhem mode* có `real_sol_reserves = 3 lamports`
nhưng `virtual_sol_reserves = 256.8 SOL`, và chính Pump.fun báo `canonical_pool_liquidity_usd = 3.57e-07`.
Bản V1 nhân 2 thành 7.1e-07. Nay bị bắt bởi kiểm tra "virtual − real ≠ 30 SOL" → INVALID.

**Data Quality 0–100** (`validation/quality.py`): −40 mỗi lỗi critical, −10 mỗi warning, −10 nếu dữ liệu thị trường
> 1.5 chu kỳ, −10 thiếu holder, −10 dev chưa xác minh on-chain, −5 không có nguồn hoạt động X/Telegram.
- **INVALID**: có lỗi critical → không chấm Opportunity, không xếp hạng, không alert, không làm signal backtest.
- **VALID**: không lỗi critical và ≥ 80.
- **PARTIAL**: còn lại → có score, hiện trong bảng, **không** vào Top Opportunities.

Mỗi token hiển thị nguồn + giờ cập nhật + tuổi dữ liệu (giây): Market (DexScreener), Bonding curve (Pump.fun),
Holders (Helius/RPC), Dev (Solana RPC), Social links (metadata). X/Telegram & Smart money = UNAVAILABLE.

**Không có giá trị giả:** dev chỉ hiện % khi RPC thực sự trả lời (`balance_verified`), nếu không → `DEV: UNKNOWN`.
Supply không rõ → không giả định 1 tỷ. Smart money / Social → `NOT AVAILABLE` và không tính vào score.

## 5. Opportunity Score (0–100) — chỉ đo momentum / hoạt động

| Thành phần | Max | Quy tắc | Nguồn |
|---|---|---|---|
| Volume momentum | 20 | Vol 5m từ 20%→200% mức lọc (tới 12) + vòng quay Vol5m/MC 1%→15% (tới 8) | DexScreener |
| Volume acceleration | 20 | Vol 5m so với nhịp 5m trung bình của 1h: 1× → 3× | DexScreener |
| Buy/Sell imbalance | 15 | B/S 5m 1.0 → 2.0 (0 điểm nếu < 10 giao dịch; không có sell → không tính) | DexScreener |
| Transaction activity | 15 | Số giao dịch 5m 20→300 (log, tới 9) + gia tốc giao dịch 1×→3× (tới 6) | DexScreener |
| Holder growth | 10 | +0% → +30% so với snapshot ≥ 5 phút trước | Helius + DB |
| Smart money | 10 | **NOT AVAILABLE** — loại khỏi mẫu số | — |
| Social momentum | 10 | **NOT AVAILABLE** — loại khỏi mẫu số (có link ≠ momentum) | — |

`Opportunity = 100 × điểm đạt / tổng max của thành phần có dữ liệu`. **Không còn risk adjustment**: risk thấp
không thể nâng Opportunity, và dữ liệu INVALID không có Opportunity. Top Opportunities = VALID, sắp theo Opportunity
(hoà thì risk thấp hơn, rồi DQ cao hơn). MC/liquidity/holder/dev đã chuyển hết sang Risk.

## 6. Risk Score (0–100) — riêng biệt
0–30 LOW · 31–60 MEDIUM · 61–80 HIGH · 81–100 EXTREME (hàng đỏ + banner đỏ).
Mỗi cờ có giá trị thật + nguồn:
dữ liệu thị trường INVALID (+20) · **liquidity không rõ (+15)** · liquidity < 50%/100% mức lọc (+15/+7) ·
MC > 20×/10× liquidity AMM (+15/+7) · pool bonding curve chưa graduate (+5) · top10 > 50/35/25% (+25/15/7) ·
1 ví > 10% (+10) · ít holder so với MC (+8) · **phân phối holder không rõ (+10)** · dev giữ > 10/5% (+15/8) ·
dev SOLD ALL/MAJOR SELL (+10) · **dev chưa xác minh (+8)** · ≥10 token cũ không graduate (+15) · >80% token cũ chết (+8) ·
dev mua > 10% supply lúc tạo (+10) · token < 10 phút (+5) / tuổi không rõ (+5) · Vol5m > 3×MC (+10) ·
mẫu wash trading (+8) · volume sụp (+10) · giá +100%/5m (+8) · B/S < 0.6 (+8) · không social (+5).
Chưa đo được (ghi rõ): wallet clustering, bundle/sniper, social spam.

## 7. Backtest (tab HISTORY hoặc `--backtest`)
- Signal = **snapshot ĐẦU TIÊN** có Data Quality **VALID** mà Opportunity *tại thời điểm đó* ≥ ngưỡng (70/80/90).
  Score được lưu lúc scan nên không có dữ liệu tương lai. Giá từ snapshot INVALID không dùng làm kết quả.
  Snapshot cũ của V1 (chưa có DQ, công thức cũ) bị bỏ qua.
- Kết quả chỉ dùng giá ghi nhận **sau** signal, trong cửa sổ 5m/15m/30m/1h/6h/24h: tỉ lệ đạt +20%/+50%/2x/5x/10x,
  tỉ lệ giảm ≥50%, max gain trung vị.
- Cửa sổ chỉ được tính khi token còn được ghi snapshot gần cuối cửa sổ (tránh bias).
- ⚠ Cần để app chạy nhiều giờ/ngày mới có mẫu đủ lớn. Token bị prune sau 6h → cửa sổ 24h chỉ có dữ liệu cho watchlist
  (tăng *Max token age* trong Settings nếu muốn backtest 24h).

## 8. Test
```
.venv\Scripts\python -m pytest -m "not live"      # ~780 test offline (không gọi mạng)
.venv\Scripts\python -m pytest --live -m live      # 5 test gọi API thật (cần HELIUS_API_KEY trong môi trường)
```

## 9. Mẫu dữ liệu thật (docs/samples/)
`python tools/capture_samples.py <MINT>` lưu response thô DexScreener + Pump.fun và kết quả validation.
- `SICAT_VALID.json` — PumpSwap, liquidity $49.7K từ DexScreener → **VALID 85**, Opportunity 0 (hoạt động đang chết:
  vol 5m $682, B/S 0.6), Risk 10.
- `RETARDMAX_INVALID.json` — curve mayhem mode: DexScreener không có liquidity, vol 5m = 0, Pump.fun
  virtual 256.8 SOL / real 3 lamports → **INVALID 5**, không chấm Opportunity, Risk 58.

## 10. Giới hạn hiện tại (nói thẳng)
- Pump.fun API không chính thức → có thể hỏng khi Pump.fun đổi; PumpPortal + DexScreener là dự phòng.
- Không có Helius key → không có holder count, top holder, holder growth (risk sẽ cộng +10 "unverified").
- Token Pump.fun *mayhem mode* (curve không chuẩn) hiện luôn INVALID vì không xác minh được liquidity.
- Không có Helius key → không có token nào đạt holder data → Holder growth NOT AVAILABLE, DQ tối đa 85.
- Dev "sold %" chỉ tính được khi app thấy token lúc tạo (initial buy từ PumpPortal).
- Chưa làm (Phase 2/3): smart money DB, cluster ví/bundle/sniper, entry price/PnL từng holder, X, Telegram social,
  narrative trending, ML.

## 11. Roadmap
- **Phase 2:** smart-money DB từ snapshot + Helius tx history; clustering (cùng funding wallet, cùng slot mua,
  creator → ví A/B/C); X API v2; Telethon monitor; narrative momentum.
- **Phase 3:** backtest mở rộng theo từng thành phần score, tối ưu trọng số, ML, anomaly detection nâng cao.
