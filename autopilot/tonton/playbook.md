# Tonton Thông Thái (@tontonthongthai): Playbook

Kênh tiếng Việt, phong cách người que, chủ đề "Vì sao…" về cơ thể người và tiến hoá. Lập ngày 2026-10-17. Cập nhật sau mỗi lần đọc số liệu.

## Số liệu nền (2026-10-17: 24 sub, 16 video, 5.069 view)

| Video | View | Ghi chú |
|---|---|---|
| Vì Sao Con Người Có Dấu Vân Tay? | 3.021 | 8:30, đăng 18:30 VN. Chiếm 60% tổng view của kênh |
| Vì sao chúng ta lại nổi da gà? | 470 | 7:10, video gần nhất (17/08) |
| Vì Sao Con Người Thuận Tay Phải Nhiều Hơn Tay Trái? | 362 | 9:17 |
| 13 video còn lại | 51–154 | Tiền sử, tiến hoá trừu tượng, lịch sử thảm hoạ |

Kênh đăng dồn trong khoảng 22/07–01/08, có ngày đăng 2 video, rồi ngừng từ 17/08.

## Công thức đang thắng

- **Chủ đề:** một hiện tượng trên **chính cơ thể người xem**, ai cũng từng gặp và có thể tự kiểm tra ngay khi xem (vân tay, nổi da gà, tay thuận).
- **Tiêu đề:** "Vì sao con người / chúng ta …?", ngắn, dưới 60 ký tự, từ khoá đặt ở đầu.
- **Hook 0:00–0:20:** đặt người xem vào tình huống quen thuộc ("Bạn không lạnh. Không có gì chạm vào người…"), đưa ra nghịch lý, rồi câu hỏi "Tại sao?".
- **Độ dài:** 7–9 phút.
- **Cấu trúc:**
  - Bác bỏ lời giải quen thuộc ("nghe rất hợp lý, cho đến khi…").
  - Giải thích cơ chế bên dưới da, có sơ đồ người que.
  - Giải thích lý do tiến hoá.
  - Một bí ẩn chưa có lời giải.
- **Kết:** sau câu trả lời chỉ một câu kêu gọi like, share, subscribe (chủ kênh chốt 17/10/2026 cho cả 2 kênh).

## Tránh

- Tiền sử hay tiến hoá trừu tượng không gắn với cơ thể người xem (Neanderthal, âm nhạc, thuần hoá, bản đồ). Nhóm này đều dưới 160 view.
- Lịch sử thảm hoạ (London 1952).
- Đăng dồn nhiều video một ngày.
- Khẳng định y khoa chắc chắn khi nghiên cứu còn tranh cãi. Trong trường hợp đó, dùng "các nhà khoa học cho rằng", "một giả thuyết là".

## Chủ đề dự trữ (cùng công thức)

- Ngáp, và vì sao ngáp lây
- Nấc cụt
- Giật mình khi sắp ngủ
- Không tự cù lét được chính mình
- Nghe giọng mình trong ghi âm thấy lạ
- Răng khôn
- Hắt hơi khi nhìn nắng
- "Não đông" khi ăn kem
- Bụng kêu khi đói
- Tê chân "kiến bò"
- Déjà vu

## Định dạng source script (giống script của chủ kênh, dán vào "AI lọc content · Người que")

```
# <Tiêu đề>

## CẢNH 1: <TÊN CẢNH>
**Thời gian: 0:00–0:15**
[HÌNH ẢNH]
<chỉ dẫn hình người que, mỗi ý một dòng>
[LỜI THOẠI]
<lời đọc tiếng Việt, câu ngắn, mỗi câu một dòng>
[NHỊP ĐỘ]
<nhanh / chậm / dừng>
---
```

Trong lời thoại, số viết bằng chữ ("chín trên mười người"). Không chào, không giới thiệu kênh trước hook. Nguồn ghi trong `sources.md`, không đưa vào lời thoại.

## Mặc định sản xuất (giống thao tác tay của chủ kênh)

- **Ngôn ngữ:** vi-VN → vi-VN, giọng TTS theo cấu hình backend cho vi-VN.
- **Video:** chế độ scenes, kế hoạch hình v2, nvidia_nvenc, chất lượng high, **sceneReveal tắt (hiệu ứng mặc định)**. Chủ kênh chốt 19/10/2026: không dùng nét vẽ chì nữa; chỉ bật hiệu ứng khi chủ kênh yêu cầu cho video cụ thể.
- **Visual style: mỗi video một phong cách vẽ riêng, không nêu màu cụ thể** (chủ kênh chốt 18/10/2026). Nêu màu cụ thể dễ làm các video rập khuôn; ba video đầu bị đơn điệu xanh lam vì playbook, script và visual_style đều ghi "xanh lam + cam đỏ".
  - Mỗi run có `style.txt` (tiếng Anh, một đoạn). Nội dung: người que 2D + một phong cách hợp chủ đề, ví dụ sách tranh thiếu nhi màu bột, in risograph, cắt giấy collage, poster khoa học retro, truyện tranh pop-art, màu nước. Thêm "rich harmonious palette chosen to suit each scene's mood, never monotone". Không ghi tên màu. Không lặp phong cách của 2–3 video gần nhất.
  - `studio.py produce` chép `style.txt` vào visual_style và style reference trước khi lập kế hoạch hình. AI chọn bảng màu (color director) sẽ tự chọn màu hợp với từng video.
  - Trong `[HÌNH ẢNH]` của script không ghi tên màu. Cần nhấn thì viết "tô màu nổi bật" hoặc "mũi tên màu khác".
  - Đã dùng: tự cù lét = hoạt hình giấy kem, mảng màu nước mềm (vẽ trước khi chốt quy tắc, ảnh đẹp nên giữ); răng khôn = poster khoa học retro in lụa, chấm halftone.
  - Dừng produce giữa chừng thì gọi `POST /api/scenes/cancel` cho project: backend vẫn vẽ ngầm sau khi client thoát, và luồng đó ghi đè kế hoạch hình mới.
  - Sau khi vẽ xong references và vài cảnh đầu, xem ảnh và đánh giá: màu có đa dạng, đẹp, cuốn hút không. Nếu đơn điệu thì dừng lại, đổi `style.txt`, bỏ `plan_approved` trong state.json rồi chạy lại.
