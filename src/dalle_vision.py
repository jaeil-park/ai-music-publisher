"""
dalle_vision.py
역할: OpenAI DALL-E 3 API를 활용하여 9:16 세로형 쇼츠 배경 이미지를 생성합니다.
"""

import os
import time
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from openai import RateLimitError

class InsufficientCreditsError(Exception):
    """DALL-E 3 크레딧 부족 시 발생하는 커스텀 예외"""
    pass

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def generate_background_image(prompt: str) -> Path:
    logger.info("=== DALL-E 3 이미지 생성 시작 ===")
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    
    logger.info("프롬프트: %s", prompt)
    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt + " vertical aspect ratio, no text, no watermarks.",
            size="1024x1792",
            quality="standard",
            n=1,
        )
    except RateLimitError as e:
        if "quota" in str(e).lower():
            logger.warning("DALL-E 3 크레딧 소진 감지. Gemini로 폴백합니다.")
            raise InsufficientCreditsError("DALL-E 3 크레딧 소진") from e
        else:
            raise # 크레딧 문제가 아닌 다른 RateLimit 에러는 그대로 발생

    image_url = response.data[0].url
    logger.info("이미지 URL 획득. 다운로드 중...")

    img_response = requests.get(image_url, timeout=60)
    img_response.raise_for_status()
    
    timestamp = int(time.time())
    file_path = DATA_DIR / f"background_{timestamp}.png"

    file_path.write_bytes(img_response.content)
    logger.info("DALL-E 3 이미지 생성 완료: %s", file_path)
    return file_path