import os
import sys
import logging
import json
import time
from pathlib import Path
from dotenv import load_dotenv

# 현재 디렉토리를 path에 추가하여 src 모듈 임포트 가능하게 함
sys.path.append(str(Path(__file__).parent))

from src.media_generator import suno_auth, _request_suno_generation, requests, REQUESTS_IMPERSONATE, _pre_check

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_fix")

def test_generation():
    logger.info("Suno 테스트 시작...")
    
    # 1. 초기 토큰 로드
    try:
        suno_auth.load_initial_token()
    except Exception as e:
        logger.error("토큰 로드 실패: %s", e)
        return

    # 2. 세션 생성 및 사전 체크
    with requests.Session(impersonate=REQUESTS_IMPERSONATE) as session:
        sid = _pre_check(session)
        
        # 3. 생성 요청 테스트
        concept = {
            "title": "Test Song",
            "genre": "pop",
            "lyrics": "[Verse]\nHello from the fix script",
            "audio_prompt": "catchy pop"
        }
        
        try:
            logger.info("생성 요청 시도 중...")
            clip_ids = _request_suno_generation(concept, session, sid)
            logger.info("✅ 성공! Clip IDs: %s", clip_ids)
        except Exception as e:
            logger.error("❌ 실패: %s", e)

if __name__ == "__main__":
    test_generation()
