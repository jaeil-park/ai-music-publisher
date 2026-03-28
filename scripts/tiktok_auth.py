"""
scripts/tiktok_auth.py
역할: TikTok OAuth 2.0 최초 액세스 토큰 발급 (1회성 설정 스크립트)

실행 방법:
    python scripts/tiktok_auth.py

사전 준비:
    1. TikTok for Developers (https://developers.tiktok.com) 앱 생성
    2. Products → Content Posting API 활성화
    3. App 설정에서 Redirect URI 등록: http://localhost:8080/callback
    4. .env에 TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET 설정

발급 후:
    .env에 TIKTOK_ACCESS_TOKEN, TIKTOK_REFRESH_TOKEN이 자동으로 저장됩니다.
    access_token은 24시간, refresh_token은 365일 유효합니다.
    이후 매일 실행 시 uploader_tiktok.py가 refresh_token으로 자동 갱신합니다.
"""

import os
import sys
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
BASE_DIR    = Path(__file__).parent.parent
ENV_PATH    = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_PATH)

CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")

REDIRECT_URI  = "http://localhost:8080/callback"
SCOPES        = "user.info.basic,video.publish,video.upload"
AUTH_URL      = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL     = "https://open.tiktokapis.com/v2/oauth/token/"

# ---------------------------------------------------------------------------
# 로컬 콜백 서버 (브라우저 리다이렉트 수신)
# ---------------------------------------------------------------------------
_received_code  = None
_received_state = None

class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _received_code, _received_state

        parsed   = urllib.parse.urlparse(self.path)
        params   = urllib.parse.parse_qs(parsed.query)
        error    = params.get("error", [None])[0]

        if error:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"인증 거부됨: {error}".encode())
            return

        _received_code  = params.get("code",  [None])[0]
        _received_state = params.get("state", [None])[0]

        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>TikTok 인증 완료!</h2>"
            b"<p>이 창을 닫고 터미널로 돌아가세요.</p></body></html>"
        )

    def log_message(self, format, *args):
        pass  # 콘솔 로그 억제


def _wait_for_callback() -> str:
    """로컬 HTTP 서버를 띄우고 콜백 code를 받을 때까지 대기."""
    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    server.handle_request()  # 요청 1개만 처리 후 종료
    if not _received_code:
        raise RuntimeError("인증 코드를 받지 못했습니다.")
    return _received_code

# ---------------------------------------------------------------------------
# Step 1: 인증 URL 생성 및 브라우저 오픈
# ---------------------------------------------------------------------------
def _build_auth_url(state: str) -> str:
    params = {
        "client_key":    CLIENT_KEY,
        "response_type": "code",
        "scope":         SCOPES,
        "redirect_uri":  REDIRECT_URI,
        "state":         state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)

# ---------------------------------------------------------------------------
# Step 2: code → access_token 교환
# ---------------------------------------------------------------------------
def _exchange_code_for_token(code: str) -> dict:
    """인증 코드를 액세스 토큰으로 교환."""
    payload = {
        "client_key":    CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    resp = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    data  = resp.json()
    error = data.get("error")
    if error:
        raise RuntimeError(f"토큰 교환 실패: {data.get('error_description', error)}")

    return data

# ---------------------------------------------------------------------------
# Step 3: .env에 토큰 저장
# ---------------------------------------------------------------------------
def _save_tokens(token_data: dict):
    """발급된 토큰을 .env에 저장."""
    access_token      = token_data["access_token"]
    refresh_token     = token_data["refresh_token"]
    expires_in        = token_data.get("expires_in", 86400)
    refresh_expires   = token_data.get("refresh_expires_in", 31536000)
    open_id           = token_data.get("open_id", "")

    set_key(str(ENV_PATH), "TIKTOK_ACCESS_TOKEN",  access_token)
    set_key(str(ENV_PATH), "TIKTOK_REFRESH_TOKEN", refresh_token)
    set_key(str(ENV_PATH), "TIKTOK_OPEN_ID",       open_id)

    print(f"\n✅ 토큰 발급 성공!")
    print(f"   access_token  유효기간: {expires_in // 3600}시간")
    print(f"   refresh_token 유효기간: {refresh_expires // 86400}일")
    print(f"   open_id: {open_id}")
    print(f"\n.env에 저장 완료: {ENV_PATH}")
    print("\n다음 단계:")
    print("  1. .env의 TIKTOK_ACCESS_TOKEN, TIKTOK_REFRESH_TOKEN을")
    print("     GitHub Actions의 ENV_FILE 시크릿에 반영하세요.")

# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main():
    if not CLIENT_KEY or not CLIENT_SECRET:
        print("[오류] .env에 TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET이 설정되지 않았습니다.")
        print("  TikTok for Developers 앱에서 Client Key와 Client Secret을 확인하세요.")
        sys.exit(1)

    state    = secrets.token_urlsafe(16)
    auth_url = _build_auth_url(state)

    print("=" * 60)
    print("TikTok OAuth 2.0 토큰 발급")
    print("=" * 60)
    print(f"\n브라우저에서 아래 URL로 인증을 진행해 주세요:")
    print(f"\n  {auth_url}\n")
    print("(자동으로 브라우저가 열리지 않으면 위 URL을 복사해서 직접 여세요)")
    print("\n로컬 콜백 서버 대기 중 (http://localhost:8080)...")

    webbrowser.open(auth_url)

    # 콜백 대기
    code = _wait_for_callback()

    # state 검증
    if _received_state != state:
        raise RuntimeError("state 값 불일치 — CSRF 가능성. 다시 시도해 주세요.")

    print(f"\n인증 코드 수신 완료. 토큰 교환 중...")
    token_data = _exchange_code_for_token(code)
    _save_tokens(token_data)


if __name__ == "__main__":
    main()
