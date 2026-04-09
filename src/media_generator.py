"""
generator.py
역할: OpenAI로 음악 컨셉 기획 → Suno API로 음원 생성 → /data에 mp3 다운로드
파이프라인 순서: brain.py -> [generator.py] -> video_maker.py -> uploader.py
"""

import os
import re
import sys
import json
import time
import uuid
import base64
import random
import logging
import datetime
from pathlib import Path
from functools import wraps
from threading import Thread
from curl_cffi import requests
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# 초기 설정
# ---------------------------------------------------------------------------
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수 및 환경변수
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

MAX_RETRIES   = 3
RETRY_DELAY   = 5    # 재시도 전 대기 시간 (초)
POLL_INTERVAL = 10   # 폴링 주기 (초)
POLL_TIMEOUT  = 300  # 최대 폴링 대기 시간 (초, 5분)
TOKEN_REFRESH_INTERVAL = 1800  # 토큰 갱신 주기 (30분, JWT 만료 1시간 전에 갱신)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
_COOKIE_STR    = os.getenv("SUNO_COOKIE")

SUNO_BASE_URL        = os.getenv("SUNO_BASE_URL", "https://studio-api-prod.suno.com/api")
CLERK_JS_VERSION     = "5.117.0"
CLERK_API_VERSION    = "2025-11-10"
REQUESTS_IMPERSONATE = "chrome131"


