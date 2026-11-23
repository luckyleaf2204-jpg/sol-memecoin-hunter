# EXTERNAL_SERVICES_INTEGRATION_SPEC

**Báo cáo kiểm kê và đề xuất tích hợp dịch vụ bên ngoài – Smart Money Radar + Hybrid Sniper**

| | |
|---|---|
| Ngày lập | 23/11/2026 |
| Căn cứ | "Yêu cầu tích hợp dịch vụ bên ngoài – Smart Money Radar + Hybrid Sniper"; báo cáo bàn giao Giai đoạn 1 (`docs/handover/bao-cao-giai-doan-1.md`) và Giai đoạn 1.5 (`docs/handover/bao-cao-giai-doan-1-5.md`) |
| Phạm vi | Đọc source code, đọc database, đọc cấu hình, nghiên cứu tài liệu chính thức. **Không sửa code, không cài package, không đổi database, không gọi API tính phí, không mua dịch vụ.** |
| Repo / nhánh | `luckyleaf2204-jpg/sol-memecoin-hunter`, nhánh `claude/smart-money-radar-pumpfun-5kczmz`, commit `c4bd48e` |
| Trạng thái giao dịch | `trading_enabled = false`, khóa cứng trong code (xem mục 1.4) |

---

## 0. TÓM TẮT VÀ KẾT LUẬN

1. **Không cần xây lại phần lõi.** Bộ thu thập và giải mã giao dịch pump.fun / PumpSwap, tính PnL, Win Rate, ROI, Smart Money Score, phân cụm ví, rủi ro token và backtest đều đã có và đã kiểm chứng ở Giai đoạn 1.5.
   - PnL hệ thống khớp số dư SOL on-chain ở 99,71% vị thế, sai lệch không quá 0,001 SOL.
   - Score tính lại khớp 60/60 ví.
2. **Helius là tích hợp có lợi nhất và gần như không cần sửa code.** Code đã có sẵn endpoint Helius, chỉ cần nhập khóa vào `.env` trên máy triển khai. Helius giải quyết đúng ba điểm yếu đo được ở Giai đoạn 1.5:
   - backfill chậm (514 ví chờ, khoảng 1,5 ngày trên RPC công khai);
   - websocket công khai ngắt khoảng 1 phút một lần;
   - RPC công khai chặn `getTokenLargestAccounts`, nên bảng `token_holders` đang có 0 dòng.
3. **PumpPortal chỉ nên dùng làm nguồn dự phòng miễn phí** (token mới, migration).
   - Stream on-chain hiện tại đã nhận mọi giao dịch pump.fun và PumpSwap miễn phí, kể cả của ví chưa theo dõi.
   - Hai luồng `subscribeTokenTrade` / `subscribeAccountTrade` của PumpPortal tính phí (0,01 SOL / 10.000 sự kiện) mà không thêm dữ liệu mới, nên **không đề xuất**.
4. **Nansen có giá trị ở một việc cụ thể:** cung cấp danh sách ví được Nansen gắn nhãn Smart Money để hệ thống tự đánh giá lại bằng công thức của mình.
   - Đổi lại là tốn credit và ràng buộc điều khoản: một số dữ liệu Smart Money chỉ được dùng nội bộ.
   - Không giao quyền quyết định cho Nansen. Nhãn Nansen không được cộng vào Score khi chưa có phê duyệt.
   - Gói Pro có khoảng 2.000 credit/tháng, nên phải dùng tiết kiệm.
5. **GMGN, Axiom, Trojan chỉ dùng để tham khảo.** Chưa xác minh được API dữ liệu chính thức nào. Không scraping, không dùng cookie hay API nội bộ.
6. **Phần còn thiếu thật sự** là lớp quyết định của Hybrid Sniper, đều dừng ở mức mô phỏng:
   - Hybrid Scoring cho token;
   - Entry Engine, Exit Engine;
   - Paper Trading chạy liên tục có sổ lệnh;
   - hiệu quả theo chiến lược.

   Có thể tái dùng mô hình khớp lệnh giấy (latency, slippage, phí, giao dịch hỏng) của ứng dụng desktop cũ trong `src/trading/`.
7. **Thứ tự đề xuất** (chi tiết ở mục 10), mỗi bước cần anh phê duyệt riêng:

   | Bước | Nội dung | Sửa code |
   |---|---|---|
   | A | Bật Helius | Không |
   | B | Tăng tốc backfill bằng Helius | 3 file |
   | C | Nansen thử nghiệm, chỉ ghi vào trường jsonb sẵn có | Không đổi schema |
   | D | PumpPortal dự phòng | Tùy chọn |
   | E | Bảng provenance cho nguồn ngoài | Cần duyệt schema |
   | F | Hybrid Sniper, Paper Trading | Cần duyệt schema |

**Hạn chế của báo cáo này.** Mạng của môi trường kiểm kê chặn trực tiếp `helius.dev`, `docs.nansen.ai`, `docs.gmgn.ai`, `axiom.trade` và `docs.trojanonsolana.com`. Thông tin của các nhà cung cấp này lấy qua công cụ tìm kiếm có trích nguồn tài liệu chính thức. Chỉ trang của PumpPortal được đọc trực tiếp. Mọi con số về giá, credit và giới hạn gọi đều được đánh dấu **"cần xác minh lại trên trang chính thức trước khi mua/kích hoạt"**.

---

## 1. HIỆN TRẠNG SOURCE CODE

### 1.1 Cấu trúc repo

