# Checkpoint việc 1 – Copy trade sâu ví 3kxcF8wH… (TẠM DỪNG theo yêu cầu, 25/11/2026)

## Đã xong
- Replay 310 vị thế đóng (302 định giá được, 8 PumpSwap pool đảo chiều bị loại), độ trễ 1/3/5/8/10 giây.
- Đọc bổ sung 404/405 cửa sổ giá bị cắt: lệnh khớp ở mọi độ trễ dùng trạng thái pool thật (chỉ 1 lệnh bán ở 8/10 s là ước tính).
- Bản nháp báo cáo: `bao-cao-copy-sau-3kxc.md`, `.docx`, `.xlsx` (thư mục này).

## File đã thay đổi
- `backend/radar/tools/deep_copy.py` (extend_window, extend, loại pool PumpSwap đảo chiều, cờ exact_state) – commit 8fe1dd0.
- `docs/research/buoc/scripts/build_deep_report.py`, `scripts/verify_3kxc.py` (bản sao; bản chạy nằm ở `winner_data/paper/deep/`).

## Còn tồn đọng (khi làm tiếp)
1. Mở file Excel kiểm tra công thức tính ra đúng số trong JSON (chưa kiểm).
2. Đọc soát bản docx (định dạng, số liệu), sau đó gửi người dùng.
3. Báo cáo chính thức việc 1, rồi mới sang việc 2 (Telegram Caller Tracker) và việc 3 (audit Master Spec).

## Dữ liệu (không nằm trong Git – thư mục winner_data bị .gitignore)
- `winner_data/paper/deep/deep_3kxc…json.gz` (checkpoint đầy đủ), `deep_3kxc…_result.json`, `verify_3kxc.json`.
- Nếu container bị thu hồi: chạy lại `python3 -m radar.tools.deep_copy <ví> ../winner_data/paper/deep 100 4000` rồi `... deep_copy extend <ví> ../winner_data/paper/deep` (RPC công khai, ~3 giờ).
