"""
stability_audio.py
역할: Stability AI 공식 Audio API를 사용하여 음원을 생성하고 MP3로 저장합니다.
"""

import os
import time
import logging
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

STABILITY_API_KEY = os.getenv("STABILITY_API_KEY")

def generate_and_download_audio(prompt: str = "Chillhop, Lo-fi, Study music, relax, beat") -> Path:
    logger.info("=== Stability Audio 생성 시작 ===")
    if not STABILITY_API_KEY:
        raise ValueError("STABILITY_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

    url = "https://api.stability.ai/v2beta/stable-audio/generate"
    headers = {
        "Authorization": f"Bearer {STABILITY_API_KEY}",
        "Accept": "audio/*"
    }
    data = {
        "prompt": prompt,
        "seconds_total": 60,
    }

    logger.info("프롬프트: %s", prompt)
    response = requests.post(url, headers=headers, data=data, timeout=300)
    
    if response.status_code != 200:
        raise RuntimeError(f"Stability API 에러: {response.status_code} - {response.text}")

    timestamp = int(time.time())
    file_path = DATA_DIR / f"{timestamp}_audio.mp3"

    with open(file_path, "wb") as f:
        f.write(response.content)

    logger.info("Stability Audio 생성 완료: %s", file_path)
    return file_path