```
sol-memecoin-hunter/
├── src/, tools/, tests/        Ứng dụng desktop cũ "SOL Memecoin Hunter" (Python, ~25.000 dòng). Độc lập.
│                               Có PumpPortal client, paper trading, anti-rug. GIỮ NGUYÊN, chỉ tham khảo.
└── smart-money-radar/          Hệ thống web đang phát triển (Giai đoạn 1, 1.5)
    ├── backend/radar/          Python 3.12, FastAPI, asyncpg, Redis – 8.938 dòng, 58 file
    │   ├── solana/             base58, PDA, hằng số chương trình, RPC client nhiều endpoint
    │   ├── net/http.py         HTTP client chung: rate limit theo nguồn, retry, cache, ghi api_sync_status
    │   ├── decoder/            Giải mã giao dịch pump.fun / PumpSwap từ log và từ giao dịch đầy đủ
    │   ├── analytics/          Vị thế, PnL, hiệu suất, Score, hành vi, phân cụm, rủi ro, tín hiệu, backtest
    │   ├── services/           Worker: stream, verifier, sync, discovery, prices, analytics, signals, kol, telegram
    │   ├── sources/            DexScreener, GeckoTerminal, Jupiter
    │   ├── api/                REST + WebSocket cho dashboard (34 route), đăng nhập cookie
    │   ├── tools/              CLI kiểm chứng: backfill-audit, latency, verify-pnl, crosscheck, history, backtest
    │   ├── db/                 pool, migrate, repo
    │   └── config.py, settings.py, context.py
    ├── backend/migrations/     001_init, 002_transfers_and_queues, 003_holder_flows, 004_watchlist
    ├── backend/tests/          unit (87 test) + integration (13 test, cần DB)
    ├── frontend/               Next.js 14: 11 trang (Overview, Wallets, chi tiết ví, KOL, Radar, Token,
    │                           Live, Alerts, Backtest/Research, Health, Settings) + Login
    ├── deploy/                 Caddy (HTTPS), backup/restore
    ├── docker-compose.yml      13 service (mỗi worker 1 container)
    └── docs/                   install, operations, data-sources, windows, handover
```

### 1.2 Database (đọc trực tiếp lúc 00:35 ngày 23/11/2026, 1,17 GB, TimescaleDB 2.17 / PostgreSQL 16)

| Bảng | Số dòng | Ghi chú |
|---|---|---|
| wallets | 758 | candidate 483, observed 238, tracked 36 |
| wallet_transactions | 431.517 | rpc_logs 378.127 (stream), rpc_tx 53.390 (giao dịch đầy đủ); verified 49.812 |
| raw_transactions | 64.005 | Giao dịch gốc lưu để giải mã lại (checkpoint backfill) |
| failed_transactions | 37.191 | Giao dịch thất bại on-chain, không tính PnL |
| wallet_positions | 21.844 | Vị thế theo chu kỳ mua–bán |
| wallet_performance / smart_money_scores | 2.262 / 2.262 | 7/30/90 ngày |
| tokens | 15.811 | Đã có cột creator, created_signature, migrated_at, metadata_source |
| token_prices | 150.248 | Nến giá on-chain + GeckoTerminal |
| token_wallet_flows | 494.843 | Dòng tiền ví theo token (ước tính holder) |
| token_holders | **0** | RPC công khai chặn `getTokenLargestAccounts` → cần Helius |
| sol_transfers / wallet_relationships / wallet_age | 23.186 / 103 / 26 | Dữ liệu phân cụm |
| token_risk_reports | 239 | |
| smart_money_signals | 0 | Vì chưa có ví đạt chuẩn Smart Money |
| watched_wallets / alerts | 19 / 18 | KOL |
| backtest_runs | 2 | |
| api_sync_status, worker_heartbeats | | Trạng thái nguồn và worker |
| system_settings | 1 | Chỉ lưu giá trị ghi đè (backfill_days = 60) |

**Trường có sẵn để ghi dữ liệu nguồn ngoài mà không cần đổi schema:**
- `wallets.discovery_source` (text) và `wallets.discovery_detail` (jsonb);
- `tokens.metadata_source`;
- `wallet_transactions.source`, `verification_status`, `verification_note`, `ingested_at`, `block_time`;
- `api_sync_status`: nguồn, job, số request, lỗi, 429, độ trễ, chi tiết jsonb.

### 1.3 Môi trường chạy thử hiện tại

- Sandbox khởi động lại lúc khoảng 00:29 ngày 23/11. PostgreSQL tự khôi phục sau lần tắt không sạch ("redo done", "ready to accept connections"); dữ liệu còn nguyên.
- Các worker đang lỗi kết nối Internet. Nguyên nhân là cổng proxy của sandbox đổi từ 37357 sang 37297, trong khi file override riêng của sandbox (`/tmp/claude-0/run/compose.sandbox.yml`, ngoài repo) vẫn ghi cổng cũ.
  - Đây không phải lỗi code. Trên VPS hoặc Windows không có proxy này.
  - Theo yêu cầu "chỉ đọc", tôi **chưa sửa**. Khi được phép: đổi số cổng trong file override, rồi chạy `docker compose up -d`.

### 1.4 Khóa giao dịch

- `config.py:70`: `trading_enabled = False` ("hard-wired: the app never trades").
- `settings.py:167`: `Spec(bool, choices=(False,))`, không thể bật.
- `settings.py:223, 246`: luôn ép về False, từ chối khi bị gửi True.
- `context.py:110`: kiểm tra cứng khi khởi động.
- Không có module ký hoặc gửi giao dịch; không có private key.

---

## 2. CHỨC NĂNG: ĐÃ CÓ / ĐANG HOẠT ĐỘNG / ĐANG LỖI / CHƯA CÓ

### 2.1 Đang hoạt động, đã kiểm chứng (Giai đoạn 1 và 1.5)

