"""
scripts/auto_suno_cookie.py
역할: undetected-chromedriver를 사용해 Suno의 Cloudflare 봇 검증을 우회하고,
      가장 신선한 쿠키를 추출하여 .env 파일의 SUNO_COOKIE를 자동 갱신합니다.
"""

import os
import time
import logging
from pathlib import Path
import undetected_chromedriver as uc
from dotenv import load_dotenv, set_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
ENV_PATH = BASE_DIR / ".env"

def fetch_and_save_cookie():
    logger.info("Chrome 브라우저를 시작합니다 (Cloudflare 우회용)...")
    
    options = uc.ChromeOptions()
    # Cloudflare는 완전한 headless 모드를 차단하는 경우가 많아,
    # 화면은 숨기되 백그라운드에서 렌더링되도록 처리하거나 headless=new를 사용합니다.
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    driver = uc.Chrome(options=options)
    
    try:
        logger.info("https://suno.com 접속 중...")
        driver.get("https://suno.com")
        
        # Cloudflare Turnstile 통과 및 페이지 로딩 대기 (10~15초)
        logger.info("봇 검증 통과를 대기합니다 (15초)...")
        time.sleep(15)
        
        cookies = driver.get_cookies()
        cookie_parts = []
        for c in cookies:
            cookie_parts.append(f"{c['name']}={c['value']}")
            
        new_cookie_str = "; ".join(cookie_parts)
        
        if "__client" in new_cookie_str:
            set_key(str(ENV_PATH), "SUNO_COOKIE", new_cookie_str)
            logger.info("✅ SUNO_COOKIE 자동 추출 및 .env 갱신 성공!")
        else:
            logger.error("❌ 쿠키 추출 실패: __client 쿠키를 찾을 수 없습니다. (봇 검증에 막혔을 수 있습니다)")
            
    finally:
        driver.quit()

if __name__ == "__main__":
    load_dotenv(dotenv_path=ENV_PATH)
    fetch_and_save_cookie()