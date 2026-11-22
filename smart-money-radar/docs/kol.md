# Theo dõi ví KOL / người gọi kèo

Đội gọi kèo thường **mua gom trước, rồi mới đăng lên X**. Hệ thống theo dõi trực tiếp các ví này trên blockchain: ví nào
trong danh sách vừa mua token là có cảnh báo Telegram ngay, trước khi bài call xuất hiện.

## Cảnh báo nhận được

| Loại | Khi nào | Nội dung |
|---|---|---|
| 🟣 **KOL MUA** | Ví trong danh sách mua một token lần đầu (≥ `kol_alert_min_sol`, mặc định 0,3 SOL) | Tên + @X, ví, token, số SOL, giờ mua, **báo sau bao nhiêu giây**, token ra mắt được bao lâu, market cap |
| 🔥 **KOL thứ N mua cùng token** | Ví thứ 2, 3… trong danh sách mua cùng token | Như trên + danh sách các ví đã mua trước theo thứ tự, số người (ví phụ tính chung với ví chính) |
| ⚠️ **KOL ĐANG BÁN** | Ví đó đã bán ≥ 50% và ≥ 90% số token đã mua | Đã mua / đã bán bao nhiêu SOL |

Độ trễ: stream nhận log giao dịch khi được xác nhận (≈ 1–2 giây), gộp mỗi 2 giây, rồi gửi Telegram: thường **2–5 giây**
sau khi ví mua. Mỗi (ví, token) chỉ báo mua 1 lần.

## Danh sách ví

* Lần cài đầu, hệ thống tự nạp 19 ví KOL công khai trong `backend/radar/data/kol_seed.txt` (tra ngày 22/11/2026, đã kiểm tra
  mọi địa chỉ tồn tại và đang giao dịch). Nguồn ghi cạnh từng ví. **Tên chủ ví là nhãn của các trang theo dõi bên thứ ba**,
  không phải xác nhận của chính KOL (ví dụ ví gắn nhãn "Ansem" đã bị chính Ansem phủ nhận ngày 16/06/2026).
* Thêm ví: Dashboard → **Ví KOL** → dán nhiều dòng `địa_chỉ Tên @X`. Hoặc dòng lệnh: `python -m radar kol-import file.txt`.
* **Ví phụ**: ví nhận SOL trực tiếp từ một ví KOL rồi giao dịch pump.fun được gợi ý ở mục "Ví phụ nghi vấn" kèm giao dịch nạp
  làm bằng chứng. Bấm **Theo dõi** để nhận cảnh báo của ví đó (tính chung một người với ví chính). Không phát hiện được ví phụ
  nạp qua sàn hoặc qua nhiều bước trung gian.
* Ví KOL luôn được theo dõi (không bị loại khỏi danh sách dù điểm thấp), được đọc lịch sử và chấm điểm như mọi ví: trang
  Ví KOL hiện Win Rate, kiểu giao dịch (người/bot) và tỷ lệ thắng nếu copy trễ 1 phút.

## Giới hạn

* Hệ thống không đọc X/Twitter (X yêu cầu đăng nhập và API trả phí); nó theo dõi **ví**, không theo dõi bài đăng.
* KOL hay đổi ví và dùng nhiều ví. Danh sách cần cập nhật định kỳ (Kolscan, KOL Explorer, uwuu.ai, MadeOnSol đều công bố ví).
* Biết ví KOL mua sớm không có nghĩa là thắng: phần lời của đội gọi kèo thường đến từ người vào sau. Xem cột "Copy trễ 1 phút".

## Cài đặt liên quan

`kol_alert_enabled`, `kol_alert_min_sol`, `kol_exit_alert_enabled` (Dashboard → Cài đặt → Ví KOL / người gọi kèo).

## API

| Method | Đường dẫn | Mô tả |
|---|---|---|
| GET | `/api/watchlist` | Danh sách ví theo dõi + Win Rate, copy, lần mua gần nhất |
| POST | `/api/watchlist` | `{"lines": "địa_chỉ Tên @X\n..."}` thêm nhiều ví |
| POST | `/api/watchlist/{address}/confirm` | Xác nhận ví phụ được gợi ý |
| DELETE | `/api/watchlist/{address}` | Bỏ theo dõi |
| GET | `/api/watchlist/feed` | Lệnh mua/bán 3 ngày gần nhất của ví theo dõi |