# ---------------------------------------------------------------------------
# Suno 인증 관리
# ---------------------------------------------------------------------------
_SUNO_HEADERS = {
    # curl_cffi impersonation이 UA·sec-ch-ua·accept-* 를 자동 설정하므로 생략.
    # 브라우저 캡처 기준 app-specific 헤더만 명시.
    "User-Agent":        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Origin":            "https://suno.com",
    "Referer":           "https://suno.com/",
    "Sec-Fetch-Site":    "same-site",
    "Sec-Fetch-Mode":    "cors",
    "Sec-Fetch-Dest":    "empty",
    "Accept":            "*/*",
    "Accept-Language":   "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


class SunoCookie:
    """
    브라우저 쿠키 문자열에서 Suno 인증에 필요한 값을 추출·관리.

    브라우저 Network 탭 분석 결과:
    - __session      : Suno API JWT 토큰 (Bearer로 직접 사용, aud="suno-api")
    - suno_device_id : device-id 요청 헤더값
    - clerk_active_context : Clerk session_id (토큰 갱신 시 필요)
    - __client       : Clerk 인증용 쿠키 (auth.suno.com 전용)
    """

    def __init__(self, cookie_str: str = ""):
        self._raw = cookie_str.strip()
        self._token: str | None = None

    def _get(self, name: str) -> str | None:
        for part in self._raw.split(";"):
            part = part.strip()
            if part.startswith(f"{name}="):
                return part[len(f"{name}="):]
        return None

    @property
    def device_id(self) -> str:
        """suno_device_id 또는 ajs_anonymous_id 쿠키 → device-id 헤더."""
        return self._get("suno_device_id") or self._get("ajs_anonymous_id") or "unknown-device"

    @property
    def session_id(self) -> str | None:
        """clerk_active_context 쿠키 → Clerk session ID."""
        raw = self._get("clerk_active_context")
        if raw:
            return raw.rstrip(":")  # 'session_xxx:' → 'session_xxx'
        return None

    @property
    def client_cookie(self) -> str | None:
        """auth.suno.com 전용 __client 쿠키값."""
        return self._get("__client")

    def _update_raw_cookie(self, name: str, value: str):
        """_raw 쿠키 문자열에서 특정 쿠키 값을 in-place 갱신."""
        parts = self._raw.split(";")
        new_parts, found = [], False
        for p in parts:
            if p.strip().startswith(f"{name}="):
                new_parts.append(f" {name}={value}" if new_parts else f"{name}={value}")
                found = True
            else:
                new_parts.append(p)
        if not found:
            new_parts.append(f" {name}={value}")
        self._raw = ";".join(new_parts)

    def get_cookie_string(self) -> str:
        """현재 갱신된 JWT 토큰이 반영된 최신 쿠키 문자열 반환."""
        if not self._token:
            return self._raw
        parts = self._raw.split(";")
        new_parts = []
        found = False
        for p in parts:
            if p.strip().startswith("__session="):
                new_parts.append(f" __session={self._token}" if new_parts else f"__session={self._token}")
                found = True
            else:
                new_parts.append(p)
        if not found:
            new_parts.append(f" __session={self._token}")
        return ";".join(new_parts)

    def load_initial_token(self):
        """
        항상 POST /touch를 통해 sid가 포함된 최신 JWT를 발급받습니다.

        쿠키의 __session을 직접 사용하지 않는 이유:
        Suno가 JWT 템플릿을 업데이트해 'sid'(세션 ID) 클레임이 필수가 됨.
        기존 쿠키의 __session은 구형 템플릿으로 발급돼 sid가 없어 422 반환.
        POST /touch만이 sid가 포함된 최신 형식 JWT를 반환함.
        """
        logger.info("POST /touch를 통해 최신 JWT(sid 포함)를 발급받습니다...")
        self.refresh_token()

    def refresh_token(self):
        """
        Clerk 토큰 갱신: POST /touch → sid가 포함된 최신 JWT 획득.

        흐름:
          1) GET /v1/client  → session_id 및 세션 상태 확인
          2) POST /touch/{session_id} → sid 포함 최신 JWT 발급 (브라우저와 동일한 방식)
          3) /touch 실패 시 POST /tokens 폴백
        """
        client = self.client_cookie
        if not client:
            raise ValueError("__client 쿠키가 없습니다. SUNO_COOKIE를 브라우저에서 갱신하세요.")

        auth_headers = {**_SUNO_HEADERS, "Cookie": f"__client={client}"}
        qs           = f"__clerk_api_version={CLERK_API_VERSION}&_clerk_js_version={CLERK_JS_VERSION}"

        # Step 1: session_id 및 세션 상태 확인
        r1 = requests.get(
            f"https://auth.suno.com/v1/client?{qs}",
            headers=auth_headers,
            impersonate=REQUESTS_IMPERSONATE,
            timeout=15,
        )
        if not r1.ok:
            logger.error("Clerk GET /v1/client 실패 %d: %s", r1.status_code, r1.text[:300])
            r1.raise_for_status()

        # Clerk가 Set-Cookie로 새 __client를 발급하면 즉시 갱신 후 auth_headers도 업데이트
        m = re.search(r'__client=([^;,\s]+)', r1.headers.get("set-cookie", ""))
        if m:
            self._update_raw_cookie("__client", m.group(1))
            auth_headers = {**_SUNO_HEADERS, "Cookie": f"__client={self.client_cookie}"}
            logger.info("__client 쿠키 자동 갱신됨 (Clerk Set-Cookie).")

        data     = r1.json()
        sessions = (
            data.get("client", {}).get("sessions") or
            data.get("response", {}).get("sessions") or
            []
        )
        if not sessions:
            raise ValueError(
                f"활성 Clerk 세션 없음. Suno 로그인 상태인지 확인하세요. 응답: {str(data)[:200]}"
            )

        session_status = sessions[0].get("status", "unknown")
        session_id     = sessions[0]["id"]
        logger.debug("활성 세션 ID: %s | 상태: %s", session_id, session_status)

        if session_status != "active":
            raise ValueError(
                f"Clerk 세션이 만료되었습니다 (status={session_status}). "
                "브라우저에서 suno.com에 재로그인 후 SUNO_COOKIE를 .env에 새로 붙여넣으세요."
            )

        # Step 2: POST /touch → last_active_token.jwt 획득 (브라우저와 동일한 방식)
        token = None
        r2 = requests.post(
            f"https://auth.suno.com/v1/client/sessions/{session_id}/touch?{qs}",
            headers={**auth_headers, "Content-Length": "0"},
            impersonate=REQUESTS_IMPERSONATE,
            timeout=15,
        )
        if r2.ok:
            td = r2.json()
            # response.last_active_token.jwt 우선, 없으면 client.sessions[0].last_active_token.jwt
            session_obj = td.get("response") or td.get("client", {}).get("sessions", [{}])[0]
            if isinstance(session_obj, dict):
                token = session_obj.get("last_active_token", {}).get("jwt")
            logger.debug("POST /touch 응답 %d | token 획득: %s", r2.status_code, bool(token))
        else:
            logger.warning("POST /touch 실패 %d: %s", r2.status_code, r2.text[:200])

        # Step 3: /touch 실패 시 /tokens 폴백
        if not token:
            r3 = requests.post(
                f"https://auth.suno.com/v1/client/sessions/{session_id}/tokens?{qs}",
                headers={**auth_headers, "Content-Length": "0"},
                impersonate=REQUESTS_IMPERSONATE,
                timeout=15,
            )
            logger.info("POST /tokens 응답 %d: %s", r3.status_code, r3.text[:300])
            if r3.ok:
                token = r3.json().get("jwt")

        if not token:
            raise ValueError(f"JWT 발급 실패. /touch: {r2.status_code}")

        self._token = token

        # JWT 디코딩 → kid·aud·만료 검증 (브라우저 캡처 확인: sid 클레임 없는 것이 정상)
        try:
            def _decode_b64(s: str) -> dict:
                s = s + "=" * ((4 - len(s) % 4) % 4)
                return json.loads(base64.urlsafe_b64decode(s).decode())

            parts   = token.split(".")
            header  = _decode_b64(parts[0])
            payload = _decode_b64(parts[1])
            exp     = payload.get("exp", 0)
            aud     = payload.get("aud", "?")
            kid     = header.get("kid", "?")
            exp_str = datetime.datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S") if exp else "?"
            logger.info("JWT 갱신 완료 | aud=%s | kid=%s | 만료=%s", aud, kid, exp_str)
            logger.info("JWT 클레임 전체: %s", json.dumps(payload, ensure_ascii=False))
            if aud != "suno-api":
                logger.warning("⚠️ JWT aud 클레임이 'suno-api'가 아닙니다 (현재: %s).", aud)
            if kid != "suno-api-rs256-key-1":
                logger.warning("⚠️  예상치 못한 JWT kid=%s.", kid)
            if exp and exp < time.time():
                logger.warning("⚠️  발급된 JWT가 이미 만료됨. SUNO_COOKIE를 갱신하세요.")
        except Exception as e:
            logger.info("JWT 토큰 갱신 완료 (payload 파싱 불가: %s).", e)

    def get_token(self) -> str | None:
        return self._token


def save_updated_cookie_to_env():
    """
    갱신된 SUNO_COOKIE를 .env 파일에 반영.
    GitHub Actions 워크플로우 종료 직전에 호출하면
    'gh secret set ENV_FILE < .env' 로 시크릿을 자동 업데이트할 수 있음.
    """
    env_path   = Path(__file__).parent.parent / ".env"
    new_cookie = suno_auth.get_cookie_string()
    if not new_cookie:
        return

    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        if re.search(r"^SUNO_COOKIE=", text, re.MULTILINE):
            text = re.sub(r"^SUNO_COOKIE=.*$", f"SUNO_COOKIE={new_cookie}", text, flags=re.MULTILINE)
        else:
            text += f"\nSUNO_COOKIE={new_cookie}\n"
        env_path.write_text(text, encoding="utf-8")
        logger.info("갱신된 SUNO_COOKIE를 .env에 저장했습니다.")
    else:
        logger.warning(".env 파일을 찾을 수 없어 쿠키를 저장하지 못했습니다.")


def _make_browser_token() -> str:
    """
    Suno 브라우저가 매 요청마다 생성하는 browser-token 헤더값.
    형식: {"token": base64({"timestamp": <epoch_ms>})}
    """
    payload = json.dumps({"timestamp": int(time.time() * 1000)}, separators=(",", ":"))
    encoded = base64.b64encode(payload.encode()).decode()
    return json.dumps({"token": encoded}, separators=(",", ":"))


def _keep_alive(suno_cookie: SunoCookie):
    """백그라운드 스레드: TOKEN_REFRESH_INTERVAL 마다 토큰 갱신."""
    while True:
        time.sleep(TOKEN_REFRESH_INTERVAL)
        try:
            suno_cookie.refresh_token()
        except Exception as e:
            logger.error("백그라운드 토큰 갱신 실패: %s", e)


def _start_keep_alive(suno_cookie: SunoCookie):
    t = Thread(target=_keep_alive, args=(suno_cookie,), daemon=True)
    t.start()
    logger.info("토큰 갱신 스레드 시작 (주기: %d초).", TOKEN_REFRESH_INTERVAL)


# 인증 객체 초기화
suno_auth = SunoCookie(_COOKIE_STR or "")


# ---------------------------------------------------------------------------
# 공통 재시도 데코레이터
# ---------------------------------------------------------------------------
def with_retry(max_retries: int = MAX_RETRIES, delay: int = RETRY_DELAY):
    """
    외부 API 호출 함수에 재시도 로직을 부여하는 데코레이터.
    토큰 인증(422) 실패 시 즉시 토큰 갱신 후 재시도하는 로직 포함.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    # 401/422 인증 오류 → 즉시 토큰 갱신 후 재시도
                    if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code in (401, 422) and attempt < max_retries:
                        try:
                            logger.warning(
                                "[%s] 인증 오류(%d). 토큰 갱신 후 재시도합니다. (시도 %d/%d)",
                                func.__name__, e.response.status_code, attempt, max_retries
                            )
                            suno_auth.refresh_token()
                        except Exception as refresh_e:
                            logger.critical("토큰 갱신 중 치명적 오류 발생: %s", refresh_e)
                            raise refresh_e
                        time.sleep(15)
                        continue

                    # 그 외 모든 예외 또는 마지막 시도
                    logger.error("[%s] 시도 %d/%d 실패: %s", func.__name__, attempt, max_retries, e)
                    if attempt < max_retries:
                        logger.info("%d초 후 재시도합니다...", delay)
                        time.sleep(delay)
                    else:
                        logger.critical("[%s] 최대 재시도 횟수(%d회) 초과.", func.__name__, max_retries)
                        raise
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# 1. generate_and_download_audio  (공개 진입점)
# ---------------------------------------------------------------------------
def generate_and_download_audio(concept: dict) -> Path:
    """
    Suno API로 음원을 생성하고 /data 폴더에 mp3로 다운로드.

    내부 흐름:
      1. __session 쿠키에서 JWT 로드 (Clerk API 호출 불필요)
      2. _request_suno_generation  → clip_id 획득
      3. _poll_until_complete      → 생성 완료 대기 후 audio_url 획득
      4. _download_mp3             → mp3 파일 저장
    """
    logger.info("=== Suno 음원 생성 파이프라인 시작 ===")
    logger.info("device_id: %s | session_id: %s", suno_auth.device_id, suno_auth.session_id)

    try:
        suno_auth.load_initial_token()
    except Exception as e:
        logger.critical("초기 토큰 로드 실패: %s", e, exc_info=True)
        sys.exit(1)

    _start_keep_alive(suno_auth)

    with requests.Session(impersonate=REQUESTS_IMPERSONATE) as session:
        session.headers.update(_SUNO_HEADERS)

        # 브라우저는 generate 전에 /api/c/check를 호출해 session-id를 받아 사용.
        _pre_check(session)

        clip_ids  = _request_suno_generation(concept, session)
        audio_url, lyrics = _poll_until_complete(clip_ids[0], session)
        concept["lyrics"] = lyrics
        file_path = _download_mp3(audio_url, session)
        
    logger.info("=== 음원 파이프라인 완료: %s ===", file_path)
    return file_path


# ---------------------------------------------------------------------------
# 내부 헬퍼 함수들
# ---------------------------------------------------------------------------
def _suno_headers() -> dict:
    """매 요청마다 fresh browser-token을 포함한 인증 헤더 반환.

    브라우저 네트워크 캡처(2026-03-29) 결과:
    - studio-api-prod.suno.com으로의 요청에 Cookie 헤더 없음.
      (브라우저는 credentials: omit으로 CORS 요청 → 쿠키 미전송)
    - Authorization: Bearer + browser-token + device-id 만 전송.
    """
    token = suno_auth.get_token()
    return {
        "Authorization": f"Bearer {token}",
        "browser-token": _make_browser_token(),
        "device-id":     suno_auth.device_id,
    }


def _pre_check(session: requests.Session) -> None:
    """
    브라우저가 generate 전에 항상 호출하는 /api/c/check 엔드포인트.
    응답 헤더의 session-id를 이후 모든 API 요청에 포함시켜야 함.
    (access-control-expose-headers: session-id → JS가 읽어 사용)
    """
    try:
        resp = session.post(
            f"{SUNO_BASE_URL}/c/check",
            json={},
            headers=_suno_headers(),
            timeout=10,
            impersonate=REQUESTS_IMPERSONATE,
        )
        sid = resp.headers.get("session-id", "")
        if sid:
            session.headers["session-id"] = sid
            logger.info("사전 체크 완료 | session-id: %s", sid[:16] + "...")
        else:
            logger.debug("사전 체크 완료 (session-id 없음). 응답: %s", resp.text[:100])
    except Exception as e:
        logger.warning("사전 체크(/api/c/check) 실패: %s (무시하고 계속)", e)


@with_retry()
def _request_suno_generation(concept: dict, session: requests.Session) -> list[str]:
    """Suno API에 음악 생성 요청 후 clip_id 목록 반환."""
    # Generate 호출 직전에 항상 session-id 갱신 시도 (재시도 시 갱신된 토큰으로 재발급)
    _pre_check(session)

    prompt_lyrics = concept.get("lyrics", "")
    if isinstance(prompt_lyrics, dict):
        prompt_lyrics = "\n".join(f"[{k}]\n{v}" for k, v in prompt_lyrics.items())
    elif isinstance(prompt_lyrics, list):
        prompt_lyrics = "\n".join(str(item) for item in prompt_lyrics)
    prompt_lyrics = str(prompt_lyrics)

    logger.info("Suno 생성 요청 → 장르: [%s] | 가사: '%s...'", concept.get("genre"), prompt_lyrics[:30].replace("\n", " "))

    # 브라우저 Network 탭에서 확인한 실제 payload 구조 (v2-web 기준)
    # - mv: "chirp-crow" (실제 모델명, v4.5-all에 해당)
    # - generation_type: "TEXT" (프롬프트 기반 생성)
    # - transaction_uuid: 요청마다 새 UUID
    # - v2-web 엔드포인트는 Cloudflare Turnstile token 필요 → v2/ 사용
    payload = {
        "generation_type":   "TEXT",
        "prompt":            prompt_lyrics,
        "tags":              concept.get("audio_prompt", ""), 
        "title":             concept.get("title", ""),
        "make_instrumental": False,  # 보컬 및 가사가 생성되도록 False로 변경
        "mv":                "chirp-crow",
        "negative_tags":     "",
        "transaction_uuid":  str(uuid.uuid4()),
    }

    response = session.post(
        f"{SUNO_BASE_URL}/generate/v2/",
        json=payload,
        headers=_suno_headers(),
        timeout=30,
        impersonate=REQUESTS_IMPERSONATE,
    )

    logger.info("Suno 응답 %d | %s", response.status_code, response.text[:300])

    if response.status_code == 402 or any(
        kw in response.text.lower() for kw in ("credit", "insufficient")
    ):
        logger.critical("Suno 크레딧 부족! 요금제를 충전하세요.")
        sys.exit(1)

    response.raise_for_status()

    data     = response.json()
    clip_ids = [clip["id"] for clip in data.get("clips", [])]
    if not clip_ids:
        raise ValueError("clip ID 없음. 응답: " + str(data))

    logger.info("생성 요청 완료 → Clip IDs: %s", clip_ids)
    return clip_ids


@with_retry()
def _poll_until_complete(clip_id: str, session: requests.Session) -> tuple[str, str]:
    """clip_id 상태를 폴링해 완료 시 audio_url 반환."""
    logger.info("음원 생성 대기 중... (clip_id: %s)", clip_id)
    elapsed = 0

    while elapsed < POLL_TIMEOUT:
        response = session.get(
            f"{SUNO_BASE_URL}/feed/",
            params={"ids": clip_id},
            headers=_suno_headers(),
            timeout=30,
            impersonate=REQUESTS_IMPERSONATE,
        )
        response.raise_for_status()

        clips  = response.json()
        if not clips:
            raise ValueError(f"clip_id '{clip_id}' 데이터 없음.")

        status = clips[0].get("status", "unknown")
        logger.info("  폴링 상태: %-12s | 경과: %d초", status, elapsed)

        if status == "complete":
            audio_url = clips[0].get("audio_url")
            if not audio_url:
                raise ValueError("status=complete이지만 audio_url이 없습니다.")
            logger.info("음원 생성 완료! CDN 파일 동기화를 위해 15초 대기합니다...")
            time.sleep(15)
            
            # [수정 확인용 주석] Suno 가사 추출 (정확한 들여쓰기 유지)
            lyrics = (clips[0].get("metadata") or {}).get("prompt", "")
            return audio_url, lyrics

        if status in ("error", "failed"):
            raise RuntimeError(f"Suno 생성 실패: {status}")

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    raise TimeoutError(f"생성 시간 초과 ({POLL_TIMEOUT}초, clip_id: {clip_id})")


@with_retry()
def _download_mp3(audio_url: str, session: requests.Session) -> Path:
    """audio_url에서 mp3를 /data/{날짜}_{시간}_audio.mp3로 다운로드."""
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    file_path = DATA_DIR / f"{timestamp}_audio.mp3"
    logger.info("mp3 다운로드 → %s", file_path)

    response = session.get(audio_url, stream=True, timeout=60, impersonate=REQUESTS_IMPERSONATE)
    response.raise_for_status()

    with open(file_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    logger.info("다운로드 완료: %s (%.1f KB)", file_path.name, file_path.stat().st_size / 1024)
    return file_path



