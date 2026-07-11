"""Generate pre-recorded fallback WAV files via Sarvam TTS.

Run once after setup:
    python scripts/generate_fallback_wavs.py

Output: frontend/public/fallback/hi-IN.wav, mr-IN.wav
"""

import os
import base64
from pathlib import Path
from sarvamai import SarvamAI

FALLBACK_TEXTS = {
    "hi-IN": ("मुझे समझ नहीं आया — मैं आपको रिसेप्शन से जोड़ती हूँ।", "priya"),
    "mr-IN": ("मला नीट समजलं नाही — मी तुम्हाला रिसेप्शनशी जोडते.", "kavya"),
}

OUT_DIR = Path(__file__).parent.parent / "frontend" / "public" / "fallback"
OUT_DIR.mkdir(parents=True, exist_ok=True)

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

for lang, (text, speaker) in FALLBACK_TEXTS.items():
    resp = client.text_to_speech.convert(
        inputs=[text],
        target_language_code=lang,
        speaker=speaker,
        model="bulbul:v3",
        enable_preprocessing=True,
    )
    audio_b64 = resp.audios[0]
    wav_bytes = base64.b64decode(audio_b64)
    out_path = OUT_DIR / f"{lang}.wav"
    out_path.write_bytes(wav_bytes)
    print(f"Written {out_path} ({len(wav_bytes)} bytes)")
