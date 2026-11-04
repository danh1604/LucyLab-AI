# LucyLab Story Studio

Ứng dụng local-first để biến một kịch bản thành video YouTube hoàn chỉnh: viết/lọc nội dung bằng AI, TTS, dựng ảnh theo scene, phụ đề, nhạc nền, Shorts và tài sản xuất bản.

LucyLab phục vụ hai luồng chính:

- **Audio Story**: truyện kể hoặc nội dung tự viết → TTS → video nền → phụ đề → xuất bản.
- **Người que / explainer**: paste kịch bản có cấu trúc → AI tách lời đọc, shot plan, asset reference và storyboard contact sheet → tạo ảnh final theo từng shot → render video.

> Dự án lưu media, session và cấu hình API ở máy cục bộ. Các tệp này bị loại khỏi Git để tránh lộ khóa và làm repository phình to.

---

## Quick Start

```bat
git clone https://github.com/ThangTo/LucyLab.git
cd LucyLab
install.bat
```

Sau đó edit `config.json` (đã tự tạo từ template) để thêm API keys, rồi chạy:

```bat
start.bat
```

Mở http://localhost:9000.

---

## Yêu cầu hệ thống

| Thành phần | Phiên bản | Bắt buộc | Cài đặt |
|---|---|---|---|
| **Windows** | 10/11 | Có | — |
| **Python** | 3.11+ | Có | [python.org](https://python.org) (check "Add to PATH") |
| **Node.js** | 18+ | Có | [nodejs.org](https://nodejs.org) |
| **ffmpeg + ffprobe** | mới nhất | Có | `winget install ffmpeg` hoặc [ffmpeg.org](https://ffmpeg.org/download.html) |
| **NVIDIA GPU** | — | Không | Để render NVENC nhanh hơn |
| **Real-ESRGAN** | — | Không | Để upscale thumbnail cục bộ |
| **gpt2api** | — | Không | Để dùng tài khoản GPT local cho tạo ảnh |

---

## Cài đặt chi tiết

### Bước 1: Clone và chạy install

```bat
git clone https://github.com/ThangTo/LucyLab.git
cd LucyLab
install.bat
```

`install.bat` sẽ:
1. Tìm Python (venv, py launcher, hoặc PATH)
2. Kiểm tra Node.js và ffmpeg
3. Tạo `config.json` từ template nếu chưa có
4. Cài Python dependencies (`backend/requirements.txt`)
5. Cài frontend dependencies và build (`npm install` + `npm run build`)

### Bước 2: Cấu hình

Edit `config.json` với các API keys của bạn. Mở file trong bất kỳ text editor nào.

**Tối thiểu cần thiết để chạy:**

| Key | Mô tả | Lấy ở đâu |
|---|---|---|
| `jwt_token` | JWT token cho TTS (Vivibe) | Vivibe → F12 → Network → json-rpc → Authorization |
| `voice_id` | Voice ID giọng đọc | Vivibe → chọn giọng → F12 → Network → userVoiceId |

**Tùy chọn (theo provider bạn chọn):**

| Key | Khi nào cần |
|---|---|
| `anthropic_api_key` | Dùng Claude cho viết truyện |
| `custom_image_api_key` | Dùng OpenAI-compatible API cho tạo ảnh |
| `safe_prompt_api_key` | Dùng LLM cho kiểm tra nội dung an toàn |
| `language_agent_api_key` | Dùng LLM cho dịch/localize nội dung |

### Bước 3: Chạy

```bat
start.bat          # Production mode: http://localhost:9000
start-dev.bat      # Dev mode: http://localhost:5700 (hot reload)
```

---

## Cấu hình providers

Toàn bộ cấu hình được nhập trong **Settings** của app và được ghi vào `config.json`. Không commit file này.

### TTS (bắt buộc)

Để tạo audio, bạn cần cấu hình TTS provider:

1. **Vivibe** (mặc định cho tiếng Việt):
   - Vào https://www.vivibe.app, đăng nhập Google
   - F12 → Network → nhấn play thử 1 câu → click request `json-rpc`
   - Tab Headers → copy giá trị sau `Bearer ` trong dòng Authorization → paste vào `jwt_token`
   - Tab Payload → copy `userVoiceId` → paste vào `voice_id`

2. **Edge TTS** (miễn phí, cho tiếng Anh/Hàn/Nhật):
   - Không cần API key
   - Vào Settings → Voice theo ngôn ngữ → chọn Edge TTS → chọn giọng

3. **Azure Speech / ElevenLabs**:
   - Cần API key riêng
   - Vào Settings → Kết nối TTS provider → nhập key

### Image (tùy chọn, để tạo ảnh scene)

**Option A: Custom provider (đơn giản nhất)**
- Dùng bất kỳ OpenAI-compatible API nào (OpenAI, Azure, proxy...)
- Trong Settings → Image API → chọn provider `custom`
- Nhập `base_url`, `api_key`, `model`

**Option B: gpt2api local (dùng tài khoản GPT miễn phí)**
- Xem hướng dẫn bên dưới

### LLM (tùy chọn, để viết truyện/dịch)

- **Anthropic Claude**: Nhập `anthropic_api_key` trong Settings
- **Ollama local**: Cài Ollama, chạy `ollama pull <model>`, config `ollama_url`
- **Custom provider**: Bất kỳ OpenAI-compatible API nào

---

## Hướng dẫn cài gpt2api (tùy chọn)

gpt2api là service local giúp dùng tài khoản ChatGPT/GPT để tạo ảnh miễn phí qua OpenAI-compatible API.

### Yêu cầu

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (hoặc WSL2 với Ubuntu)
- Tài khoản ChatGPT/GPT có hiệu lực

### Cài đặt

```powershell
# 1. Clone gpt2api
git clone https://github.com/432539/gpt2api.git D:\Project\gpt2api
cd D:\Project\gpt2api

# 2. Khởi động gpt2api (Docker mode)
.\start-gpt2api.ps1

# 3. Setup tài khoản và API key
.\setup-gpt2api-local.ps1
```

Script setup sẽ:
- Đăng nhập vào gpt2api admin (admin/admin123)
- Tạo tài khoản GPT từ OAuth token của bạn
- Tạo local API key → lưu vào `.local/openai-compatible-api-key.txt`

### Cấu hình LucyLab để dùng gpt2api

Trong `config.json` hoặc Settings của LucyLab:

```json
{
  "image_generation_provider": "gpt2api",
  "scene_gpt2api_base_url": "http://127.0.0.1:17200/v1",
  "scene_gpt2api_api_key": "sk-klein-...",
  "scene_gpt2api_asset_base_url": "http://127.0.0.1:17080",
  "gpt2api_admin_base_url": "http://127.0.0.1:17188",
  "gpt2api_admin_user": "admin",
  "gpt2api_admin_password": "admin123",
  "gpt2api_autostart": true,
  "gpt2api_start_script": "D:\\Project\\gpt2api\\start-gpt2api.ps1"
}
```

LucyLab sẽ tự khởi động gpt2api khi cần tạo ảnh (nếu `gpt2api_autostart: true`).

### Ports của gpt2api

| Port | Service |
|---|---|
| 17080 | User frontend |
| 17088 | Admin frontend |
| 17180 | User API |
| 17188 | Admin API |
| **17200** | **OpenAI-compatible API (LucyLab dùng cái này)** |

---

## Cài Real-ESRGAN (tùy chọn)

Real-ESRGAN giúp upscale thumbnail lên độ phân giải cao hơn.

1. Tải binary từ [GitHub Releases](https://github.com/xinntao/Real-ESRGAN/releases)
2. Giải nén vào `tools/realesrgan/` (cùng thư mục với `README_windows.md`)
3. Cần có: `realesrgan-ncnn-vulkan.exe`, `vcomp140.dll`, và thư mục `models/`

```
tools/realesrgan/
  realesrgan-ncnn-vulkan.exe
  vcomp140.dll
  models/
    realesrgan-x4plus.param
    realesrgan-x4plus.bin
    ...
```

---

## Kiến trúc

```text
React + Vite (frontend :5700 khi dev)
                │
                ▼
FastAPI (backend :9000)
 ├─ Story / LLM providers
 ├─ TTS providers (Vivibe, Edge TTS, Azure, ElevenLabs)
 ├─ Image providers (custom API / gpt2api local)
 ├─ FFmpeg / FFprobe
 ├─ Real-ESRGAN (tùy chọn)
 └─ Local project store: output_audio/<project>/
```

## Tính năng

- Viết truyện qua prompt hoặc interview AI; sửa trực tiếp trước khi tạo TTS.
- Quản lý session tách biệt theo project, tránh lẫn audio/video giữa các truyện.
- TTS theo đoạn với nhiều provider (Vivibe, Edge TTS, Azure, ElevenLabs).
- Normalize số sang dạng đọc cho ngôn ngữ khác (Korean, English, v.v.).
- Tạo video từ ảnh scene với image provider tùy chỉnh, reference image, retry và render GPU/CPU fallback.
- Luồng người que có visual bible, asset tái sử dụng, shot timing bám theo audio thực tế và storyboard contact sheet 1–3 trang.
- Subtitle preview/render với CUDA hybrid acceleration, background bo góc, nhạc nền.
- Cắt audio bằng waveform, cắt video, loop video, ghép outro và tạo teaser/climax/Shorts.
- Thumbnail Director: LLM đọc kịch bản + visual bible để tạo 3 concept × 2 biến thể, channel format layout.
- Chuẩn bị tiêu đề, mô tả, thumbnail và tài sản YouTube.
- Tích hợp tùy chọn `gpt2api` để dùng tài khoản cục bộ cho tạo ảnh.

## Luồng làm video người que

1. Ở **Nội dung**, chọn **AI lọc content · Người que** và paste kịch bản.
2. LLM tách `tts_text`, reusable references và **final shot plan**. Shot được chia theo thay đổi về hành động, evidence, map, biểu đồ, text overlay hoặc góc máy.
3. Sau TTS, hệ thống retime shot theo metadata audio thật.
4. Ở bước Video, tạo asset reference và storyboard contact sheet:
   - Storyboard là 1–3 ảnh nhiều ô để xem toàn bộ sequence.
   - Storyboard không phải ảnh final và không được gán lại vào từng scene.
   - Ảnh final dùng style/character/location/prop reference tái sử dụng.
5. Tạo các scene image còn thiếu, render video và tiếp tục sang phụ đề/hậu kỳ.

## Cấu trúc thư mục

```text
LucyLab/
├─ backend/                 # FastAPI, TTS, scene/video, subtitle, thumbnail, publishing services
├─ frontend/                # React + Vite + Tailwind UI
├─ tools/realesrgan/        # Chỉ giữ hướng dẫn; runtime/model tải local khi cần
├─ output_audio/            # Project/session/media tạo ra tại máy (ignored)
├─ config.json              # Provider keys và local settings (ignored)
├─ config.example.json      # Template cho config.json
├─ install.bat              # Cài đặt dependencies
├─ start-dev.bat            # Chạy dev mode (hot reload)
└─ start.bat                # Chạy production mode
```

## Kiểm thử

Chạy các test backend:

```bat
cd backend
py -m pytest test_tts_service.py test_subtitle_service.py test_thumbnail_director_service.py -v
```

## Khắc phục lỗi nhanh

| Triệu chứng | Kiểm tra |
|---|---|
| Không mở được app | Kiểm tra port 9000 bị chiếm, chạy `start.bat` lại. |
| `config.json not found` | Chạy `install.bat` để tạo từ template. |
| `Python not found` | Cài Python 3.11+ và check "Add to PATH". |
| `Node.js not found` | Cài Node.js 18+ từ nodejs.org. |
| `ffmpeg not found` | Cài ffmpeg: `winget install ffmpeg`. |
| Không tạo được audio | Kiểm tra `jwt_token` và `voice_id` trong Settings. |
| Ảnh lỗi provider | Kiểm tra API key; với gpt2api kiểm tra Docker đang chạy. |
| Frontend cũ / trắng | Chạy lại `npm run build` trong thư mục `frontend/`. |

## Ghi chú phát triển

- Không commit `config.json` hoặc generated media.
- `config.example.json` là template — giữ nó đồng bộ khi thêm config key mới.
- Giữ test cho các thay đổi ảnh hưởng session, scene timing, subtitle render và provider integration.
- Khi thêm provider mới, cấu hình phải đi qua Settings hoặc biến môi trường local; không hard-code secret.
