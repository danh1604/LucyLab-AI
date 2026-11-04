# Nemo (@nemotiori) — Playbook

Kênh tiếng Đức, người que, lịch sử/văn hoá. Ngày lập: 2026-10-16. Cập nhật sau mỗi lần đọc số liệu.

## Số liệu nền (2026-10-16, 38 sub, 5 video)

| Video | View | Ghi chú |
|---|---|---|
| Warum gibt es deutsche Dörfer mitten in Rumänien? | 8.088 | 572s, có title EN + caption en-US |
| Graun (làng chìm, tháp chuông) | 236 | dấu vết Đức/nói tiếng Đức ở nước khác |
| Brasilien (Pomerode / Hunsrückisch) | 176 | cùng công thức Rumänien, mới 18 ngày |
| Kindheit 1975 | 113 | hoài niệm, ngôi "du" |
| Schamanin 9.000 Jahre | 68 | khảo cổ bí ẩn |

## Công thức đang thắng

- **Chủ đề:** "Dấu vết Đức ở nơi không ngờ": cộng đồng/làng/ngôn ngữ Đức ngoài nước Đức.
- **Tiêu đề:** câu hỏi "Warum …?" + nghịch lý địa lý ("deutsche X mitten in Y"). Dưới 70 ký tự.
- **Hook 0:00–0:35:** hình ảnh gây nhầm lẫn ("Dieses Haus steht nicht in Deutschland"), rồi hé lộ, rồi hứa một chi tiết cụ thể ở cuối.
- **Độ dài:** 8–10 phút (~1.300–1.600 từ tiếng Đức).
- **Cấu trúc:** 6–8 đoạn. Mỗi đoạn kết bằng một câu kéo sang đoạn sau. Có 1 nhân vật/gia đình thật để kể. Có đoạn nêu mặt tối (xung đột, trục xuất, đồng hoá) để giữ độ tin cậy.
- **Kết:** sau câu trả lời chỉ một câu kêu gọi like, share, subscribe (chủ kênh chốt 17/10/2026). Không trỏ sang video khác, vì API không gắn được end screen.
- **Mỗi video:** thêm title/description tiếng Anh (bản dịch) như video Rumänien.

## Tránh

- Khảo cổ/bí ẩn chung chung, hoài niệm "du" kéo dài (view thấp nhất).
- Clickbait sai sự thật; ảnh AI trình bày như ảnh thật (ghi "KI-Rekonstruktion").
- Đăng dồn dập. Tối đa 3–4 video/tuần, mỗi video có nghiên cứu nguồn riêng (chính sách nội dung hàng loạt 15/07/2025).

## Lịch đăng

- **Khung giờ:** thứ Tư và thứ Bảy, 17:00 giờ Berlin. Giờ Việt Nam là 22:00 khi Đức dùng giờ mùa hè (CEST), 23:00 khi dùng giờ mùa đông (CET).
- **Đã xếp lịch:**
  - 17/10/2026: Wolgadeutsche.
  - 24/10/2026: Colonia Tovar (9rb44HLmmkg).
  - 28/10/2026: Mennoniten in Mexiko (PaTLRxs24P4).
  - 31/10/2026: Texas-Deutsche (93FZo6R1cG0).
  - 04/11/2026: Namibia / Swakopmund (RiMlMf_ggvg).
  - 07/11/2026: Nueva Germania (S0OHm_sKBKQ).
  - 11/11/2026: Qingdao / Tsingtau (ybZmfHH_lew).
  - 14/11/2026: Bandō / Beethovens Neunte (4G9ZOaFgo9E).
  - 18/11/2026: Unserdeutsch (tgt5Pvo50FA).

## Định dạng source script (dán vào "AI lọc content · Người que")

```
1. <Tên đoạn tiếng Việt>
0:00–0:35
Hướng dẫn dựng: <chỉ dẫn hình ảnh bằng tiếng Việt, chữ trên màn hình trong „…“>
Lời đọc:
<câu tiếng Đức, mỗi câu một dòng; nhãn nguồn ở cuối dòng nếu có, ví dụ: Hunsrück Museum>
2. ...
```

Số và năm viết bằng chữ trong lời đọc (achtzehnhundertvierundzwanzig). Không chào, không logo trước hook.

## Mặc định sản xuất (giống thao tác tay của chủ kênh)

- Ngôn ngữ: de-DE → de-DE, Edge TTS `de-DE-FlorianMultilingualNeural`.
- Video: chế độ scenes, kế hoạch hình người que v2, nvidia_nvenc, chất lượng high, sceneReveal tắt.
- **Visual style: mỗi video một phong cách vẽ riêng, không nêu màu cụ thể** (chủ kênh chốt 18/10/2026, áp dụng cả 2 kênh). Mỗi run có `style.txt` theo cách làm ở `../tonton/playbook.md`. Không ghi tên màu trong `style.txt`, phần "Hướng dẫn dựng" của script hay prompt thumbnail. Không lặp phong cách của 2–3 video gần nhất.
  - Đã dùng: 5 video đầu (Wolgadeutsche → Namibia) = storybook mực + màu nước trên giấy cũ; Nueva Germania = sổ tay thám hiểm thực vật, mực + gouache; Qingdao = poster du lịch in thạch bản, bưu thiếp cũ; Bandō = tranh khắc gỗ ukiyo-e; Unserdeutsch = cắt dán giấy nhiều lớp.