- **Phụ đề:** giống Nemo. Chỉ 16x9, không burn.

## Lịch đăng

- **Khung giờ:** thứ Ba và thứ Sáu, 18:30 giờ Việt Nam. Ba video tốt nhất đều đăng 18:30; các video đăng 07:00 đều dưới 155 view.
- **Đã xếp lịch:**
  - 17/10/2026 23:45: Ngáp lây (FeC15p2bCFk). Chủ kênh chọn đăng ngay, ngoài khung giờ; theo dõi view để so với khung 18:30.
  - 20/10/2026 18:30: Nấc cụt (3MCgJwOjLfA).
  - 23/10/2026 18:30: Tự cù lét (AGqWKSux9sQ).
  - 27/10/2026 18:30: Răng khôn (L8DuwD0qs7I).
  - 30/10/2026 18:30: Giật mình khi sắp ngủ (FvlLMQn6Zmg).
  - 03/11/2026 18:30: Giọng trong ghi âm (z2u99xxrjjI).
  - 06/11/2026 18:30: Não đông khi ăn kem (TiYZVJkhrMc).
  - 10/11/2026 18:30: Hắt hơi khi nhìn nắng (lYL5bad3x8w).

## Đóng gói YouTube

Cách làm giống Nemo: viết `packaging.json` theo định dạng ở `../nemo/playbook.md`, thumbnail do Codex vẽ.

- **Title (vi):** "Vì sao …?", ≤ 60 ký tự. Không viết hoa từng chữ. Không emoji.
- **Description (vi):**
  1. 2–3 câu hook, lặp lại câu hỏi.
  2. Phần `Mốc thời gian:` bắt đầu từ `0:00`, lấy thời gian từ `captions.srt`.
  3. Phần `Nguồn:` lấy từ `sources.md`, kèm dòng "Hình minh hoạ: hoạt hình người que, không phải ảnh thật."
  4. Gợi ý xem video vân tay.
  5. Câu hỏi mời comment.
  6. Dòng cuối có 3–5 hashtag (#VìSao #CơThểNgười #KhoaHọc …).
- **Tags:** ≤ 500 ký tự. Thứ tự:
  1. Từ khoá chính.
  2. Biến thể "tại sao…", "…để làm gì".
  3. Thuật ngữ khoa học.
  4. 3–5 tag tiếng Anh.

  (Hai video tốt nhất không có tag, nên tag chỉ là phụ.)
- **EN localization:** có. Dịch title và description sang tiếng Anh. Chi phí thấp, giúp video xuất hiện trong tìm kiếm tiếng Anh.
- **Thumbnail (CTR):**
  - Cùng phong cách người que trong video.
  - Một bộ phận cơ thể phóng to chiếm ≥ 40% khung.
  - Một nhân vật người que biểu cảm mạnh.
  - Chữ tiếng Việt 2–4 từ, to, đậm, viền đen, không lặp nguyên tiêu đề (vd "ĐỂ LÀM GÌ?").
  - Góc dưới phải để trống.
  - **Kiểm tra dấu tiếng Việt từng chữ**: model ảnh dễ vẽ sai dấu. Sai thì xoá `cand_N.png` và chạy lại. Nếu vẫn sai, dùng thumbnail không chữ.
  - Màu tươi, tương phản mạnh. Prompt thumbnail không ghi tên màu, kể cả màu chữ. Ghi:
    - "same art style and palette as the style reference image";
    - nền: "one bold flat colour picked from the style reference image's own palette (never a colour that does not appear in it), contrasting with the subject";
    - chữ: "in the brightest colour of the palette with thick black outline".

    Nếu chỉ ghi "one bold flat colour that makes the subject pop", model hay tự chọn nền cyan (tự cù lét, 18/10).
  - Chữ có dấu khó (TRƯỚC, NGƯỢC, NGẮN…): thêm gợi ý cách viết, vd "the last word is TRƯỚC: U with a horn, then O with a horn and an acute accent". Không có gợi ý thì "NGẮN" bị vẽ thành "NGĂN" (răng khôn, 18/10).
  - Xem cả 3 ứng viên ở cỡ feed 360px trên nền tối và nền sáng. Chủ thể cùng tông với nền (xương hàm vàng trên nền vàng) sẽ chìm ở cỡ nhỏ.
  - **Bắt buộc tự đánh giá CTR từng ứng viên** trước khi chọn (chủ kênh chốt 18/10/2026). Xem ảnh ở cỡ nhỏ và chấm từng tiêu chí:
    1. Chủ thể chính chiếm ≥ 40% khung, nhìn là hiểu ngay.
    2. Biểu cảm nhân vật mạnh.
    3. Có khoảng trống tò mò khiến người xem muốn bấm.
    4. Chữ 2–4 từ, đúng dấu, đọc được ở cỡ nhỏ.
    5. Màu nổi bật, tương phản mạnh.
    6. Góc dưới phải trống.
    7. Không có chi tiết gây hiểu nhầm hay phản cảm.

    Ứng viên nào trượt một tiêu chí thì xoá và vẽ lại (sửa prompt nếu cần). Chỉ chọn ảnh đạt hết, rồi ghi lý do chọn vào báo cáo.
