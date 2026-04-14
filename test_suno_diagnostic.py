import os
import json
import time
import logging
from pathlib import Path
from dotenv import load_dotenv
from curl_cffi import requests as curl_requests
import requests as std_requests

# src 폴더를 path에 추가
import sys
sys.path.append(str(Path(__file__).parent))

from src.media_generator import SunoCookie, _suno_headers, _pre_check, REQUESTS_IMPERSONATE

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s - %(message)s')
logger = logging.getLogger("diagnostic")

def diagnostic_test():
    load_dotenv()
    cookie_str = os.getenv("SUNO_COOKIE")
    if not cookie_str:
        logger.error("SUNO_COOKIE not found in .env")
        return

    suno_cookie = SunoCookie(cookie_str)
    
    with curl_requests.Session() as session:
        # 1. JWT 갱신
        logger.info("Step 1: Refreshing JWT...")
        suno_cookie.refresh_token()
        
        # 2. Pre-check
        logger.info("Step 2: Pre-check...")
        session_id = _pre_check(session)
        logger.info(f"Session-id: {session_id}")

        # 3. Variants 정의
        gen_url = "https://studio-api-prod.suno.com/api/generate/v2/"
        payload = {
            "gpt_description_prompt": "A simple happy song about morning coffee",
            "prompt": "",
            "mv": "chirp-v3-5",
            "title": "",
            "tags": "pop",
            "make_instrumental": False,
            "continue_clip_id": None,
            "continue_at": None
        }

        base_headers = _suno_headers(session_id)
        
        variants = [
            ("curl_cffi (Chrome 124)", "curl", base_headers),
            ("Standard Requests (Minimal TLS)", "std", base_headers),
            ("No session-id headers (curl)", "curl", {k: v for k, v in base_headers.items() if k not in ["session-id", "x-session-id"]}),
        ]

        for name, mode, headers in variants:
            logger.info(f"\n--- Testing Variant: {name} ---")
            try:
                if mode == "curl":
                    response = session.post(gen_url, headers=headers, json=payload, impersonate=REQUESTS_IMPERSONATE, timeout=15)
                else:
                    response = std_requests.post(gen_url, headers=headers, json=payload, timeout=15)
                
                logger.info(f"Result Status: {response.status_code}")
                if response.status_code == 200:
                    logger.info("✅ SUCCESS! This variant works.")
                    print(f"\n[FOUND] Working variant: {name}")
                    return
                else:
                    logger.warning(f"Failed: {response.text}")
            except Exception as e:
                logger.error(f"Error testing {name}: {e}")
            
            time.sleep(3)

if __name__ == "__main__":
    diagnostic_test()
