import os
import logging
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (ModuleNotFoundError 방지)
import sys
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from dotenv import load_dotenv
from curl_cffi import requests
import argparse
from src.media_generator import _request_suno_generation, suno_auth, _pre_check, _send_heartbeat

# 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def verify_gen(captcha_token: str | None = None):
    print("\n" + "="*50)
    print(" [Suno 음원 생성 API 검증 시작]")
    print("="*50)
    
    # .env 로드
    load_dotenv()
    
    try:
        # 1. 초기 토큰 로드
        print("\n1. Clerk 세션 및 JWT 토큰 초기화 중...")
        suno_auth.load_initial_token()
        print(f"   - 세션 ID: {suno_auth.session_id}")
        print(f"   - 디바이스 ID: {suno_auth.device_id}")
        print("   ✅ JWT 및 쿠키 동기화 완료")
        
        with requests.Session(impersonate="chrome131") as session:
            # 2. 아주 간단한 컨셉으로 생성 요청
            concept = {
                "title": "Test Song",
                "genre": "jazz",
                "audio_prompt": "jazz piano",
                "lyrics": "[Verse 1]\nHello Suno, please work."
            }

            # 3. 사전 체크 및 Heartbeat 전송
            print("\n2. Suno 서버 사전 체크 및 Heartbeat 전송 중...")
            sid = _pre_check(session)
            if sid:
                session.headers["session-id"] = sid
                print(f"   - 발급된 session-id: {sid[:16]}...")
            else:
                print("   ⚠️ session-id가 발급되지 않았습니다. (무시하고 진행)")

            # 인자로 받은 토큰이 있다면 Heartbeat 전송
            if captcha_token:
                print(f"   - CLI로 전달받은 토큰으로 Heartbeat 시도... ({captcha_token[:10]}...)")
                _send_heartbeat(session, captcha_token)
            else:
                print("   - 자동 2Captcha 풀이 또는 .env 설정으로 Heartbeat 시도...")
                _send_heartbeat(session)

            # 4. 생성 요청
            print("\n3. Suno 음원 생성 요청 전송 중...")
            clip_ids = _request_suno_generation(concept, session, sid)
            print(f"   ✅ 생성 성공! Clip IDs: {clip_ids}")
            print("\n" + "="*50)
            print(" [검증 결과: 성공]")
            print("="*50 + "\n")
            
    except Exception as e:
        print("\n" + "!"*50)
        print(f" ❌ 검증 실패: {str(e)}")
        print("!"*50 + "\n")
        logger.error("상세 예외 정보:", exc_info=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", help="Suno Heartbeat용 CAPTCHA 토큰 (1.wNA_...)")
    args = parser.parse_args()
    
    verify_gen(args.token)
