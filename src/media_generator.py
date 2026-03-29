"""
media_generator.py
역할: Stability AI API를 사용하여 오디오 및 9:16 배경 이미지를 생성합니다.
"""

import os
import time
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

STABILITY_API_KEY = os.getenv("STABILITY_API_KEY")

def generate_and_download_audio(prompt: str) -> Path:
    logger.info("=== Stability Audio 생성 시작 ===")
    if not STABILITY_API_KEY:
        raise ValueError("STABILITY_API_KEY가 설정되지 않았습니다.")

    url = "https://api.stability.ai/v2beta/stable-audio/generate"
    headers = {"Authorization": f"Bearer {STABILITY_API_KEY}", "Accept": "audio/*"}
    data = {
        "prompt": prompt,
        "seconds_total": 180,
        "output_format": "mp3",
    }
    files = {"none": ""}

    logger.info("오디오 프롬프트: %s", prompt)
    response = requests.post(url, headers=headers, data=data, files=files, timeout=300)
    response.raise_for_status()

    file_path = DATA_DIR / f"{int(time.time())}_audio.mp3"
    with open(file_path, "wb") as f:
        f.write(response.content)
    logger.info("Stability Audio 생성 완료: %s", file_path)
    return file_path

def generate_background_image(prompt: str) -> Path:
    logger.info("=== Stability Image 생성 시작 ===")
    if not STABILITY_API_KEY:
        raise ValueError("STABILITY_API_KEY가 설정되지 않았습니다.")

    url = "https://api.stability.ai/v2beta/stable-image/generate/core"
    headers = {"Authorization": f"Bearer {STABILITY_API_KEY}", "Accept": "image/*"}
    data = {"prompt": prompt, "aspect_ratio": "9:16", "output_format": "png"}
    files = {"none": ""}

    logger.info("이미지 프롬프트: %s", prompt)
    response = requests.post(url, headers=headers, data=data, files=files, timeout=60)
    response.raise_for_status()

    file_path = DATA_DIR / f"{int(time.time())}_bg.png"
    with open(file_path, "wb") as f:
        f.write(response.content)
    logger.info("Stability Image 생성 완료: %s", file_path)
    return file_path