# API

Base: `https://<DOMAIN>/api` · OpenAPI tương tác: `/api/docs` · Mọi route trừ `/api/health`, `/api/auth/*` cần cookie phiên.
Số SOL là số thực (đơn vị SOL), thời gian ISO-8601 UTC. API **chỉ đọc** blockchain — không có route tạo/ký/gửi giao dịch.

## Xác thực
| Method | Path | Body | Ghi chú |
|---|---|---|---|
| POST | `/api/auth/login` | `{"password": "..."}` | đặt cookie `smr_session`; 10 lần sai/15 phút → 429 |
| POST | `/api/auth/logout` | | |
| GET | `/api/auth/me` | | `{"authenticated": bool}` |
| GET | `/api/health` | | 200 khi DB + Redis sẵn sàng |

## Tổng quan & ví
| Method | Path | Tham số | Trả về |
|---|---|---|---|
| GET | `/api/overview` | `period` | số ví theo dõi / đạt tiêu chí, token đang gom, SOL mua 24h, cảnh báo chưa xem, tín hiệu mạnh nhất, trạng thái stream |
| GET | `/api/wallets/top` | `period=7|30|90`, `sort=score|win_rate|median_buy|realized_pnl|roi|hit_5x|hit_10x|last_active`, `order`, `limit`, `official=true|false` | Top N (chính thức: chỉ ví đạt chuẩn, độ tin cậy High/Medium) |
| GET | `/api/wallets` | `status`, `q` (tiền tố địa chỉ), `period`, `page`, `page_size` | mọi ví đang theo dõi |
| POST | `/api/wallets` | `{"address": "..."}` | thêm ví, xếp hàng backfill |
| GET | `/api/wallets/{address}` | | thông tin ví, hiệu suất 7/30/90, điểm + thành phần, quan hệ, cụm, đường PnL |
| GET | `/api/wallets/{address}/positions` | `period`, `status`, `qualifying` | vị thế: vốn, giá/MC vào ra, PnL, ROI, đỉnh sau entry, 2x–10x, ROI theo mốc, xác minh, cờ |
| GET | `/api/wallets/{address}/transactions` | `limit`, `before`, `mint` | từng lệnh kèm signature, phí, nguồn, trạng thái xác minh |
| POST | `/api/wallets/{address}/exclude` | `{"excluded": true, "reason": "..."}` | loại trừ khỏi xếp hạng & tín hiệu |

## Token, tín hiệu, cảnh báo
| Method | Path | Tham số | Trả về |
|---|---|---|---|
| GET | `/api/radar` | `hours` (1–168), `limit` | Token Radar: MC, liquidity, volume, SM buyers, SM buy/sell, net flow + nhãn gom/phân phối, giá 5m/15m/1h, tuổi, curve, risk, tín hiệu |
| GET | `/api/tokens/{mint}` | | metadata, báo cáo rủi ro 11 mục, lệnh của ví theo dõi, nến 24h, tín hiệu, holder |
| GET | `/api/signals` | `limit`, `level` | lịch sử tín hiệu |
| GET | `/api/alerts` | `unseen_only`, `limit` | cảnh báo (nội dung, kênh, trạng thái gửi, đã xem) |
| POST | `/api/alerts/{id}/seen` | `seen=true|false` | |
| POST | `/api/alerts/seen-all` | | |

## Cài đặt & hệ thống
| Method | Path | Ghi chú |
|---|---|---|
| GET | `/api/settings` | cài đặt hiện tại, mặc định, trạng thái API key (chỉ `true/false`) |
| PUT | `/api/settings` | `{"changes": {"min_buy_sol": 2.5, ...}}` — kiểm tra kiểu & khoảng giá trị; `trading_enabled` không thể bật |
| POST | `/api/settings/test-telegram` | gửi tin nhắn thử |
| GET | `/api/system/status` | trạng thái nguồn dữ liệu, heartbeat worker, hàng đợi, số liệu |
| GET | `/api/backtest/latest` | kết quả backtest gần nhất |

## WebSocket
`wss://<DOMAIN>/api/ws` (cookie phiên). Sự kiện JSON `{"type": ..., "data": {...}}`:
`signal` (tín hiệu mới/tăng cấp), `alert` (cảnh báo đã lưu/gửi), `rankings_updated`, `ping` (20 giây).
