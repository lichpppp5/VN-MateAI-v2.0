# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
train_wake_word.py
==================
Wake Word Trainer — Phase 13.

Ghi lại chính xác Google STT trả về gì khi bạn nói "Hey Lyly".
Tự động cập nhật wake_word_patterns.json để engine nhận ra đúng.

Chạy: python train_wake_word.py
"""
import json
import os
import sys
import speech_recognition as sr

PATTERNS_FILE = "wake_word_patterns.json"
SAMPLES = 5  # Số lần thu mẫu

def load_patterns():
    if os.path.exists(PATTERNS_FILE):
        with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"patterns": [], "tokens": []}

def save_patterns(data):
    with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Đã lưu patterns vào {PATTERNS_FILE}")

def main():
    print("=" * 55)
    print("  VN-MateAI Wake Word Trainer")
    print("=" * 55)
    print()
    print(f"Sẽ thu {SAMPLES} mẫu giọng nói.")
    print("Mỗi lần nghe tiếng beep → nói 'Hey Lyly' to, rõ ràng.")
    print()
    input("Nhấn Enter để bắt đầu...")
    print()

    r = sr.Recognizer()
    r.energy_threshold = 300
    r.dynamic_energy_threshold = True
    r.pause_threshold = 0.6

    collected_en = []
    collected_vi = []

    with sr.Microphone() as source:
        print("Calibrating noise... (1 giây)")
        r.adjust_for_ambient_noise(source, duration=1)
        print(f"Energy threshold: {r.energy_threshold:.0f}")
        print()

        for i in range(SAMPLES):
            print(f"[{i+1}/{SAMPLES}] Nói 'Hey Lyly' ngay bây giờ... ", end="", flush=True)
            try:
                audio = r.listen(source, timeout=5, phrase_time_limit=3)
                print(f"OK ({len(audio.get_raw_data())} bytes)")

                # Try EN
                try:
                    en = r.recognize_google(audio, language="en-US").lower().strip()
                    print(f"  → EN: \"{en}\"")
                    if en:
                        collected_en.append(en)
                except sr.UnknownValueError:
                    print("  → EN: (không rõ)")
                except sr.RequestError as e:
                    print(f"  → EN: Lỗi mạng ({e})")

                # Try VI
                try:
                    vi = r.recognize_google(audio, language="vi-VN").lower().strip()
                    print(f"  → VI: \"{vi}\"")
                    if vi:
                        collected_vi.append(vi)
                except sr.UnknownValueError:
                    print("  → VI: (không rõ)")
                except sr.RequestError as e:
                    print(f"  → VI: Lỗi mạng ({e})")

            except sr.WaitTimeoutError:
                print("Timeout - không nghe thấy gì")
            print()

    print("=" * 55)
    print("KẾT QUẢ THU MẪU:")
    print(f"  EN patterns: {collected_en}")
    print(f"  VI patterns: {collected_vi}")
    print()

    all_transcripts = collected_en + collected_vi
    if not all_transcripts:
        print("❌ Không thu được mẫu nào! Kiểm tra micro và kết nối internet.")
        return

    # Extract tokens (unique words)
    tokens = set()
    phrases = set()
    for t in all_transcripts:
        phrases.add(t)
        for word in t.split():
            if len(word) >= 2:
                tokens.add(word)

    # Load existing and merge
    data = load_patterns()
    existing_phrases = set(data.get("patterns", []))
    existing_tokens = set(data.get("tokens", []))

    new_phrases = phrases - existing_phrases
    new_tokens = tokens - existing_tokens

    data["patterns"] = sorted(existing_phrases | phrases)
    data["tokens"] = sorted(existing_tokens | tokens)
    data["raw_samples"] = data.get("raw_samples", []) + all_transcripts

    save_patterns(data)

    print(f"📊 Tổng patterns: {len(data['patterns'])}")
    print(f"📊 Tổng tokens:   {len(data['tokens'])}")
    if new_phrases:
        print(f"🆕 Phrases mới:   {sorted(new_phrases)}")
    if new_tokens:
        print(f"🆕 Tokens mới:    {sorted(new_tokens)}")

    print()
    print("✅ Đã cập nhật wake_word_patterns.json!")
    print("   Khởi động lại server để áp dụng.")

if __name__ == "__main__":
    main()
