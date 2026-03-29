"""
gemini_vision.py
역할: Google Gemini API를 활용하여 9:16 세로형 쇼츠 배경 이미지를 생성합니다.
      (DALL-E 3 크레딧 소진 시 폴백으로 사용됩니다)
"""

import os
import time
import logging
from pathlib import Path

from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def generate_background_image_gemini(prompt: str) -> Path:
    logger.info("=== Gemini API 이미지 생성 시작 (폴백) ===")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-pro")

    logger.info("프롬프트: %s", prompt)
    response = model.generate_content(
        f"{prompt}, vertical 9:16 aspect ratio, no text, no watermarks, cinematic, high quality",
        generation_config={"response_mime_type": "image/png"},
    )

    timestamp = int(time.time())
    file_path = DATA_DIR / f"background_{timestamp}.png"
    with open(file_path, "wb") as f:
        f.write(response.parts[0].blob.data)

    logger.info("Gemini 이미지 생성 완료: %s", file_path)
    return file_path