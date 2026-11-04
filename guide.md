# 🎙️ HƯỚNG DẪN SỬ DỤNG LUCYLAB / VIVIBE TTS TOOL

> **Tạo audio miễn phí không giới hạn bằng cách khai thác preview API**
>
> API preview trả về `creditsUsed: 0` → không tốn credit → tải về miễn phí

---

## 📋 MỤC LỤC

- [Công cụ này làm gì?](#c%C3%B4ng-c%E1%BB%A5-n%C3%A0y-l%C3%A0m-g%C3%AC)
- [Yêu cầu hệ thống](#y%C3%AAu-c%E1%BA%A7u-h%E1%BB%87-th%E1%BB%91ng)
- [Cài đặt lần đầu](#c%C3%A0i-%C4%91%E1%BA%B7t-l%E1%BA%A7n-%C4%91%E1%BA%A7u)
- [Lấy JWT Token và Voice ID](#l%E1%BA%A5y-jwt-token-v%C3%A0-voice-id)
- [Sử dụng](#s%E1%BB%AD-d%E1%BB%A5ng)
- [Cấu trúc thư mục output](#c%E1%BA%A5u-tr%C3%BAc-th%C6%B0-m%E1%BB%A5c-output)
- [Mẹo & Thủ thuật](#m%E1%BA%B9o--th%E1%BB%A7-thu%E1%BA%ADt)
- [Xử lý lỗi thường gặp](#x%E1%BB%AD-l%C3%BD-l%E1%BB%97i-th%C6%B0%E1%BB%9Dng-g%E1%BA%B7p)
- [Câu hỏi thường gặp](#c%C3%A2u-h%E1%BB%8Fi-th%C6%B0%E1%BB%9Dng-g%E1%BA%B7p)

---

## Công cụ này làm gì?

| Tính năng             | Mô tả                                                         |
| --------------------- | ------------------------------------------------------------- |
| 🎤 **Text-to-Speech** | Chuyển văn bản thành giọng nói (bất kỳ giọng nào trên Vivibe) |
| 💰 **Miễn phí 100%**  | Khai thác API preview (`creditsUsed: 0`)                      |
| ♾️ **Không giới hạn** | Không giới hạn số ký tự, số lần gọi                           |
| 📦 **Tự động ghép**   | Chia đoạn → tải audio → ghép thành file MP3 hoàn chỉnh        |
| 🖥️ **Giao diện menu** | Menu interactive dễ sử dụng trên terminal                     |

---

## Yêu cầu hệ thống

| Thành phần           | Phiên bản | Cài đặt                                        |
| -------------------- | --------- | ---------------------------------------------- |
| **Python**           | 3.8+      | [python.org](https://python.org)               |
| **requests**         | latest    | `pip install requests`                         |
| **FFmpeg**           | latest    | [ffmpeg.org](https://ffmpeg.org/download.html) |
| **Tài khoản Google** | \-        | Để đăng nhập Vivibe                            |

### Cài FFmpeg

````bash
# Windows (winget)
winget install ffmpeg

# macOS (Homebrew)
brew install ffmpeg

# Linux (apt)
sudo apt install ffmpeg

---

## Cài đặt lần đầu

### Bước 1: Chạy script

```bash
python lucylab_tts.py
````

Script sẽ tự động phát hiện chưa có file `config.json` và chuyển sang chế độ cấu hình lần đầu.

### Bước 2: Nhập 3 thông tin

Script sẽ yêu cầu bạn nhập:

1. **JWT Token** (xác thực API)
2. **Voice ID** (giọng đọc)
3. **Tốc độ đọc** (0.5 - 2.0)

> 💡 Cấu hình được lưu vào `config.json`, lần sau chạy không cần nhập lại.

---

## Lấy JWT Token và Voice ID

### 🔑 Lấy JWT Token

> ⚠️ **JWT Token chỉ có hiệu lực \~1 giờ.** Hết hạn cần lấy lại.

1.  Mở trình duyệt, vào [**https://www.vivibe.app**](https://www.vivibe.app)
2.  **Đăng nhập** bằng tài khoản Google
3.  Nhấn `F12` → tab `Network`
4.  Nhập một câu bất kỳ vào ô text
5.  Chọn giọng đọc (ví dụ: Ngọc Huyền)
6.  Nhấn nút **▶️ PLAY** (nghe thử từng câu)
7.  Trong tab Network, tìm request đến `api.lucylab.io/json-rpc`
8.  Click vào request đó → tab `Headers`
9.  Tìm dòng `Authorization`
10. Copy **toàn bộ** giá trị sau chữ `Bearer `(rất dài, khoảng 1000 ký tự)

```
Authorization: Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjJmMjk1... (copy hết dòng này)
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                  Phần cần copy (sau chữ "Bearer ")
```

### 🎤 Lấy Voice ID

1. Trong **cùng request** ở trên
2. Chuyển sang tab `Payload`
3. Tìm trường `userVoiceId`
4. Copy giá trị

```json
{
  "method": "tts",
  "input": {
    "text": "...",
    "userVoiceId": "mhsL3CPLxmLYdSTKp3GANz",  ← Đây là Voice ID
    "speed": 1.0,
    "blockVersion": 0
  }
}
```

### 📋 Danh sách Voice ID tham khảo

| Giọng      | Voice ID (cần xác minh) |
| ---------- | ----------------------- |
| Ngọc Huyền | _(tự lấy từ Vivibe)_    |
| Ban Mai    | _(tự lấy từ Vivibe)_    |
| Long       | _(tự lấy từ Vivibe)_    |

> Mỗi lần đổi giọng trên Vivibe, `userVoiceId` sẽ thay đổi. Hãy lấy lại Voice ID tương ứng.

---

## Sử dụng

### Chạy script

```bash
python lucylab_tts.py
```

### Menu chính

```
📋 MENU:
  1. Tạo audio từ file .txt
  2. Tạo audio từ văn bản dán trực tiếp
  3. Cập nhật JWT Token
  4. Đổi Voice ID
  5. Đổi tốc độ
  6. Kiểm tra file trong output
  7. Thoát
```

### Option 1: Tạo audio từ file .txt

1. Chọn `1`
2. Kéo thả file `.txt` vào terminal hoặc nhập đường dẫn đầy đủ
3. Script hiển thị preview các đoạn
4. Nhấn `Enter` để xác nhận bắt đầu
5. Chờ script tải audio và ghép file

**Định dạng file .txt:**

- Encode: **UTF-8**
- Nội dung: văn bản tiếng Việt có dấu
- Không giới hạn độ dài

### Option 2: Dán văn bản trực tiếp

1. Chọn `2`
2. Dán nội dung văn bản
3. Gõ `END` trên một dòng riêng để kết thúc
4. Script tự động lưu tạm và xử lý

### Option 3: Cập nhật JWT Token

Khi gặp lỗi `JWT_EXPIRED`:

1. Vào Vivibe, **F5 refresh** trang
2. Bắt request mới → lấy JWT mới
3. Chọn option `3` → dán JWT mới

---

## Cấu trúc thư mục output

```
output_audio/
│
├── TENFILE_FULL.mp3                 ← File tổng hợp đã ghép (tất cả đoạn)
│
└── TENFILE_segments/                ← Các file WAV riêng lẻ từng đoạn
    ├── TENFILE_0001.wav
    ├── TENFILE_0002.wav
    ├── TENFILE_0003.wav
    ├── TENFILE_0004.wav
    └── ...
```

### Giải thích

| File/Thư mục        | Mô tả                                   |
| ------------------- | --------------------------------------- |
| `TENFILE_FULL.mp3`  | File MP3 hoàn chỉnh đã ghép tất cả đoạn |
| `TENFILE_segments/` | Thư mục chứa từng file WAV riêng lẻ     |
| `TENFILE_0001.wav`  | File audio của đoạn 1                   |
| `TENFILE_0002.wav`  | File audio của đoạn 2                   |

> Tên file dựa trên tên file `.txt` gốc. Ví dụ: `chuong1.txt` → `chuong1_FULL.mp3`

---

## Mẹo & Thủ thuật

### 🚀 Gửi cả đoạn dài thay vì từng câu

- Script mặc định chia đoạn **2500 ký tự** / lần gọi API
- API LucyLab **không giới hạn cứng** độ dài text
- **Có thể tăng lên 4000-5000** ký tự để giảm số request
- Sửa trong file `config.json`:

```json
{
  "max_chars": 4000
}
```

### ⚡ Tăng tốc độ xử lý

| Tham số   | Mặc định | Ý nghĩa                               |
| --------- | -------- | ------------------------------------- |
| `workers` | 3        | Số request đồng thời                  |
| `delay`   | 1.0      | Thời gian chờ giữa các request (giây) |

```json
{
  "workers": 5,
  "delay": 0.5
}
```

> ⚠️ **Cảnh báo**: Nếu để quá nhanh có thể bị **rate limit** hoặc **chặn IP**.

### 🔄 Đổi IP khi bị chặn

Nếu bị chặn IP:

1. Dùng **SIM 4G** (USB tethering từ điện thoại)
2. Bật/tắt **chế độ máy bay** 10 giây để đổi IP
3. Chạy lại script

### 📂 Xử lý nhiều file cùng lúc

```bash
# Xử lý tuần tự
python lucylab_tts.py chuong1.txt
python lucylab_tts.py chuong2.txt
python lucylab_tts.py chuong3.txt
```

### 🎵 Ghép thủ công nếu cần

```bash
# Tạo file list
ls output_audio/TENFILE_segments/*.wav | sort > filelist.txt

# Ghép với ffmpeg
ffmpeg -f concat -safe 0 -i filelist.txt -acodec libmp3lame -b:a 192k output.mp3
```

---

## Xử lý lỗi thường gặp

### ❌ `JWT_EXPIRED`

**Nguyên nhân**: Token hết hạn sau \~1 giờ.

**Cách fix**:

1. Vào Vivibe, F5 refresh trang
2. Bắt request mới → lấy JWT mới
3. Vào script → chọn option `3` → dán JWT mới

### ❌ `HTTP 401 / 403`

**Nguyên nhân**: JWT không hợp lệ hoặc bị thu hồi.

**Cách fix**: Đăng nhập lại Vivibe, lấy JWT hoàn toàn mới.

### ❌ `HTTP 429`

**Nguyên nhân**: Rate limit - gọi API quá nhanh.

**Cách fix**:

- Tăng `delay` lên 2-3 giây
- Giảm `workers` xuống 1-2
- Đợi 5-10 phút rồi thử lại

### ❌ File audio = 0 bytes hoặc rất nhỏ

**Nguyên nhân**: Text quá ngắn hoặc API lỗi.

**Cách fix**: Kiểm tra nội dung đoạn text, thử lại.

### ❌ `ModuleNotFoundError: No module named 'requests'`

**Cách fix**:

```bash
pip install requests
```

### ❌ `ffmpeg: command not found`

**Cách fix**: Cài đặt FFmpeg (xem [Yêu cầu hệ thống](#y%C3%AAu-c%E1%BA%A7u-h%E1%BB%87-th%E1%BB%91ng))

---

## Câu hỏi thường gặp

### Q: Có bị khóa tài khoản không?

**A:** Preview API không tốn credit và không giới hạn số lần gọi. Tuy nhiên, nếu lạm dụng quá mức (hàng nghìn request/giờ), Vivibe có thể:

- Rate limit tạm thời
- Chặn IP
- Vô hiệu hóa JWT token

Không ảnh hưởng đến tài khoản Google gốc.

### Q: Chất lượng audio có khác gì bản export trả phí không?

**A:** Giống hệt. Cả preview và export đều gọi chung backend LucyLab, chỉ khác là preview gắn cờ `creditsUsed: 0`.

### Q: Có cần đăng nhập mỗi lần chạy không?

**A:** Không. JWT lưu trong `config.json`, chỉ cần cập nhật khi hết hạn (\~1 giờ). Nếu script đang chạy mà JWT hết hạn giữa chừng, các đoạn đã tải vẫn được lưu.

### Q: Có thể dùng cho giọng khác ngoài Ngọc Huyền không?

**A:** Có. Chỉ cần đổi **Voice ID** (option 4 trong menu). Mỗi giọng trên Vivibe có một Voice ID riêng.

### Q: Tôi có thể bán audio tạo ra từ tool này không?

**A:** Tool này dành cho mục đích cá nhân. Việc thương mại hóa audio từ preview API có thể vi phạm điều khoản dịch vụ của Vivibe/LucyLab.

---

## 📞 Hỗ trợ

Nếu gặp vấn đề:

1. Kiểm tra lại JWT token còn hạn không
2. Kiểm tra Voice ID đúng chưa
3. Đọc lại [Xử lý lỗi thường gặp](#x%E1%BB%AD-l%C3%BD-l%E1%BB%97i-th%C6%B0%E1%BB%9Dng-g%E1%BA%B7p)

---

> ⚡ **Tip cuối cùng**: API LucyLab cho phép gửi text rất dài (test được 5000 ký tự/lần). Hãy tận dụng điều này để giảm số request, tăng tốc độ, và ít bị rate limit hơn.
