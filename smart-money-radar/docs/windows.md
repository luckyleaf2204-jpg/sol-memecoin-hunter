# Chạy Smart Money Radar trên máy tính Windows 10/11 (không cần VPS)

Hướng dẫn dành cho người không chuyên lập trình. Toàn bộ hệ thống (thu thập dữ liệu Solana, phân tích ví, Top 10,
Token Radar, cảnh báo Telegram, dashboard) chạy trên máy tính của bạn bằng **Docker Desktop**.
Mã nguồn và chức năng **giống hệt** bản VPS; chỉ khác cách triển khai (file `docker-compose.windows.yml`).

> Khi máy tính tắt hoặc ngủ, hệ thống tạm dừng thu thập. Khi máy bật lại, nó tự chạy tiếp và tự đồng bộ lại
> lịch sử các ví đang theo dõi. Cảnh báo trong khoảng máy tắt sẽ bị lỡ.

---

## 1. Yêu cầu máy tính

| Hạng mục | Tối thiểu | Khuyến nghị |
|---|---|---|
| Hệ điều hành | Windows 10 64-bit (bản 22H2) hoặc Windows 11 | Windows 11 |
| RAM | 8 GB | 16 GB |
| Ổ cứng trống | 20 GB | 50 GB (SSD) |
| CPU | 4 nhân, **bật ảo hóa** (Virtualization) | |
| Mạng | Internet ổn định | |
| Tài khoản | Helius API key (gói trả phí bạn đang dùng) | Telegram bot (tùy chọn) |

Docker Desktop miễn phí cho cá nhân và doanh nghiệp nhỏ (dưới 250 nhân viên và dưới 10 triệu USD doanh thu/năm).

## 2. Cài Docker Desktop (một lần)

1. Tải tại https://www.docker.com/products/docker-desktop/ → **Download for Windows**.
2. Chạy file cài đặt, giữ tùy chọn **Use WSL 2 instead of Hyper-V**. Cài xong, **khởi động lại máy**.
3. Mở **Docker Desktop**, chấp nhận điều khoản, có thể bỏ qua bước đăng nhập (Skip).
4. Đợi góc dưới bên trái hiện **Engine running** (màu xanh).
5. Vào **Settings (bánh răng) → General**, tích chọn **Start Docker Desktop when you sign in to your computer**, rồi bấm **Apply & restart**.

Nếu Docker báo lỗi:
* *"WSL 2 installation is incomplete"*: mở **PowerShell (Admin)**, chạy `wsl --update`, rồi khởi động lại máy.
* *"Virtualization must be enabled"*: vào BIOS bật **Intel VT-x / AMD-V (SVM)**. Có thể kiểm tra trước trong
  Task Manager → Performance → CPU → dòng *Virtualization: Enabled*.

## 3. Tải mã nguồn

**Cách A — không cần Git:** trên GitHub, chọn nhánh chứa dự án → **Code → Download ZIP**.
Giải nén vào một thư mục **ngắn, không dấu, không khoảng trắng, không nằm trong OneDrive**, ví dụ:

```
C:\SmartMoneyRadar\
```

**Cách B — có Git:** `git clone <địa chỉ repo> C:\SmartMoneyRadar`

