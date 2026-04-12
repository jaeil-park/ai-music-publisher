import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from src.media_generator import suno_auth, save_updated_cookie_to_env

# 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def test_auth():
    logger.info("Suno 인증 테스트 시작...")
    
    # .env 로드
    load_dotenv()
    
    try:
        # 1. 초기 토큰 로드 (refresh_token 호출 포함)
        logger.info("1. 초기 토큰 로드 시도...")
        suno_auth.load_initial_token()
        
        token = suno_auth.get_token()
        if token:
            logger.info("✅ 토큰 획득 성공! Token (첫 20자): %s...", token[:20])
        else:
            logger.error("❌ 토큰 획득 실패.")
            return

        # 2. 쿠키 저장 테스트
        logger.info("2. .env 파일 저장 테스트...")
        save_updated_cookie_to_env()
        
        # 3. .env 파일 확인
        env_content = Path(".env").read_text(encoding="utf-8")
        if "__session=" in env_content:
            logger.info("✅ .env 파일에 __session이 성공적으로 업데이트되었습니다.")
        else:
            logger.warning("⚠️ .env 파일에 __session이 보이지 않습니다 (기존에 없었거나 업데이트 실패).")

    except Exception as e:
        logger.error("❌ 테스트 중 오류 발생: %s", e, exc_info=True)

if __name__ == "__main__":
    test_auth()
