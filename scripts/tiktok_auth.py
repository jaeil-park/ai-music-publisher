"""
scripts/tiktok_auth.py
역할: TikTok OAuth 2.0 최초 액세스 토큰 발급 (1회성 설정 스크립트)

실행 방법:
    python scripts/tiktok_auth.py

사전 준비:
    1. TikTok for Developers (https://developers.tiktok.com) 앱 생성
    2. Products -> Content Posting API 활성화
    3. Login Kit -> Desktop 탭에 Redirect URI 등록: http://localhost:8080/callback
    4. .env에 TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET 설정

발급 후:
    .env에 TIKTOK_ACCESS_TOKEN, TIKTOK_REFRESH_TOKEN이 자동으로 저장됩니다.
"""

import os
import sys
import hashlib
import base64
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Windows console UTF-8 fix
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from dotenv import load_dotenv, set_key

# ---------------------------------------------------------------------------
# Config
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
# Local callback server
# ---------------------------------------------------------------------------
_received_code  = None
_received_state = None

class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _received_code, _received_state

        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        error  = params.get("error", [None])[0]

        if error:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"Auth denied: {error}".encode())
            return

        _received_code  = params.get("code",  [None])[0]
        _received_state = params.get("state", [None])[0]

        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>TikTok auth complete!</h2>"
            b"<p>Close this tab and return to the terminal.</p></body></html>"
        )

    def log_message(self, format, *args):
        pass


def _wait_for_callback() -> str:
    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    server.handle_request()
    if not _received_code:
        raise RuntimeError("No auth code received.")
    return _received_code

# ---------------------------------------------------------------------------
# PKCE  (RFC 7636 S256)
# ---------------------------------------------------------------------------
def _make_pkce_pair() -> tuple[str, str]:
    """
    TikTok-specific PKCE (non-standard):
      code_verifier : random string [A-Za-z0-9-._~], 64 chars
      code_challenge: SHA256(code_verifier).hexdigest()  <-- hex, NOT base64url
    Ref: https://developers.tiktok.com/doc/login-kit-desktop
    """
    alphabet      = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    code_verifier = "".join(secrets.choice(alphabet) for _ in range(64))
    code_challenge = hashlib.sha256(code_verifier.encode("ascii")).hexdigest()
    return code_verifier, code_challenge


def _build_auth_url(state: str, code_challenge: str) -> str:
    params = {
        "client_key":            CLIENT_KEY,
        "response_type":         "code",
        "scope":                 SCOPES,
        "redirect_uri":          REDIRECT_URI,
        "state":                 state,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)

# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------
def _exchange_code_for_token(code: str, code_verifier: str) -> dict:
    payload = {
        "client_key":    CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  REDIRECT_URI,
        "code_verifier": code_verifier,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    resp = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    data  = resp.json()
    error = data.get("error")
    if error:
        raise RuntimeError(f"Token exchange failed: {data.get('error_description', error)}")

    return data

# ---------------------------------------------------------------------------
# Save tokens to .env
# ---------------------------------------------------------------------------
def _save_tokens(token_data: dict):
    access_token    = token_data["access_token"]
    refresh_token   = token_data["refresh_token"]
    expires_in      = token_data.get("expires_in", 86400)
    refresh_expires = token_data.get("refresh_expires_in", 31536000)
    open_id         = token_data.get("open_id", "")

    set_key(str(ENV_PATH), "TIKTOK_ACCESS_TOKEN",  access_token)
    set_key(str(ENV_PATH), "TIKTOK_REFRESH_TOKEN", refresh_token)
    set_key(str(ENV_PATH), "TIKTOK_OPEN_ID",       open_id)

    print(f"\n[OK] Token issued!")
    print(f"     access_token  expires: {expires_in // 3600}h")
    print(f"     refresh_token expires: {refresh_expires // 86400}d")
    print(f"     open_id: {open_id}")
    print(f"\nSaved to: {ENV_PATH}")
    print("\nNext: update GitHub Actions ENV_FILE secret with the new tokens.")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not CLIENT_KEY or not CLIENT_SECRET:
        print("[ERROR] TIKTOK_CLIENT_KEY or TIKTOK_CLIENT_SECRET not set in .env")
        sys.exit(1)

    state                         = secrets.token_urlsafe(16)
    code_verifier, code_challenge = _make_pkce_pair()

    auth_url = _build_auth_url(state, code_challenge)

    print("=" * 60)
    print("TikTok OAuth 2.0 Token Issuance")
    print("=" * 60)
    print(f"\nOpening browser for auth...\n")
    print(f"  {auth_url}\n")
    print("Waiting for callback on http://localhost:8080 ...")

    webbrowser.open(auth_url)

    code = _wait_for_callback()

    if _received_state != state:
        raise RuntimeError("state mismatch — possible CSRF. Try again.")

    print("\nAuth code received. Exchanging for token...")
    token_data = _exchange_code_for_token(code, code_verifier)
    _save_tokens(token_data)


if __name__ == "__main__":
    main()