| Chức năng | Module | Bằng chứng |
|---|---|---|
| Stream on-chain mọi giao dịch pump.fun + PumpSwap (logsSubscribe 2 chương trình) | `services/stream.py`, `decoder/logs.py` | 1.5: p50 4,4 giây; tự kết nối lại bằng watchdog |
| Xác minh giao dịch bằng giao dịch đầy đủ, đối chiếu số dư | `services/verifier.py`, `decoder/tx.py`, `decoder/events.py` | 98,0% verified với ví đã đọc lịch sử |
| Backfill 60 ngày có checkpoint, không trùng, có thứ tự ưu tiên | `services/wallet_sync.py`, `tools/backfill_audit.py` | 1.5: ngắt rồi chạy tiếp, 0 giao dịch tải lại, 0 dòng trùng |
| Phát hiện ví (big buyer, early buyer, patient winner, seed token) | `services/discovery.py` | 758 ví |
| PnL realized/unrealized, Win Rate, ROI, drawdown, profit factor, thời gian nắm giữ | `analytics/positions.py`, `analytics/performance.py` | 99,71% vị thế lệch ≤ 0,001 SOL so với on-chain |
| Smart Money Score + bảng kiểm bắt buộc | `analytics/scoring.py` | Tính lại 60/60, chênh 0 |
| Phân loại người/bot | `analytics/behavior.py` | |
| Phân cụm ví (nguồn cấp vốn, chuyển SOL, mua đồng thời, creator) | `analytics/relationships.py`, `services/holder_probe.py` | 103 quan hệ |
| Rủi ro token (dev holding, dev đã bán, bundle ở slot tạo, holder > 5%, ví mới, wash trade, thanh khoản) | `analytics/risk.py` | 239 báo cáo |
| Tín hiệu Smart Money (số ví độc lập, khử trùng theo cụm, tiến độ thoát) | `analytics/signals.py`, `services/signal_engine.py` | Chưa phát sinh vì 0 ví đạt chuẩn |
| KOL Tracker + cảnh báo mua/bán | `services/kol.py`, `services/telegram.py` | 19 ví KOL |
| Giá: nến on-chain, GeckoTerminal, DexScreener, SOL/USD Jupiter | `services/prices.py`, `sources/*` | SOL/USD cập nhật mỗi phút |
| Backtest walk-forward, mô phỏng copy trễ 0/30/60/180 giây | `analytics/backtest.py`, `analytics/price_hits.py` | 2 lần chạy |
| Dashboard 11 trang, đăng nhập, HTTPS, Data Health | `frontend/`, `api/`, `deploy/caddy` | Giai đoạn 1 |
| Docker Compose 13 service, backup/restore, tự chạy lại sau reboot | `docker-compose.yml`, `deploy/backup` | 1.5: 30 phút 0 lỗi; 23/11: tự khôi phục sau tắt máy |

### 2.2 Đang lỗi hoặc yếu (đo được)

| Vấn đề | Nguyên nhân | Hướng xử lý đề xuất |
|---|---|---|
| Websocket công khai ngắt khoảng 1 phút/lần (14 lần trong 30 phút); stream p99 40 giây | Giới hạn của RPC công khai | Helius websocket (Bước A) |
| Backfill chậm: 514 ví chờ, khoảng 4,4 chữ ký/giây | RPC công khai, 1 request mỗi giao dịch | Helius (A), sau đó `getTransactionsForAddress` (B) |
| 15 ví backfill lỗi (ConnectError) | RPC công khai | Tự thử lại; ít lỗi hơn với Helius |
| Đồng bộ tăng dần ví đã đủ lịch sử bị chậm (p50 6.438 giây) khi sync bận backfill | 2 luồng dùng chung | Helius tăng RPS (A); nếu cần, tách luồng (thay đổi nhỏ, không bắt buộc) |
| `token_holders` = 0 dòng | RPC công khai chặn `getTokenLargestAccounts` | Helius (A), không sửa code |
| 0 ví Smart Money | Thiếu lịch sử (xem báo cáo 1.5) | Hoàn tất backfill; Nansen làm nguồn ví ứng viên bổ sung (C) |
| Telegram chưa gửi thật | Chưa có Bot Token / Chat ID | Anh cung cấp trong `.env` |

### 2.3 Chưa có (so với Mục 3 của yêu cầu)

| Module yêu cầu | Hiện trạng | Đánh giá |
|---|---|---|
| Data Normalizer | **Có** cho dữ liệu on-chain: mọi nguồn RPC/stream đều quy về mô hình `Trade`/`Event` thống nhất (`decoder/models.py`) | Chỉ cần adapter cho Nansen/PumpPortal |
| Data Storage | **Có** | Bổ sung bảng provenance cho nguồn ngoài (Bước E, cần duyệt) |
| Wallet Intelligence | **Có, đã kiểm chứng** | Giữ nguyên |
| Wallet Clustering | **Có** | Giữ nguyên |
| Smart Money Confirmation | **Có** (`signals.py`: ví độc lập, khử cụm) | Giữ nguyên |
| Dev Analysis | **Một phần** (`risk.py`: dev holding, dev đã bán, số lần bán; `tokens.creator`; quan hệ creator) | Thiếu lịch sử tạo token của dev → bổ sung nhỏ ở Bước F |
| Bundler Analysis | **Một phần** (`risk.py`: số ví mua ở slot tạo, % cung) | Thiếu mức tập trung theo nhóm sớm → Bước F |
| Hybrid Scoring (token) | **Chưa có** | Module mới (F) |
| Risk Engine | **Có** | Giữ nguyên; Hybrid Scoring sẽ gọi lại |
| Entry Engine | **Một phần** (`price_hits.analyse_entry`, mô phỏng copy trễ) | Module mới mỏng (F) |
| Exit Engine | **Một phần** (cảnh báo thoát khi Smart Money bán – `signals.exit_progress`) | Thiếu quy tắc chốt lời/cắt lỗ cho Paper → F |
| Paper Trading | **Một phần**: backtest + copy simulation; chạy giấy liên tục chỉ có ở app desktop cũ (`src/trading/execution.py`, `bot.py`) | Module mới, tái dùng mô hình khớp lệnh cũ (F) |
| Performance Analytics | **Có** cho ví; **chưa có** theo chiến lược | Bổ sung trong F |
| Trade Execution | **Không có – đúng yêu cầu** | Khóa |

---

## 3. MODULE ĐÃ LÀM TỐT, KHÔNG CẦN XÂY LẠI

