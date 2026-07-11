"""
Generate greeting WAV files for instant playback on the frontend.

Greetings are static — they never change — so we pre-synthesize them once
and serve them from frontend/public/greetings/. The frontend plays the
cached file the moment the user clicks "Start Call", eliminating the 6-7s
wait for the LiveKit agent worker to connect and call session.say().

Run from repo root:
    python scripts/generate_greetings.py

Requires SARVAM_API_KEY in .env (or environment).
Output: frontend/public/greetings/hi-IN.wav  and  mr-IN.wav
"""

import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import requests

SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY")
if not SARVAM_API_KEY:
    sys.exit("ERROR: SARVAM_API_KEY not set in .env")

OUT_DIR = ROOT / "frontend" / "public" / "greetings"
OUT_DIR.mkdir(parents=True, exist_ok=True)

GREETINGS = [
    {
        "lang": "hi-IN",
        "text": "नमस्कार! अपोलो हॉस्पिटल्स में आपका स्वागत है। मैं स्वस्था हूँ, आपकी एआई स्वास्थ्य सहायक। अपॉइंटमेंट बुक करने, सही डॉक्टर या विभाग की जानकारी देने, अस्पताल की सेवाओं से जुड़े सवालों के जवाब देने और आपको उचित टीम से जोड़ने में मैं आपकी मदद कर सकती हूँ। कृपया बताइए, आज मैं आपकी कैसे सहायता कर सकती हूँ?",
        "speaker": "priya",
    },
    {
        "lang": "mr-IN",
        "text": "नमस्कार! अपोलो हॉस्पिटल्समध्ये आपले स्वागत आहे. मी स्वस्था आहे, तुमची एआय आरोग्य सहाय्यक. अपॉइंटमेंट बुक करणे, योग्य डॉक्टर किंवा विभागाची माहिती देणे, रुग्णालयाच्या सेवांबाबत माहिती देणे आणि योग्य विभागाशी जोडणे यासाठी मी तुमची मदत करू शकते. कृपया सांगा, आज मी तुमची कशी मदत करू?",
        "speaker": "kavya",
    },
]


def synthesize(text: str, lang: str, speaker: str) -> bytes:
    resp = requests.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={
            "api-subscription-key": SARVAM_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "inputs": [text],
            "target_language_code": lang,
            "speaker": speaker,
            "model": "bulbul:v3",
            "enable_preprocessing": True,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    # Sarvam returns { "audios": ["<base64 wav>"] }
    audio_b64 = data["audios"][0]
    return base64.b64decode(audio_b64)


for g in GREETINGS:
    out_path = OUT_DIR / f"{g['lang']}.wav"
    print(f"Synthesising {g['lang']} ({g['speaker']})...", end=" ", flush=True)
    try:
        wav_bytes = synthesize(g["text"], g["lang"], g["speaker"])
        out_path.write_bytes(wav_bytes)
        print(f"OK  →  {out_path.relative_to(ROOT)}  ({len(wav_bytes):,} bytes)")
    except Exception as e:
        print(f"FAILED: {e}")

print("\nDone. Commit the generated .wav files so Vercel serves them as static assets.")
