import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from curl_cffi import requests
from src.media_generator import _request_suno_generation, suno_auth

# 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def verify_gen():
    logger.info("Suno 음원 생성 API 검증 시작...")
    
    # .env 로드
    load_dotenv()
    
    try:
        # 1. 초기 토큰 로드
        suno_auth.load_initial_token()
        
        # 2. 아주 간단한 컨셉으로 생성 요청
        concept = {
            "title": "Test Song",
            "genre": "jazz",
            "audio_prompt": "jazz piano",
            "lyrics": "[Verse 1]\nHello Suno, please work."
        }
        
        with requests.Session(impersonate="chrome124") as session:
            # _request_suno_generation 호출 (내부에서 _suno_headers 사용)
            logger.info("생성 요청 중...")
            clip_ids = _request_suno_generation(concept, session)
            logger.info("✅ 성공! Clip IDs: %s", clip_ids)
            
    except Exception as e:
        logger.error("❌ 검증 실패: %s", e, exc_info=True)

if __name__ == "__main__":
    verify_gen()
