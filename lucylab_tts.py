#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LucyLab/Vivibe TTS - Khai thác preview API để tạo audio miễn phí vô hạn
Cách dùng: python lucylab_tts.py
Hướng dẫn: Xem file HUONG_DAN_SU_DUNG.txt
"""

import requests
import json
import os
import re
import time
import subprocess
import shutil
import sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== CẤU HÌNH ====================
LUCYLAB_API = "https://api.lucylab.io/json-rpc"
TEMP_DIR = "temp_audio"
OUTPUT_DIR = "output_audio"
MAX_CHARS_PER_SEGMENT = 2500      # Số ký tự tối đa mỗi đoạn gửi API
MAX_WORKERS = 3                    # Số request đồng thời (giữ thấp để tránh bị phát hiện)
DELAY_BETWEEN = 1.0               # Delay giữa các request (giây)

# ==================== CÁC HÀM CHÍNH ====================

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_banner():
    print("""
╔══════════════════════════════════════════════╗
║     🎙️  LUCYLAB / VIVIBE TTS TOOL          ║
║     Tạo audio miễn phí không giới hạn       ║
║     Khai thác preview API (creditsUsed: 0)  ║
╚══════════════════════════════════════════════╝
    """)

def get_user_config():
    """Nhập cấu hình từ người dùng khi chạy lần đầu"""
    config_file = "config.json"
    
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            config = json.load(f)
        print(f"📂 Đã tải cấu hình từ {config_file}")
        return config
    
    print("🔧 CẤU HÌNH LẦN ĐẦU - Cần nhập các thông tin sau:\n")
    print("1. Lấy JWT Token:")
    print("   - Vào https://www.vivibe.app, đăng nhập Google")
    print("   - F12 → Application → Local Storage → https://www.vivibe.app")
    print("   - Tìm key 'jwt' hoặc 'token', copy giá trị")
    print("   - HOẶC: F12 → Network → nhấn play thử 1 câu → click request 'json-rpc'")
    print("   - Tab Headers → copy toàn bộ giá trị sau 'Bearer ' trong dòng Authorization\n")
    
    jwt = input("JWT Token: ").strip()
    
    print("\n2. Lấy Voice ID của giọng bạn muốn:")
    print("   - Trên Vivibe, chọn giọng (vd: Ngọc Huyền)")
    print("   - Nhấn play thử 1 câu, F12 → Network → request 'json-rpc'")
    print("   - Tab Payload → copy giá trị 'userVoiceId'\n")
    
    voice_id = input("Voice ID: ").strip()
    
    print("\n3. Tốc độ đọc (0.5 - 2.0, mặc định 1.0):")
    speed = input("Tốc độ [1.0]: ").strip() or "1.0"
    
    config = {
        "jwt_token": jwt,
        "voice_id": voice_id,
        "speed": float(speed),
        "max_chars": MAX_CHARS_PER_SEGMENT,
        "workers": MAX_WORKERS,
        "delay": DELAY_BETWEEN
    }
    
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    print(f"\n✅ Đã lưu cấu hình vào {config_file}")
    return config


def update_jwt(config):
    """Cập nhật JWT token mới"""
    print("\n🔄 JWT Token đã hết hạn hoặc không hợp lệ.")
    print("   Vào Vivibe, đăng nhập lại, lấy JWT mới từ F12 → Network → json-rpc → Authorization")
    new_jwt = input("JWT Token mới: ").strip()
    if new_jwt:
        config["jwt_token"] = new_jwt
        with open("config.json", 'w') as f:
            json.dump(config, f, indent=2)
        print("✅ Đã cập nhật JWT mới!")
    return config


def split_text_into_segments(text, max_chars=MAX_CHARS_PER_SEGMENT):
    """
    Chia văn bản thành các đoạn, mỗi đoạn <= max_chars ký tự.
    Ưu tiên cắt ở dấu chấm, xuống dòng, hoặc dấu phẩy để giữ giọng đọc tự nhiên.
    """
    if len(text) <= max_chars:
        return [text]
    
    segments = []
    current = ""
    
    # Tách theo câu trước
    sentences = re.split(r'(?<=[.!?…])\s+', text)
    
    for sentence in sentences:
        if len(current) + len(sentence) <= max_chars:
            current += (" " if current else "") + sentence
        else:
            if current.strip():
                segments.append(current.strip())
            
            # Nếu 1 câu đã vượt max_chars, cắt theo dấu phẩy hoặc từ
            if len(sentence) > max_chars:
                sub_segments = split_long_sentence(sentence, max_chars)
                segments.extend(sub_segments)
                current = ""
            else:
                current = sentence
    
    if current.strip():
        segments.append(current.strip())
    
    return segments


def split_long_sentence(sentence, max_chars):
    """Cắt câu quá dài thành nhiều phần nhỏ hơn"""
    parts = []
    words = sentence.split()
    current = ""
    
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current += (" " if current else "") + word
        else:
            if current:
                parts.append(current.strip())
            current = word
    
    if current.strip():
        parts.append(current.strip())
    
    return parts


def call_tts_api(text, voice_id, speed, jwt_token):
    """
    Gọi LucyLab JSON-RPC API để lấy URL audio preview.
    Trả về URL audio hoặc None nếu lỗi.
    """
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
        "Origin": "https://www.vivibe.app",
        "Referer": "https://www.vivibe.app/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    }
    
    payload = {
        "method": "tts",
        "input": {
            "text": text,
            "userVoiceId": voice_id,
            "speed": speed,
            "blockVersion": 0
        }
    }
    
    try:
        r = requests.post(LUCYLAB_API, json=payload, headers=headers, timeout=60)
        
        if r.status_code == 200:
            data = r.json()
            
            # Kiểm tra lỗi từ API
            if "error" in data:
                error_msg = data["error"].get("message", str(data["error"]))
                return {"error": error_msg}
            
            # Thành công - lấy URL audio
            if "result" in data and "url" in data["result"]:
                return {
                    "url": data["result"]["url"],
                    "credits_used": data["result"].get("creditsUsed", 0),
                    "credits_remaining": data["result"].get("creditsRemaining", "N/A")
                }
        elif r.status_code == 401 or r.status_code == 403:
            return {"error": "JWT_EXPIRED"}
        else:
            return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def download_audio(url, filepath):
    """Tải file audio từ URL về local"""
    try:
        r = requests.get(url, timeout=120, stream=True)
        if r.status_code == 200:
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return os.path.getsize(filepath) > 500
    except Exception as e:
        print(f"    Lỗi tải: {e}")
    return False


def process_segment(args):
    """Xử lý 1 đoạn văn bản: gọi API → tải audio. Dùng cho ThreadPoolExecutor."""
    index, text, voice_id, speed, jwt_token, output_dir, prefix = args
    
    result = call_tts_api(text, voice_id, speed, jwt_token)
    
    if "error" in result:
        return {"index": index, "success": False, "error": result["error"]}
    
    audio_url = result["url"]
    filename = f"{prefix}_{index:04d}.wav"
    filepath = os.path.join(output_dir, filename)
    
    if download_audio(audio_url, filepath):
        return {
            "index": index,
            "success": True,
            "filepath": filepath,
            "filename": filename,
            "credits_used": result.get("credits_used", 0),
            "text_length": len(text)
        }
    else:
        return {"index": index, "success": False, "error": "Download failed"}


def convert_to_mp3(input_file, output_file):
    """Chuyển đổi WAV sang MP3 bằng ffmpeg"""
    try:
        cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-acodec", "libmp3lame", "-b:a", "192k",
            output_file
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return os.path.exists(output_file) and os.path.getsize(output_file) > 0
    except:
        return False


def merge_wav_files(wav_files, output_file):
    """Ghép nhiều file WAV thành 1 file MP3"""
    if not wav_files:
        return False
    
    # Tạo file list
    list_file = os.path.join(TEMP_DIR, "merge_list.txt")
    with open(list_file, 'w', encoding='utf-8') as f:
        for fp in wav_files:
            escaped = fp.replace("'", "'\\''").replace("\\", "\\\\")
            f.write(f"file '{escaped}'\n")
    
    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-acodec", "libmp3lame", "-b:a", "192k",
            output_file
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return os.path.exists(output_file) and os.path.getsize(output_file) > 0
    except Exception as e:
        print(f"  Lỗi ffmpeg: {e}")
        return False


def process_text_file(input_file, config):
    """Xử lý toàn bộ file text → tạo audio"""
    output_prefix = os.path.splitext(os.path.basename(input_file))[0]
    segment_output_dir = os.path.join(OUTPUT_DIR, output_prefix + "_segments")
    os.makedirs(segment_output_dir, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    # Đọc file
    with open(input_file, 'r', encoding='utf-8') as f:
        full_text = f.read()
    
    if not full_text.strip():
        print("❌ File rỗng!")
        return
    
    print(f"\n{'='*60}")
    print(f"📖 File: {input_file}")
    print(f"📏 Tổng: {len(full_text)} ký tự")
    
    # Chia thành đoạn
    segments = split_text_into_segments(full_text, config["max_chars"])
    print(f"✂️  Chia thành {len(segments)} đoạn (max {config['max_chars']} ký tự/đoạn)")
    
    # Hiển thị preview các đoạn
    print(f"\n📋 Preview các đoạn:")
    for i, seg in enumerate(segments[:5], 1):
        preview = seg[:80].replace('\n', ' ') + ("..." if len(seg) > 80 else "")
        print(f"   Đoạn {i}: {len(seg)} ký tự | {preview}")
    if len(segments) > 5:
        print(f"   ... và {len(segments) - 5} đoạn nữa")
    
    # Xác nhận
    print(f"\n⏱️  Dự kiến: {len(segments)} request, mỗi request cách {config['delay']}s")
    print(f"   Thời gian ước tính: {len(segments) * (config['delay'] + 3) / 60:.1f} phút")
    confirm = input("\n▶️  Bắt đầu xử lý? (Y/n): ").strip().lower()
    if confirm == 'n':
        print("❌ Đã hủy.")
        return
    
    # Xử lý từng đoạn
    start_time = time.time()
    results = []
    errors = []
    jwt_expired = False
    
    print(f"\n{'='*60}")
    print(f"⬇️  ĐANG TẢI AUDIO PREVIEW (miễn phí, không tốn credit)")
    print(f"{'='*60}\n")
    
    # Chuẩn bị args cho thread pool
    tasks = []
    for i, seg in enumerate(segments, 1):
        tasks.append((i, seg, config["voice_id"], config["speed"], 
                      config["jwt_token"], segment_output_dir, output_prefix))
    
    with ThreadPoolExecutor(max_workers=config["workers"]) as executor:
        futures = {executor.submit(process_segment, task): task[0] for task in tasks}
        
        for future in as_completed(futures):
            result = future.result()
            idx = result["index"]
            
            if result["success"]:
                results.append(result)
                fname = result["filename"]
                chars = result["text_length"]
                print(f"  ✅ Đoạn {idx:04d}: {fname} ({chars} ký tự)")
            else:
                error = result.get("error", "Unknown")
                errors.append({"index": idx, "error": error})
                
                if error == "JWT_EXPIRED":
                    jwt_expired = True
                    print(f"  ⚠️  Đoạn {idx:04d}: JWT HẾT HẠN! Cần cập nhật token mới.")
                    # Hủy các futures còn lại
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    break
                else:
                    print(f"  ❌ Đoạn {idx:04d}: {error}")
            
            time.sleep(config["delay"])
    
    elapsed = time.time() - start_time
    
    # Xử lý kết quả
    print(f"\n{'='*60}")
    print(f"📊 KẾT QUẢ")
    print(f"{'='*60}")
    print(f"  ✅ Thành công: {len(results)}/{len(segments)} đoạn")
    print(f"  ❌ Lỗi: {len(errors)} đoạn")
    print(f"  ⏱️  Thời gian: {elapsed/60:.1f} phút")
    
    if results:
        total_credits = sum(r.get("credits_used", 0) for r in results)
        print(f"  💰 Credits đã dùng: {total_credits} (đáng lẽ phải là 0)")
    
    if jwt_expired:
        print(f"\n⚠️  JWT TOKEN HẾT HẠN!")
        print(f"  Các file đã tải thành công được lưu trong: {segment_output_dir}/")
        print(f"  Để tiếp tục: chạy lại script, chọn 'Cập nhật JWT' từ menu chính")
        return
    
    if not results:
        print("❌ Không có đoạn nào thành công!")
        return
    
    # Sắp xếp kết quả theo index
    results.sort(key=lambda x: x["index"])
    wav_files = [r["filepath"] for r in results]
    
    # Tạo file MP3 tổng hợp từ tất cả các đoạn
    merged_mp3 = os.path.join(OUTPUT_DIR, f"{output_prefix}_FULL.mp3")
    print(f"\n🔧 Đang ghép {len(wav_files)} đoạn thành 1 file MP3...")
    
    if merge_wav_files(wav_files, merged_mp3):
        size_mb = os.path.getsize(merged_mp3) / (1024 * 1024)
        print(f"  ✅ Đã tạo: {merged_mp3} ({size_mb:.2f} MB)")
    else:
        print(f"  ⚠️  Không thể ghép file. Các file riêng lẻ nằm trong: {segment_output_dir}/")
        # Vẫn convert từng file riêng lẻ sang MP3
        print(f"\n🔧 Đang chuyển đổi từng đoạn sang MP3...")
        mp3_dir = os.path.join(OUTPUT_DIR, output_prefix + "_mp3")
        os.makedirs(mp3_dir, exist_ok=True)
        for r in results:
            wav_path = r["filepath"]
            mp3_path = os.path.join(mp3_dir, r["filename"].replace('.wav', '.mp3'))
            if convert_to_mp3(wav_path, mp3_path):
                print(f"  ✅ {r['filename']} → MP3")
    
    # Dọn dẹp file temp
    try:
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)
    except:
        pass
    
    print(f"\n{'='*60}")
    print(f"🎉 HOÀN TẤT!")
    print(f"{'='*60}")
    print(f"📂 File tổng hợp: {merged_mp3 if os.path.exists(merged_mp3) else 'Không có'}")
    print(f"📂 Các file riêng lẻ: {segment_output_dir}/")
    print(f"   Tổng: {len(results)} file WAV")
    print(f"\n💡 MẸO: Bạn có thể dùng ffmpeg để ghép thủ công nếu cần:")
    print(f"   ffmpeg -f concat -safe 0 -i filelist.txt -c copy output.wav")


def main_menu():
    """Menu chính"""
    config = get_user_config()
    
    while True:
        clear_screen()
        print_banner()
        print(f"📋 Cấu hình hiện tại:")
        print(f"   Voice ID: {config['voice_id']}")
        print(f"   Tốc độ: {config['speed']}x")
        print(f"   Max ký tự/đoạn: {config['max_chars']}")
        print(f"   Workers: {config['workers']} | Delay: {config['delay']}s")
        print(f"   JWT Token: {config['jwt_token'][:30]}...{config['jwt_token'][-10:] if len(config['jwt_token']) > 40 else config['jwt_token']}")
        print()
        print("=" * 60)
        print("📋 MENU:")
        print("  1. Tạo audio từ file .txt")
        print("  2. Tạo audio từ văn bản dán trực tiếp")
        print("  3. Cập nhật JWT Token")
        print("  4. Đổi Voice ID")
        print("  5. Đổi tốc độ")
        print("  6. Kiểm tra file trong output")
        print("  7. Thoát")
        print("=" * 60)
        
        choice = input("\n👉 Chọn (1-7): ").strip()
        
        if choice == '1':
            # Tạo audio từ file
            print("\n📂 Kéo thả file .txt vào đây hoặc nhập đường dẫn:")
            filepath = input("Đường dẫn file: ").strip().strip('"').strip("'")
            
            if os.path.exists(filepath) and filepath.endswith('.txt'):
                process_text_file(filepath, config)
            else:
                print("❌ File không tồn tại hoặc không phải .txt!")
            
            input("\nNhấn Enter để về menu...")
        
        elif choice == '2':
            # Tạo audio từ text dán trực tiếp
            print("\n📝 Dán nội dung văn bản (nhập END trên dòng riêng để kết thúc):")
            lines = []
            while True:
                line = input()
                if line == "END":
                    break
                lines.append(line)
            
            text = "\n".join(lines)
            if text.strip():
                # Lưu tạm
                temp_file = os.path.join(TEMP_DIR, "temp_input.txt")
                os.makedirs(TEMP_DIR, exist_ok=True)
                with open(temp_file, 'w', encoding='utf-8') as f:
                    f.write(text)
                process_text_file(temp_file, config)
            else:
                print("❌ Không có nội dung!")
            
            input("\nNhấn Enter để về menu...")
        
        elif choice == '3':
            config = update_jwt(config)
            input("\nNhấn Enter để về menu...")
        
        elif choice == '4':
            print("\n🎤 Nhập Voice ID mới:")
            print("   (Vào Vivibe → chọn giọng → F12 → Network → play thử → copy userVoiceId)")
            new_voice = input("Voice ID mới: ").strip()
            if new_voice:
                config["voice_id"] = new_voice
                with open("config.json", 'w') as f:
                    json.dump(config, f, indent=2)
                print("✅ Đã cập nhật Voice ID!")
            input("\nNhấn Enter để về menu...")
        
        elif choice == '5':
            new_speed = input("\n⏩ Tốc độ mới (0.5 - 2.0): ").strip()
            try:
                speed = float(new_speed)
                if 0.5 <= speed <= 2.0:
                    config["speed"] = speed
                    with open("config.json", 'w') as f:
                        json.dump(config, f, indent=2)
                    print("✅ Đã cập nhật tốc độ!")
                else:
                    print("❌ Tốc độ phải từ 0.5 đến 2.0!")
            except:
                print("❌ Không hợp lệ!")
            input("\nNhấn Enter để về menu...")
        
        elif choice == '6':
            print(f"\n📂 Nội dung thư mục output ({OUTPUT_DIR}):")
            if os.path.exists(OUTPUT_DIR):
                for item in sorted(os.listdir(OUTPUT_DIR)):
                    item_path = os.path.join(OUTPUT_DIR, item)
                    if os.path.isdir(item_path):
                        file_count = len([f for f in os.listdir(item_path) if f.endswith('.wav')])
                        print(f"  📁 {item}/ ({file_count} file)")
                    else:
                        size_mb = os.path.getsize(item_path) / (1024 * 1024)
                        print(f"  🎵 {item} ({size_mb:.2f} MB)")
            else:
                print("  (trống)")
            input("\nNhấn Enter để về menu...")
        
        elif choice == '7':
            print("\n👋 Tạm biệt!")
            break
        
        else:
            print("❌ Chọn không hợp lệ!")
            input("Nhấn Enter để thử lại...")


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    # Nếu có tham số dòng lệnh, xử lý nhanh
    if len(sys.argv) >= 2:
        config = get_user_config()
        filepath = sys.argv[1]
        if os.path.exists(filepath):
            process_text_file(filepath, config)
        else:
            print(f"❌ File không tồn tại: {filepath}")
    else:
        main_menu()