Các file dùng hằng ngày nằm trong `C:\SmartMoneyRadar\smart-money-radar\windows\`.

## 4. Cấu hình lần đầu — bấm đúp `windows\setup.bat`

Script tự tạo file `.env` (cấu hình), tự sinh mật khẩu database và khóa bảo mật, rồi hỏi bạn từng mục:

| Câu hỏi | Nhập gì |
|---|---|
| Mật khẩu đăng nhập dashboard | Tự đặt, ít nhất 10 ký tự |
| Helius API key | Key của gói trả phí bạn đang dùng (lấy ở https://dashboard.helius.dev → API Keys) |
| Số request/giây | Giới hạn của gói Helius, xem trên dashboard Helius. Không chắc thì bấm Enter (mặc định 8) |
| Telegram bot token / chat id | Xem [telegram-setup.md](telegram-setup.md). Có thể bấm Enter để bỏ qua và bổ sung sau |

Muốn sửa sau này: chạy lại `setup.bat`, hoặc mở `smart-money-radar\.env` bằng Notepad.
**Không bao giờ** đổi `POSTGRES_PASSWORD` sau lần chạy đầu tiên (database đã được tạo với mật khẩu đó).
Không bao giờ đưa private key hay seed phrase vào file này: hệ thống chỉ đọc dữ liệu, không giao dịch.

## 5. Khởi động — bấm đúp `windows\start.bat`

* Lần đầu, script tải và build các thành phần (khoảng 1–2 GB), mất **5–15 phút**. Những lần sau chỉ mất vài giây.
* Khi xong, trình duyệt tự mở **http://localhost:8080**. Đăng nhập bằng mật khẩu dashboard.
* Sau 10–30 phút, hệ thống bắt đầu có ví ứng viên. Top 10 chính thức cần thời gian tích lũy dữ liệu (xem README).

## 6. Tự chạy khi bật máy

Chỉ cần hai điều kiện:
1. Docker Desktop đã bật mục **Start Docker Desktop when you sign in** (mục 2, bước 5).
2. Bạn **không** chạy `stop.bat` trước khi tắt máy.

Khi bạn đăng nhập Windows, Docker Desktop tự mở, rồi tất cả container tự chạy lại nhờ cấu hình `restart: unless-stopped`.
Nếu đã chạy `stop.bat`, các container sẽ **giữ trạng thái dừng** cho tới khi bạn chạy lại `start.bat`.

Nên chỉnh thêm:
* **Settings → System → Power** (Windows 11) hoặc **Power Options** (Windows 10): khi cắm sạc, đặt *Sleep = Never*.
  Máy ngủ thì hệ thống ngừng nhận giao dịch.
* Windows Update đôi khi tự khởi động lại máy. Hệ thống sẽ tự chạy lại sau khi bạn đăng nhập.

## 7. Dùng hằng ngày

| File (trong `windows\`) | Tác dụng |
|---|---|
| `start.bat` | Khởi động / bật lại toàn bộ hệ thống, mở dashboard |
| `status.bat` | Xem trạng thái các thành phần (cột STATUS phải là `Up` / `healthy`) |
| `logs.bat` | Xem 200 dòng nhật ký gần nhất (API key đã được che) để gửi khi cần hỗ trợ |
| `stop.bat` | Dừng hệ thống; dữ liệu vẫn được giữ |
| `backup-now.bat` | Sao lưu database ngay |
| `restore.bat` | Khôi phục database từ một bản sao lưu |
| `update.bat` | Cập nhật phiên bản mới (dữ liệu được giữ) |

Trong Docker Desktop → **Containers** → `smart-money-radar`, bạn sẽ thấy 8 thành phần:
`postgres`, `redis`, `api`, `worker`, `frontend`, `caddy`, `backup` đang chạy, và `migrate` ở trạng thái *Exited* (đúng như vậy:
nó chỉ chạy một lần để tạo bảng database).

## 8. Sao lưu và khôi phục dữ liệu

**Tự động:**
* Mỗi ngày lúc **12:00 trưa** (giờ Việt Nam). Đổi giờ bằng biến `BACKUP_AT_UTC` trong `.env`, tính theo giờ UTC = giờ VN − 7.
* Mỗi lần bật máy, nếu bản gần nhất đã quá 24 giờ, hệ thống tự sao lưu bù sau khoảng 2 phút.
* Giữ các bản trong 14 ngày (`BACKUP_KEEP_DAYS`).

**Thủ công:** bấm đúp `backup-now.bat`.

Các bản sao lưu nằm trong `smart-money-radar\backups\radar-YYYYMMDD-HHMMSS.dump`.
Nên **chép thư mục `backups` sang USB hoặc Google Drive mỗi tuần**: nếu ổ cứng hỏng, các bản trên máy cũng mất theo.

**Khôi phục:** bấm đúp `restore.bat` → nhập tên file (ví dụ `radar-20261122-050000.dump`) → gõ `YES`.
Script tự dừng ứng dụng, thay toàn bộ database bằng bản sao lưu (có xử lý riêng cho bảng TimescaleDB), rồi chạy lại.

**Chuyển sang máy mới:**
1. Cài Docker Desktop và tải mã nguồn (mục 2–3).
2. Chép file `.env` và thư mục `backups` từ máy cũ sang đúng vị trí.
3. Chạy `start.bat`, rồi chạy `restore.bat` với bản sao lưu mới nhất.

## 9. Xem dashboard trên iPhone trong cùng mạng Wi-Fi (tùy chọn)

Mặc định dashboard **chỉ mở được trên chính máy tính** (`127.0.0.1`), an toàn nhất. Nếu muốn xem trên điện thoại khi ở nhà:
1. Trong `.env`, đổi `LOCAL_BIND=127.0.0.1` thành `LOCAL_BIND=0.0.0.0`, rồi chạy lại `start.bat`.
2. Khi Windows Firewall hỏi, chọn **Allow** cho mạng *Private*.
3. Xem IP của máy: mở `cmd`, gõ `ipconfig`, lấy dòng *IPv4 Address* (ví dụ `192.168.1.20`).
4. Trên iPhone mở `http://192.168.1.20:8080`.