1. **Bộ giải mã pump.fun/PumpSwap** (`decoder/*`).
   - Đã xử lý: đường cong định giá bằng USDC, pool đảo chiều, lệnh qua router (bỏ chặng trung gian), phí, tip, rent.
   - PnL khớp on-chain 99,71%.
   - Helius Enhanced Transactions / Parsed Events không thay thế được vì không có lợi thêm. Chỉ nên dùng làm **công cụ đối chiếu độc lập** nếu cần.
2. **Tính PnL / Win Rate / ROI / Score** (`analytics/positions.py`, `performance.py`, `scoring.py`). Công thức đã được duyệt và kiểm chứng. **Không đổi.**
3. **Stream on-chain** (`services/stream.py`).
   - Nhận toàn bộ giao dịch của hai chương trình, miễn phí, gồm cả ví chưa theo dõi. Đây chính là nguồn phát hiện ví mới.
   - Helius websocket dùng ngay được vì code đã ưu tiên Helius khi có khóa (`config.py:85–92`, `rpc.ws_urls()`).
4. **Backfill có checkpoint** (`services/wallet_sync.py`). Chỉ cần thay cách lấy giao dịch (Bước B); phần giải mã và ghi giữ nguyên.
5. **HTTP client có rate limit theo nguồn, retry, ghi trạng thái nguồn** (`net/http.py`). Thêm Nansen chỉ là thêm 1 dòng `SourcePolicy`.
6. **Phân cụm, rủi ro token, tín hiệu, KOL, Telegram, dashboard, Docker, backup** – giữ nguyên.

---

## 4. ĐỐI CHIẾU NHU CẦU DỮ LIỆU VỚI TỪNG NHÀ CUNG CẤP

Ký hiệu: ✅ đã có trong hệ thống · ➕ nhà cung cấp bổ sung có lợi · ⛔ không nên dùng · ❓ cần xác minh.

### 4.1 Helius

| Năng lực Helius | Nhu cầu của hệ thống | Hiện trạng | Đề xuất |
|---|---|---|---|
| RPC (getSignaturesForAddress, getTransaction, getMultipleAccounts…) | Backfill, verifier, discovery | ✅ Code đã hỗ trợ, chỉ thiếu khóa | ➕ **Bước A: bật bằng `.env`** |
| `getTokenLargestAccounts` (gọi nặng) | Holder lớn, rủi ro tập trung | Bị RPC công khai chặn → 0 dòng | ➕ Bước A, không sửa code |
| `getTransactionsForAddress` (lịch sử giao dịch đầy đủ theo địa chỉ, 100 giao dịch/lần) | Backfill nhanh | Chưa dùng | ➕ **Bước B**: giảm số request khoảng 30–100 lần. ❓ Gói được dùng, định dạng trả về |
| Standard WebSocket (logsSubscribe) | Stream | ✅ Code có sẵn | ➕ Bước A. ❓ **Cách tính phí/dung lượng websocket trên gói của anh**: luồng pump.fun rất lớn, phải đo 1 giờ đầu |
| Enhanced WebSocket / LaserStream gRPC | Độ trễ thấp hơn | Chưa dùng | ⛔ Chưa cần. LaserStream mainnet cần gói Business trở lên (khoảng 499 USD/tháng). Chỉ cân nhắc khi Hybrid Sniper cần độ trễ dưới 1 giây |
| Enhanced Transactions (cũ) / Parsed Events, Parsed Streams (mới) | Giải mã | ✅ Đã tự giải mã chính xác | ⛔ Không thay thế. Tùy chọn dùng làm công cụ đối chiếu (`tools/crosscheck.py` đã có khung) |
| Webhooks | Sự kiện ví | Stream đã bao phủ | ⛔ Chưa cần. Cân nhắc sau cho ví KOL nếu muốn giảm tải stream |
| DAS (getAsset) | Metadata token | Có từ log tạo token + DexScreener | ⛔ Chưa cần |

### 4.2 PumpPortal (đọc trực tiếp https://pumpportal.fun/data-api/real-time/, /fees/, /FAQ/, /legal/)

| Luồng | Phí | Hệ thống hiện có | Đề xuất |
|---|---|---|---|
| `subscribeNewToken` | Miễn phí | ✅ Stream on-chain đã bắt sự kiện tạo token (`tokens.created_signature`, `stream_from_creation`) | ➕ Tùy chọn **dự phòng** khi stream on-chain suy giảm (Bước D) |
| `subscribeMigration` | Miễn phí | ✅ Stream bắt migration (`tokens.migrated_at`, `migration_signature`) | ➕ Như trên |
| `subscribeTokenTrade` | 0,01 SOL / 10.000 sự kiện; cần API key và ví liên kết có ít nhất 0,02 SOL | ✅ Stream đã có mọi giao dịch, miễn phí | ⛔ Không dùng: trùng dữ liệu và tốn phí |
| `subscribeAccountTrade` | Như trên | ✅ Stream + KOL tracker đã có | ⛔ Không dùng |

Thông số chính thức của PumpPortal:
- Chỉ dùng **1 kết nối websocket**. Không gửi quá 200 tin subscribe/giây, không quá 5.000 địa chỉ/tin. Vi phạm bị khóa tạm 1 giờ.
- Dữ liệu ở mức commitment **"processed"**, thường chậm dưới 100 ms so với gRPC nếu máy chủ đặt ở New York.
- Từ 01/5/2026, dữ liệu giao dịch chỉ dành cho người dùng có API key.
- Điều khoản: PumpPortal không liên kết với pump.fun; tranh chấp giải quyết bằng trọng tài.

Lưu ý: commitment "processed" có thể có sự kiện bị bỏ (fork). Hệ thống phải lưu `received_at` và chỉ coi là xác nhận khi đã thấy on-chain ở mức "confirmed".

Ứng dụng desktop cũ đã có client PumpPortal (`src/pumpfun/stream.py`, `client.py`), có thể **tham khảo**. Client này không có API key, nên không đọc được dữ liệu giao dịch theo quy định từ 01/5/2026.

### 4.3 Nansen (qua tìm kiếm, nguồn docs.nansen.ai; ❓ toàn bộ cần xác minh trên trang chính thức)

