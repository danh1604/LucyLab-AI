# Autopilot kênh YouTube: quy trình vận hành

Claude vận hành kênh từ đầu đến cuối. Chủ kênh chỉ duyệt ở **2 cổng**, không cần nhắc việc. Trao đổi với chủ kênh bằng tiếng Việt.

Mỗi kênh có một thư mục `autopilot/<kênh>/`, gồm:

| File | Nội dung |
|---|---|
| `profile.json` | Ngôn ngữ, giọng TTS, timezone, cài đặt video/phụ đề giống thao tác tay |
| `playbook.md` | Số liệu, công thức đang thắng, định dạng script, quy tắc đóng gói. Cập nhật sau mỗi lần đọc số liệu |
| `runs/<YYYY-MM-DD_slug>/` | Một video. Không commit (`.gitignore`) |

## Quy trình một video

1. **Chọn chủ đề.**
   - Theo "Công thức đang thắng" trong playbook.
   - Không trùng các video đã có.
   - Mỗi chủ đề phải có nguồn kiểm chứng riêng.
2. **Nghiên cứu và viết.**
   - Tạo `runs/<run>/`, gồm:
     - `sources.md`: các nguồn, mỗi nguồn ghi rõ đã dùng để kiểm chứng ý nào.
     - `script.md`: theo định dạng trong playbook.
     - `state.json`: `{"title": "<tiêu đề>"}`.
3. **CỔNG 1: duyệt kịch bản.** Gửi cho chủ kênh:
   - Tóm tắt từng video.
   - Tiêu đề.
   - Đường dẫn `script.md`.
4. **Sản xuất.**
   - Chạy `python autopilot/studio.py produce runs/<run>` ở chế độ chạy nền.
   - Thời gian khoảng 1h15–1h20 máy chạy. Riêng ảnh cảnh mất ~45 phút.
   - Nếu lỗi: chạy lại `produce`, studio tự resume từ bước dừng.
5. **Đóng gói.**
   - Lần dừng 1: viết `packaging.json` theo playbook, chapters lấy thời gian từ `captions.srt`.
   - Lần dừng 2: xem `thumbnails/cand_*.png`, chọn ảnh rồi điền `"thumbnail"`.
   - Chạy lại `produce`, video được upload ở chế độ **PRIVATE**.
6. **CỔNG 2: duyệt đăng.** Gửi cho chủ kênh:
   - Link video private.
   - Tiêu đề và thumbnail đã chọn.
   - Giờ đăng đề xuất.
7. **Lên lịch.**
   - Chỉ chạy sau khi chủ kênh đồng ý cho đúng video đó.
   - Lệnh: `python autopilot/studio.py publish runs/<run> <YYYY-MM-DDTHH:MM>`, giờ theo timezone trong profile.

## Làm theo lô

- Có thể duyệt cổng 1 cho 3–4 kịch bản cùng lúc.
- Sản xuất lần lượt từng video. Các video dùng chung backend và gpt2api.
- Các video lô xếp vào khung giờ cố định của kênh, xem mục "Lịch đăng" trong playbook. `published_at` trong `state.json` cho biết khung nào đã có video.

## Nhiều kênh

- Backend chỉ giữ một token YouTube, đặt tại `output_audio/_shared/youtube/token.json`.
- `studio.py` lưu thêm một bản riêng cho mỗi kênh: `token.<channel_id>.json`.
- Trước mỗi lần upload, publish hoặc refresh, `studio.py` đổi sang token của đúng kênh trong `profile.json` (`youtube.channel_id`). Nếu kênh không khớp thì dừng lại.
- Kênh mới: chủ kênh tự kết nối một lần trong UI. Không cần kết nối lại cho kênh cũ, vì token của kênh cũ đã được lưu.

## Không bao giờ

- Không đăng công khai hay lên lịch khi chưa có lời duyệt cho đúng video đó.
- Không nhập mật khẩu hay đăng nhập thay chủ kênh. Không in key hay mật khẩu trong `config.json`.
- Không sửa file `.py` trong `backend/` khi đang có `produce` chạy. Uvicorn `--reload` sẽ khởi động lại và cắt luồng SSE đang chạy.

## Kiểm tra trên YouTube

- Sau khi upload hoặc update, `videos.list` có thể trả về dữ liệu cũ trong vài phút.
- Nên đọc vài lần, cách nhau ~10 giây, và tin bản mới nhất có trạng thái `processed`.
- Lệnh update khi video còn đang xử lý sẽ được YouTube xếp hàng, không bị mất.