Kết nối kiểu này **không mã hóa** (HTTP). Chỉ dùng trong mạng nhà tin cậy, và **không mở cổng** trên router ra Internet.

## 10. Những điểm có thể không tương thích với Windows và cách đã xử lý

| Vấn đề | Ảnh hưởng | Cách xử lý trong dự án / việc bạn cần làm |
|---|---|---|
| Ký tự xuống dòng Windows (CRLF) trong script `.sh` | Script backup trong container sẽ lỗi | `.gitattributes` ép LF cho mọi file văn bản (riêng `.bat` là CRLF); file ZIP từ GitHub cũng giữ LF. File `.env` lưu bằng Notepad (CRLF) vẫn đọc đúng: đã kiểm thử |
| Cổng 80/443 thường bị IIS/Skype chiếm; HTTPS Let's Encrypt cần tên miền | Bản VPS không chạy được trên PC | Bản Windows dùng HTTP trên cổng 8080, chỉ mở cho `127.0.0.1`. Nếu cổng 8080 bận, đổi `LOCAL_PORT` trong `.env` |
| Chứng chỉ tự ký cho `localhost` | Trình duyệt báo "không an toàn" | Không dùng HTTPS cho localhost; cookie đăng nhập vẫn hoạt động (đã kiểm thử) |
| PowerShell làm hỏng file nhị phân khi pipe (`Get-Content … \| pg_restore`) | Khôi phục database thất bại | Sao lưu/khôi phục chạy **bên trong** container (`backup.sh now`, `restore.sh`), không pipe qua Windows |
| TimescaleDB cần `timescaledb_pre_restore()` / `post_restore()` | Khôi phục có thể lỗi bảng giá (hypertable) | `restore.sh` tự làm; đã kiểm thử khôi phục cả bảng hypertable |
| PC thường tắt lúc 02:30 (giờ backup bản VPS) | Không có bản sao lưu | Đổi sang 12:00 trưa và sao lưu bù khi bật máy |
| 8 container worker riêng tốn RAM | Máy 8 GB bị chậm | Gộp vào một container `worker` (`radar all`) |
| Healthcheck của worker | Docker Desktop hiển thị *unhealthy* gây hiểu nhầm | Healthcheck riêng: worker phải ghi heartbeat trong 5 phút (cũng đã sửa cho bản VPS) |
| Máy ngủ / tắt | Mất dữ liệu stream trong lúc tắt | Tự kết nối lại; ví đang theo dõi được đồng bộ lại lịch sử; nên tắt chế độ ngủ khi cắm sạc |
| Thư mục trong OneDrive / đường dẫn có dấu | Đồng bộ file gây lỗi, đường dẫn dài | Đặt ở `C:\SmartMoneyRadar` |
| Phần mềm diệt virus quét file của Docker | Chậm, đôi khi khóa file | Database nằm trong volume của Docker (không phải thư mục Windows). Nếu vẫn chậm, thêm thư mục dự án vào danh sách loại trừ |
| Docker Hub giới hạn lượt tải khi cài lần đầu (lỗi `429 Too Many Requests`) | `start.bat` báo lỗi khi tải image | Đợi 15–30 phút rồi chạy lại, hoặc đăng nhập Docker Desktop bằng tài khoản Docker miễn phí |
| Giờ hệ thống | — | Dữ liệu lưu theo UTC; dashboard và Telegram hiển thị giờ Việt Nam (GMT+7) |

## 11. Xử lý sự cố nhanh

| Hiện tượng | Cách làm |
|---|---|
| `start.bat` báo *Docker Desktop chua chay* | Mở Docker Desktop, đợi *Engine running*, chạy lại |
| `localhost:8080` không mở được | Chạy `status.bat`; nếu có thành phần không `Up`, chạy `logs.bat` và gửi nội dung |
| Quên mật khẩu dashboard | Chạy lại `setup.bat`, nhập mật khẩu mới, rồi chạy `start.bat` |
| Không có cảnh báo Telegram | Dashboard → Cài đặt → **Gửi tin nhắn thử** |
| Nhiều lỗi `429` trong log | Giảm `HELIUS_RPS` trong `.env` theo đúng gói Helius, rồi chạy `start.bat` |
| Ổ cứng đầy | Docker Desktop → Troubleshoot → **Clean / Purge data** chỉ khi đã có bản sao lưu; hoặc giảm `stream_retention_days` trong Cài đặt |

## 12. Gỡ cài đặt

* Dừng và xóa container, **giữ** dữ liệu: mở `cmd` trong thư mục `smart-money-radar`, chạy `docker compose -f docker-compose.windows.yml down`
* Xóa **cả dữ liệu** (không thể hoàn tác; hãy sao lưu trước): thêm `-v` vào cuối lệnh trên.