| Nhóm API | Nhu cầu | Đề xuất | Ghi chú |
|---|---|---|---|
| Smart Money DEX Trades (Solana) | Nguồn ví ứng viên chất lượng | ➕ **Bước C – ưu tiên 1**: đưa ví vào `wallets` với `discovery_source='nansen_sm'`, rồi backfill và chấm bằng **công thức của hệ thống** | Theo nguồn thứ cấp: chỉ dùng nội bộ, không phân phối lại |
| Smart Money Netflow | Dòng tiền theo token | ➕ Tùy chọn (C), hiển thị tham khảo, **không đưa vào Score** | Netflow bị hạn chế phân phối lại, cần Nansen chấp thuận |
| Smart Money Holdings / Historical Holdings | Danh mục của ví nhãn | ⛔ Chưa cần | Hệ thống đã tự có vị thế từ on-chain |
| Token God Mode: Holders, Flow Intelligence | Holder, dòng tiền token | ⛔ Chưa cần khi đã có Helius + `token_wallet_flows` | Tốn credit |
| Profiler / PnL Leaderboard | Đối chiếu PnL | ➕ Tùy chọn (C): chỉ cho Top N ứng viên, lưu để **so sánh**, không thay thế PnL hệ thống | Đối chiếu chéo độc lập |
| Token Information / Indicators | Thông tin token | ⛔ Chưa cần | |

Gói và giới hạn (❓ cần xác minh):
- Free: 100 credit một lần, không có Smart Money API.
- Pro: khoảng 49 USD/tháng nếu trả năm, khoảng 69 USD/tháng nếu trả tháng; nạp lại tối đa 2.000 credit/tháng; mua thêm khoảng 10 USD / 10.000 credit (có trang ghi khác).
- Giới hạn gọi: Pro 75 request/giây, 1.500 request/phút.
- Smart Money endpoint: khoảng 5 credit/lần; một số endpoint 150 credit khi lấy nhãn cao cấp.
- Credit hết hạn sau 1 năm.

Hệ quả: với 2.000 credit/tháng chỉ khoảng 400 lần gọi 5 credit, tức khoảng 13 lần/ngày. Vì vậy phải có **hạn mức credit theo ngày** và gọi theo lô, không gọi liên tục.

---

## 5. TỔNG HỢP GIÁ / QUYỀN TRUY CẬP / GIỚI HẠN / ĐỘ TRỄ / ĐIỀU KHOẢN

| Nhà cung cấp | Gói tối thiểu đề xuất | Chi phí ước tính | Giới hạn | Độ trễ | Điều khoản cần lưu ý | Mức tin cậy thông tin |
|---|---|---|---|---|---|---|
| Helius | Gói trả phí anh đang có. Tham chiếu: Developer 49 USD/tháng, 10 triệu credit, 50 RPS (Business 499 USD: 100 triệu credit, 200 RPS) | Backfill 514 ví × 3.000 giao dịch: cách hiện tại khoảng 1,5 triệu credit (1 credit/request); với `getTransactionsForAddress` khoảng 150.000 credit (10 credit / 100 giao dịch) | Theo gói; credit thêm khoảng 5 USD / 1 triệu | Đo bằng `radar latency` sau khi bật | Không lưu khóa trong code/log (đã tuân thủ: `secret_values()` che trong log) | Qua tìm kiếm, nguồn helius.dev/docs ❓ |
| PumpPortal | Không key (token mới, migration); có key nếu cần giao dịch | 0 cho phần đề xuất | 1 kết nối, ≤ 200 tin/giây, ≤ 5.000 địa chỉ/tin | Commitment processed, dưới 100 ms sau gRPC | Không liên kết pump.fun; trọng tài | **Đọc trực tiếp** ✅ |
| Nansen | Pro | 49–69 USD/tháng | 2.000 credit/tháng; 75 request/giây | Không nêu | Dữ liệu Smart Money chỉ dùng nội bộ; netflow cần chấp thuận khi phân phối | Qua tìm kiếm ❓ |
| GMGN | – | – | – | – | Chưa thấy API dữ liệu công khai chính thức; công cụ bên thứ ba là scraping → **không dùng** | Qua tìm kiếm ❓ |
| Axiom | – | – | – | – | Chưa xác minh được API công khai | Qua tìm kiếm ❓ |
| Trojan | – | – | – | – | Bot Telegram giao dịch; chưa thấy API | Qua tìm kiếm ❓ |

---

## 6. GMGN / AXIOM / TROJAN – CHỈ THAM KHẢO

| Nguồn | Tham khảo gì | Áp dụng vào đâu |
|---|---|---|
| GMGN | Cách trình bày Smart Money, hồ sơ ví (PnL, win rate, phân bố lãi lỗ), nhãn ví (dev, sniper, KOL) | Trang chi tiết ví đã có; chỉ cân nhắc chỉnh hiển thị sau, không ưu tiên |
| Axiom | Bộ lọc token mới / sắp migration / đã migration (kiểu "Pulse"), quản lý vị thế | Bộ lọc đầu vào cho Hybrid Scoring (F) |
| Trojan | Sniper, migration sniper, copy trade, TP/SL nhiều bậc, auto-sell, bảo vệ MEV | Thiết kế Exit Engine cho Paper Trading (F); **không** xây bot Telegram giao dịch |

Không scraping, không cookie hay session, không API nội bộ. Chỉ tích hợp khi có API chính thức và anh phê duyệt.

---

## 7. KIẾN TRÚC TÍCH HỢP TỐI GIẢN