- Phụ đề: chỉ 16x9, JetBrains Mono NL SemiBold 54, chữ trắng viền đen 3, không burn (giống video Brasilien).

## Đóng gói YouTube (Claude viết `packaging.json`, Codex vẽ thumbnail)

`produce` dừng sau phụ đề → viết `<run>/packaging.json`; dừng lần hai sau khi vẽ `thumbnails/cand_*.png` → xem, điền `"thumbnail"`; chạy lại → upload PRIVATE kèm ngôn ngữ de, category 27, caption `captions.srt` (de), thumbnail, bản EN.

```json
{"title": "...", "description": "...", "tags": ["..."],
 "localizations": {"en": {"title": "...", "description": "..."}},
 "thumbnail_prompts": ["...", "...", "..."], "thumbnail": "thumbnails/cand_N.png"}
```

- **Title (de):** "Warum …?" + nghịch lý địa lý, ≤ 70 ký tự, từ khoá chính nằm trong 40 ký tự đầu. Không viết hoa toàn bộ, không emoji.
- **Description (de):**
  1. 2 câu hook (lặp lại câu hỏi + hứa chi tiết), chứa từ khoá chính trong 150 ký tự đầu.
  2. `Kapitel:` từ `0:00`, 6–8 mốc lấy thời gian thật trong `captions.srt` (mỗi mốc ≥ 10 giây).
  3. `Quellen:` từ `sources.md` (tên nguồn + URL), dòng "Bilder: KI-Rekonstruktion, keine historischen Fotos."
  4. Trỏ sang video Rumänien (+ video cùng loạt), câu hỏi comment.
  5. Dòng cuối: 3–5 hashtag (#Geschichte #Wolgadeutsche …). 3 hashtag đầu hiện trên tiêu đề.
- **Tags:** ≤ 500 ký tự (validator tự kiểm), thêm biến thể không dấu (Doerfer) như video Rumänien. Thứ tự: từ khoá chính → tên riêng (địa danh, nhân vật, cộng đồng) → chủ đề rộng (deutsche Geschichte, Auswanderung) → 3–5 tag EN.
- **EN localization:** `localizations.en` dịch title/description, giữ chapters và nguồn (video Rumänien có en-US + caption EN và là video duy nhất bùng nổ).
- **Thumbnail (CTR):** 3 ý tưởng khác hẳn nhau để chọn (hoặc A/B "Test & Compare" trong Studio, làm tay).
  - Phong cách giống cảnh trong video (theo `style.txt` của run), nhân vật mặc trang phục thời kỳ (studio tự gửi ảnh `scenes/references/style` làm tham chiếu).
  - Một chủ thể rõ, chiếm ≥ 40% khung; 1 nhân vật cận cảnh biểu cảm mạnh (ngạc nhiên/sợ) nhìn về phía điều bí ẩn.
  - Nghịch lý nhìn thấy được trong 1 giây (vd: làng Đức tháp nhà thờ giữa thảo nguyên Nga).
  - 2–4 chữ tiếng Đức to, đậm, viền đen, không lặp nguyên title (vd "DEUTSCH? IN RUSSLAND?"). Chữ đặt một bên, không đè chủ thể.
  - Tương phản cao hơn cảnh trong video: bão hoà màu, 2–3 màu chính lấy từ bảng màu của ảnh style reference. Chữ dùng màu sáng nhất của bảng màu, viền đen dày. Prompt không ghi tên màu (Tonton 18/10: không ghi thì model hay tự chọn nền cyan). Góc dưới phải để trống (đồng hồ thời lượng).
  - Đọc được ở cỡ 168×94. Không logo, không khung viền, không chữ nhỏ.
  - Prompt mẫu: `YouTube thumbnail, 16:9. Same art style and palette as the style reference image, but more saturated and higher contrast, never monotone. Background: one bold tone picked from the style reference image's own palette (never a colour that does not appear in it), contrasting with the subject. Scene: <chủ thể + nghịch lý>. One character in period clothing in the foreground, close-up, <biểu cảm>, looking at <điều bí ẩn>. Bold German text "<2–4 TỪ>" in large letters in the brightest colour of the palette with thick black outline on the <left/right> third. High contrast, uncluttered, nothing in the bottom-right corner, readable at small size.`
  - **Tự đánh giá CTR từng ứng viên** theo 7 tiêu chí ở `../tonton/playbook.md` (chủ kênh chốt 18/10/2026). Ứng viên nào trượt thì xoá và vẽ lại.
  - Xem ảnh: chữ Đức đúng chính tả (ä ö ü ß), không có chữ thừa; sai thì xoá `cand_N.png` và chạy lại.