```
                 ┌──────────────── NGUỒN DỮ LIỆU ────────────────┐
                 │ Helius RPC/WS │ RPC công khai │ PumpPortal (tùy chọn) │ Nansen (tùy chọn) │ DexScreener/Gecko/Jupiter │
                 └──────┬────────┴──────┬────────┴─────────┬────────────┴────────┬──────────┴────────────┬──────┘
   INGESTION (có sẵn)   │ solana/rpc.py (đa endpoint,      │ sources/pumpportal  │ sources/nansen.py     │ sources/*.py
                        │ full_history, rate limit)        │ (MỚI, 1 kết nối)    │ (MỚI, hạn mức credit) │ net/http.py
                        │ services/stream.py (watchdog)    │                      │                       │
   NORMALIZATION        ▼ decoder/* → Trade/Event (có sẵn) ▼ adapter → tokens     ▼ adapter → wallets      │
                        │                                  │ (metadata_source)    │ (discovery_source,     │
                        │                                  │                      │  discovery_detail)     │
   STORAGE              ▼──────────────── PostgreSQL/TimescaleDB hiện tại (+ bảng provenance ở Bước E) ─────▼
   INTELLIGENCE           positions · performance · scoring · behavior · relationships · risk   (GIỮ NGUYÊN)
   DECISION               signals (có sẵn)  →  hybrid_score · entry · exit   (MỚI, Bước F)
   VALIDATION             backtest (có sẵn)  →  paper_trader (MỚI, Bước F, chỉ mô phỏng)
   EXECUTION              ✖ KHÔNG CÓ – trading_enabled = false
```

Nguyên tắc adapter: mỗi nhà cung cấp là **một file nguồn + một role worker riêng**, bật/tắt bằng `.env` / settings và `profiles` của Docker Compose. Tắt một nguồn thì phần còn lại vẫn chạy (degraded mode). Trạng thái từng nguồn hiện tự động trên Data Health qua `api_sync_status`.

---

## 8. DANH SÁCH FILE: GIỮ NGUYÊN / BỔ SUNG / CHỈNH SỬA

### 8.1 Giữ nguyên, không chạm (đã kiểm thử)

| Nhóm | File |
|---|---|
| Giải mã | `backend/radar/decoder/events.py`, `logs.py`, `models.py`, `tx.py` |
| Phân tích lõi | `analytics/positions.py`, `performance.py`, **`scoring.py` (công thức Score)**, `behavior.py`, `relationships.py`, `risk.py`, `signals.py`, `price_hits.py`, `holders.py`, `backtest.py` |
| Worker | `services/stream.py`, `verifier.py`, `discovery.py`, `prices.py`, `analytics_runner.py`, `signal_engine.py`, `kol.py`, `telegram.py`, `holder_probe.py`, `txproc.py`, `health.py` |
| Hạ tầng | `db/*`, `context.py`, `logging_setup.py`, `solana/base58.py`, `pda.py`, `constants.py`, `sources/dexscreener.py`, `geckoterminal.py`, `jupiter.py`, `api/auth.py` |
| Migration | `backend/migrations/001`–`004` (không sửa migration cũ) |
| Công cụ | `tools/*` |
| Frontend | Toàn bộ `frontend/` (chưa phát triển giao diện mới trong các bước A–E) |
| Triển khai | `deploy/*`, Dockerfile backend/frontend |
| Ứng dụng desktop cũ | Toàn bộ `src/`, `tools/`, `tests/` ở thư mục gốc – chỉ tham khảo |

### 8.2 Bước A – Bật Helius: **không sửa file code nào**

| File | Việc | Lý do |
|---|---|---|
| `.env` trên máy triển khai (không có trong repo) | Anh tự điền `HELIUS_API_KEY`, `HELIUS_RPS` | Code đã hỗ trợ (`config.py:85–92`) |
| `docs/install.md` (tùy chọn) | Ghi chú đặt `HELIUS_RPS` theo gói trả phí (hiện ghi "gói Free") | Tránh 429 hoặc dùng thiếu hạn mức |

### 8.3 Bước B – Backfill nhanh bằng Helius `getTransactionsForAddress` (không đổi schema)

| File | Loại | Thay đổi | Lý do |
|---|---|---|---|
| `backend/radar/solana/rpc.py` | Chỉnh sửa (thêm hàm, không sửa hàm cũ) | Thêm `get_transactions_for_address(address, before, limit)`, chỉ gọi endpoint `helius` | 1 request lấy tới 100 giao dịch đầy đủ thay cho 1 + 100 request |
| `backend/radar/services/wallet_sync.py` | Chỉnh sửa | Khi có Helius và bật cờ: lấy giao dịch theo trang qua hàm mới, ghi `raw_transactions` như cũ; lỗi thì **quay về cách cũ** | Giữ checkpoint, giải mã, ghi dữ liệu y nguyên |
| `backend/radar/settings.py` | Chỉnh sửa | Thêm cờ `helius_tx_history` (mặc định **false**) | Bật/tắt không cần deploy lại; rollback tức thì |
| `backend/tests/unit/test_decoder.py` hoặc file test mới | Bổ sung | Test: dữ liệu trả về giải mã giống hệt `getTransaction` trên fixture thật | Bảo đảm PnL không đổi |

Nghiệm thu: chạy `verify-pnl` trước và sau; sai lệch PnL không đổi.

### 8.4 Bước C – Nansen thử nghiệm (không đổi schema)

| File | Loại | Thay đổi | Lý do |
|---|---|---|---|
| `backend/radar/sources/nansen.py` | **Bổ sung** | Client gọi endpoint được duyệt; header API key từ env; đếm credit; dừng khi chạm hạn mức ngày | Adapter riêng, có thể tắt |
| `backend/radar/services/nansen_sync.py` | **Bổ sung** | Job theo lô (ví dụ 2–4 lần/ngày): Smart Money DEX Trades (Solana) → thêm ví `candidate` với `discovery_source='nansen_sm'`, nhãn và thời điểm vào `discovery_detail.nansen`; tùy chọn Profiler PnL cho Top N ứng viên → `discovery_detail.nansen_pnl` | Nansen chỉ **gợi ý ví**; đánh giá vẫn do công thức hệ thống |
| `backend/radar/config.py` | Chỉnh sửa | Thêm `nansen_api_key` và đưa vào `secret_values()` để che trong log | An toàn khóa |
| `backend/radar/net/http.py` | Chỉnh sửa (1 dòng) | `"nansen": SourcePolicy(rps=…)` | Rate limit + retry + ghi `api_sync_status` có sẵn |
| `backend/radar/settings.py` | Chỉnh sửa | `nansen_enabled` (mặc định **false**), `nansen_daily_credit_cap`, `nansen_jobs` | Kiểm soát chi phí |
| `backend/radar/__main__.py` | Chỉnh sửa | Đăng ký role `nansen` (không đổi role cũ) | Chạy như worker riêng |
| `backend/radar/api/app.py` | Chỉnh sửa (1 dòng) | Thêm `nansen_api_key` vào danh sách "đã cấu hình / Not configured" | Data Health hiển thị, không lộ giá trị |
| `docker-compose.yml` | Chỉnh sửa | Service `nansen` với `profiles: ["nansen"]` (mặc định không chạy) | Bật/tắt độc lập |
| `.env.example` | Chỉnh sửa | `NANSEN_API_KEY=` (để trống) | Hướng dẫn |
| `backend/tests/unit/test_nansen.py` | **Bổ sung** | Test adapter bằng mẫu phản hồi ghi lại từ API thật (không dữ liệu giả hiển thị) | |

Quy tắc bắt buộc:
- Nhãn Nansen **không** tự cộng điểm Score và **không** tự tạo tín hiệu.
- Muốn đưa nhãn vào Score phải đề xuất công thức riêng và anh phê duyệt.

### 8.5 Bước D – PumpPortal dự phòng (tùy chọn)

| File | Loại | Thay đổi | Lý do |
|---|---|---|---|
| `backend/radar/sources/pumpportal.py` | **Bổ sung** | 1 kết nối websocket, `subscribeNewToken` + `subscribeMigration`, reconnect có backoff, chống trùng theo signature, lưu `received_at` | Dự phòng khi stream on-chain suy giảm; đo chéo độ trễ |
| `backend/radar/services/pumpportal_feed.py` | **Bổ sung** | Ghi token mới vào `tokens` chỉ khi chưa có (`ON CONFLICT DO NOTHING`, `metadata_source='pumpportal'`); ghi độ trễ vào `api_sync_status` | Không ghi đè dữ liệu on-chain |
| `backend/radar/__main__.py`, `docker-compose.yml` | Chỉnh sửa | Role/service `pumpportal`, `profiles: ["pumpportal"]` | Mặc định tắt |
| `backend/radar/settings.py` | Chỉnh sửa | `pumpportal_enabled` (mặc định false) | |

Lưu ý: có thể tham khảo `src/pumpfun/stream.py` của app cũ, nhưng không import chéo dự án.

### 8.6 Bước E – Provenance nguồn ngoài (**cần phê duyệt schema**)

| File | Loại | Thay đổi | Lý do |
|---|---|---|---|
| `backend/migrations/005_external_sources.sql` | **Bổ sung** (chỉ CREATE TABLE, không ALTER/DROP bảng cũ) | `external_events` (provider, endpoint, event_type, event_time, received_at, request_id, signature, wallet, mint, payload jsonb, verification_status, UNIQUE(provider, dedupe_key)); `external_labels` (provider, address, label, first_seen, last_seen, payload) | Đáp ứng Mục 6: lưu provenance, chống trùng, không ghi đè dữ liệu mâu thuẫn |
| `sources/nansen.py`, `sources/pumpportal.py` | Chỉnh sửa | Ghi thêm vào bảng mới | |

Chỉ làm khi Bước C/D chứng minh có giá trị. Sao lưu DB trước khi migrate.

### 8.7 Bước F – Hybrid Sniper (chỉ Paper Trading; **cần phê duyệt thiết kế và schema**)

| File | Loại | Nội dung |
|---|---|---|
| `backend/radar/analytics/hybrid_score.py` | Bổ sung | Điểm token = token mới + Smart Money (`signals.py`) + dòng tiền (`token_wallet_flows`) + thanh khoản + rủi ro (`risk.py`); thiếu dữ liệu thì xử lý thận trọng |
| `backend/radar/analytics/entry_rules.py`, `exit_rules.py` | Bổ sung | Thời điểm vào; chốt lời nhiều bậc, cắt lỗ, thoát khi Smart Money bán (tái dùng `signals.exit_progress`) |
| `backend/radar/services/paper_trader.py` | Bổ sung | Sổ lệnh giấy liên tục; khớp lệnh mô phỏng có độ trễ, phí, slippage, giao dịch hỏng (chuyển logic từ `src/trading/execution.py` của app cũ) |
| `backend/migrations/006_paper_trading.sql` | Bổ sung | `paper_trades`, `paper_runs` (chỉ tạo bảng mới) |
| `backend/radar/api/app.py`, `frontend/app/backtest/page.tsx` | Chỉnh sửa (giai đoạn sau) | Hiển thị kết quả Paper theo chiến lược trong Research Lab |
| Execution | **Không làm** | `trading_enabled` giữ false |

---

## 9. RỦI RO, DỰ PHÒNG, ROLLBACK

| Rủi ro | Mức | Dự phòng | Rollback |
|---|---|---|---|
| Websocket Helius tính phí theo dung lượng; luồng pump.fun lớn làm vượt hạn mức | Trung bình ❓ | Theo dõi credit 1 giờ đầu; nếu tốn: đưa Helius xuống sau RPC công khai cho stream, giữ Helius cho RPC | Xóa hoặc đổi biến `.env`, `docker compose up -d` |
| Vượt credit Helius do backfill | Thấp | `HELIUS_RPS` theo gói; Bước B giảm khoảng 10 lần credit | Tắt cờ `helius_tx_history` |
| Nansen tốn credit, điều khoản hạn chế | Trung bình | Hạn mức credit/ngày; chỉ dùng nội bộ; không hiển thị dữ liệu Nansen cho bên thứ ba | `nansen_enabled=false`, dừng service |
| Nhãn Nansen sai hoặc không phù hợp memecoin mới | Trung bình | Chỉ dùng làm nguồn ứng viên; mọi ví vẫn qua backfill + công thức hệ thống | Bỏ ví `discovery_source='nansen_sm'` khỏi hàng đợi bằng settings |
| PumpPortal ở mức "processed" (có thể có sự kiện không được xác nhận) | Thấp | Chỉ thêm token khi chưa có; xác nhận bằng on-chain "confirmed" | Tắt profile |
| Mất kết nối nhà cung cấp | Thấp | Đa endpoint, watchdog, degraded mode có sẵn | – |
| Thay đổi làm sai PnL | Thấp | Chạy `verify-pnl` và toàn bộ test trước/sau mỗi bước | `git revert` commit của bước đó |
| Migration mới | Thấp | Chỉ CREATE TABLE; sao lưu trước (`deploy/backup`) | `DROP TABLE` bảng mới hoặc restore bản sao lưu (đã thử: restore 44 giây, mã thoát 0) |

Quy trình mỗi bước:
1. Sao lưu DB.
2. Tạo nhánh hoặc commit riêng.
3. Chạy test và `verify-pnl` trước.
4. Thay đổi nhỏ.
5. Chạy test, `verify-pnl` và `latency` sau.
6. Báo cáo kèm bằng chứng.
7. Nếu không đạt thì `git revert` và tắt cờ.

---

## 10. KẾ HOẠCH TRIỂN KHAI VÀ TIÊU CHÍ NGHIỆM THU

| Bước | Nội dung | Điều kiện bắt đầu | Tiêu chí nghiệm thu (có bằng chứng) |
|---|---|---|---|
| **A** | Bật Helius qua `.env` (0 code) | Anh nhập khóa trên máy triển khai | `stream_endpoint = helius`; số lần kết nối lại < 2/giờ; stream p99 < 10 giây; `token_holders` > 0; tốc độ backfill ≥ 10 lần hiện tại; credit tiêu thụ 24 giờ được ghi lại |
| **B** | `getTransactionsForAddress` cho backfill | A đạt, xác minh gói cho phép | Backfill hết hàng đợi; `verify-pnl` ≥ 99,7% trong 0,001 SOL; 0 dòng trùng; test xanh |
| **C** | Nansen thử nghiệm 2 tuần | Anh xác nhận gói Nansen và đồng ý chi phí | Số ví Nansen đưa vào; tỷ lệ đạt chuẩn theo công thức hệ thống so với ví tự phát hiện; credit dùng ≤ hạn mức; so sánh PnL Nansen với hệ thống trên Top N |
| **D** | PumpPortal dự phòng (tùy chọn) | Chỉ khi A vẫn còn mất sự kiện | Độ trễ token mới so với stream on-chain; 0 bản ghi ghi đè |
| **E** | Bảng provenance | Anh duyệt schema | Migration chạy trên bản sao lưu trước; mọi sự kiện ngoài có provider/received_at/dedupe |
| **F** | Hybrid Sniper – Paper | Anh duyệt thiết kế; đã có ví Smart Money đạt chuẩn | Paper chạy ≥ 2–4 tuần; báo cáo PnL sau phí và slippage, win rate, drawdown, expectancy, profit factor theo chiến lược; **không** bật giao dịch thật |

---

## 11. ĐIỂM CẦN ANH QUYẾT ĐỊNH / CUNG CẤP

1. **Helius:**
   - Tên gói đang dùng (Developer / Business / Professional) và số RPS, để đặt `HELIUS_RPS`.
   - Gói có `getTransactionsForAddress` không (Bước B).
   - Websocket có tính theo dung lượng không.
   - Khóa chỉ nhập trên máy triển khai.
2. **Nansen:** anh đã có gói API chưa (Free / Pro); đồng ý mức credit/ngày bao nhiêu; xác nhận chỉ dùng nội bộ.
3. **PumpPortal:** có cần lớp dự phòng không (đề xuất: chưa cần, xem lại sau Bước A).
4. **Phê duyệt từng bước A → F.** Bước E, F có thay đổi schema nên cần duyệt riêng.
5. **Môi trường thử:** cho phép sửa số cổng proxy trong file override của sandbox (ngoài repo) để các worker chạy lại.

---

## PHỤ LỤC – NGUỒN THAM KHẢO

- PumpPortal (đọc trực tiếp 23/11/2026):
  - https://pumpportal.fun/data-api/real-time/
  - https://pumpportal.fun/fees/
  - https://pumpportal.fun/FAQ/
  - https://pumpportal.fun/legal/
- Helius (qua tìm kiếm; trang bị chặn từ môi trường kiểm kê):
  - https://www.helius.dev/docs/billing/plans
  - https://www.helius.dev/docs/billing/credits
  - https://www.helius.dev/pricing
  - https://www.helius.dev/docs/enhanced-transactions/overview
  - https://www.helius.dev/docs/parsed-events
  - https://www.helius.dev/docs/parsed-streams
- Nansen (qua tìm kiếm):
  - https://docs.nansen.ai/api/overview
  - https://docs.nansen.ai/getting-started/credits
  - https://docs.nansen.ai/getting-started/rate-limits
  - https://docs.nansen.ai/api/smart-money/holdings
  - https://academy.nansen.ai/articles/1287744-plans-and-pricing
  - https://academy.nansen.ai/articles/0938495-get-started-with-api
- GMGN / Axiom / Trojan (qua tìm kiếm, không có tài liệu API chính thức được xác minh):
  - https://docs.gmgn.ai/
  - https://docs.mobula.io/guides/gmgn-alternatives
  - https://medium.com/@geggonen/axiom-trade-complete-guide-938e97c0a3a6
  - https://medium.com/@CubeBaseBlog/trojan-solana-trading-bot-review-and-best-alternatives-2d5f9a085eb7
- Nội bộ: `docs/handover/bao-cao-giai-doan-1.md`, `docs/handover/bao-cao-giai-doan-1-5.md`, `docs/data-sources.md`
