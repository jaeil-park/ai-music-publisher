"""
generator.py
역할: OpenAI로 음악 컨셉 기획 → Suno API로 음원 생성 → /data에 mp3 다운로드
파이프라인 순서: brain.py -> [generator.py] -> video_maker.py -> uploader.py
"""

import os
import sys
import json
import time
import uuid
import base64
import logging
import datetime
from pathlib import Path
from functools import wraps
from threading import Thread
from curl_cffi import requests
from dotenv import load_dotenv
from openai import OpenAI

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
REQUESTS_IMPERSONATE = "chrome110"

# ---------------------------------------------------------------------------
# Suno 인증 관리
# ---------------------------------------------------------------------------
_SUNO_HEADERS = {
    "User-Agent":     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin":         "https://suno.com",
    "Referer":        "https://suno.com/",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
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

    def load_initial_token(self):
        """
        초기 JWT 토큰 로드.
        1순위: __session 쿠키 (httpOnly라 Network 탭에서만 보임)
        2순위: __client 쿠키로 Clerk GET /v1/client 호출
        """
        token = self._get("__session")
        if token:
            self._token = token
            logger.info("초기 JWT 토큰 로드 완료 (__session 쿠키).")
        else:
            logger.info("__session 쿠키 없음 → Clerk API로 토큰 발급.")
            self.refresh_token()

    def refresh_token(self):
        """
        Clerk GET /v1/client → last_active_token.jwt 로 토큰 갱신.

        Network 탭 분석으로 확인:
        - last_active_token.jwt 는 kid="suno-api-rs256-key-1", aud="suno-api" 포함
        - __client 쿠키만 auth.suno.com에 전송 (도메인 격리)
        - __session 쿠키는 약 1시간마다 만료되므로 갱신 필요
        """
        client = self.client_cookie
        if not client:
            raise ValueError("__client 쿠키가 없습니다. SUNO_COOKIE를 브라우저에서 갱신하세요.")

        url  = f"https://auth.suno.com/v1/client?_clerk_js_version={CLERK_JS_VERSION}"
        resp = requests.get(
            url=url,
            headers={**_SUNO_HEADERS, "Cookie": f"__client={client}"},
            impersonate=REQUESTS_IMPERSONATE,
            timeout=15,
        )
        if not resp.ok:
            logger.error("Clerk 토큰 갱신 실패 %d: %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()

        data     = resp.json()
        sessions = data.get("response", data).get("sessions", [])
        if not sessions:
            raise ValueError("Clerk 응답에 sessions 없음. 쿠키 만료 의심.")

        token = sessions[0].get("last_active_token", {}).get("jwt")
        if not token:
            raise ValueError("last_active_token.jwt 없음.")
        self._token = token
        logger.info("JWT 토큰 갱신 완료 (last_active_token).")

    def get_token(self) -> str | None:
        return self._token


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
    """외부 API 호출 함수에 최대 max_retries회 재시도 로직을 부여하는 데코레이터."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
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
# 1. generate_daily_concept
# ---------------------------------------------------------------------------
@with_retry()
def generate_daily_concept() -> dict:
    """
    OpenAI를 사용해 오늘 날짜 기반으로 매일 다른 음악 장르와 프롬프트를 기획.

    Returns:
        dict: {
            "genre": str,        # 장르명 (영문)
            "mood": str,         # 분위기 키워드 (한글)
            "suno_prompt": str,  # Suno에 전달할 프롬프트 (영문)
            "title": str,        # 유튜브 쇼츠 제목 (한글)
            "description": str   # 유튜브 설명란 소개 (한글)
        }
    """
    logger.info("OpenAI로 오늘의 음악 컨셉 기획 시작...")

    today      = datetime.date.today()
    weekday_kr = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"][today.weekday()]
    month      = today.month
    season     = (
        "봄" if 3 <= month <= 5 else
        "여름" if 6 <= month <= 8 else
        "가을" if 9 <= month <= 11 else
        "겨울"
    )

    system_prompt = (
        "당신은 음악 큐레이터입니다. 매일 다른 분위기의 음악 컨셉을 기획합니다.\n"
        "반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.\n"
        "{\n"
        '  "genre": "장르명 (영문)",\n'
        '  "mood": "분위기 키워드 (한글, 3단어 이내)",\n'
        '  "suno_prompt": "Suno AI 음악 생성 프롬프트 (영문, 60자 이내)",\n'
        '  "title": "유튜브 쇼츠용 제목 (한글, 20자 이내)",\n'
        '  "description": "유튜브 설명란 소개 (한글, 60자 이내)"\n'
        "}"
    )
    user_prompt = (
        f"오늘 날짜: {today.isoformat()} ({weekday_kr}, {season})\n\n"
        "이 날짜·요일·계절을 seed로 활용해 오늘에 어울리는 독특한 음악 컨셉 하나를 기획해 주세요.\n"
        "월요일→에너제틱, 금요일→파티, 주말→여유, 봄→청량, 여름→신남, 가을→감성, 겨울→포근한 분위기를 참고하세요."
    )

    client   = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.85,
    )

    concept = json.loads(response.choices[0].message.content)
    logger.info("컨셉 기획 완료 → 장르: [%s] | 분위기: %s", concept.get("genre"), concept.get("mood"))
    return concept


# ---------------------------------------------------------------------------
# 2. generate_and_download_audio  (공개 진입점)
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

    session = requests.Session(impersonate=REQUESTS_IMPERSONATE)
    session.headers.update(_SUNO_HEADERS)

    clip_ids  = _request_suno_generation(concept, session)
    audio_url = _poll_until_complete(clip_ids[0], session)
    file_path = _download_mp3(audio_url, session)
    logger.info("=== 음원 파이프라인 완료: %s ===", file_path)
    return file_path


# ---------------------------------------------------------------------------
# 내부 헬퍼 함수들
# ---------------------------------------------------------------------------
def _suno_headers() -> dict:
    """매 요청마다 fresh browser-token을 포함한 인증 헤더 반환."""
    return {
        "Authorization": f"Bearer {suno_auth.get_token()}",
        "browser-token": _make_browser_token(),
        "device-id":     suno_auth.device_id,
    }


@with_retry()
def _request_suno_generation(concept: dict, session: requests.Session) -> list[str]:
    """Suno API에 음악 생성 요청 후 clip_id 목록 반환."""
    logger.info("Suno 생성 요청 → '%s'", concept.get("suno_prompt"))

    # 브라우저 Network 탭에서 확인한 실제 payload 구조 (v2-web 기준)
    # - mv: "chirp-crow" (실제 모델명, v4.5-all에 해당)
    # - generation_type: "TEXT" (프롬프트 기반 생성)
    # - transaction_uuid: 요청마다 새 UUID
    # - v2-web 엔드포인트는 Cloudflare Turnstile token 필요 → v2/ 사용
    payload = {
        "generation_type":   "TEXT",
        "prompt":            concept["suno_prompt"],
        "tags":              concept.get("genre", ""),
        "title":             concept.get("title", ""),
        "make_instrumental": True,
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
def _poll_until_complete(clip_id: str, session: requests.Session) -> str:
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
            logger.info("음원 생성 완료!")
            return audio_url

        if status in ("error", "failed"):
            raise RuntimeError(f"Suno 생성 실패: {status}")

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    raise TimeoutError(f"생성 시간 초과 ({POLL_TIMEOUT}초, clip_id: {clip_id})")


@with_retry()
def _download_mp3(audio_url: str, session: requests.Session) -> Path:
    """audio_url에서 mp3를 /data/{날짜}_audio.mp3로 다운로드."""
    file_path = DATA_DIR / f"{datetime.date.today().strftime('%Y%m%d')}_audio.mp3"
    logger.info("mp3 다운로드 → %s", file_path)

    response = session.get(audio_url, stream=True, timeout=60, impersonate=REQUESTS_IMPERSONATE)
    response.raise_for_status()

    with open(file_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    logger.info("다운로드 완료: %s (%.1f KB)", file_path.name, file_path.stat().st_size / 1024)
    return file_path


# ---------------------------------------------------------------------------
# 독립 실행 (단독 테스트용)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    concept = generate_daily_concept()
    print("\n[오늘의 컨셉]")
    print(json.dumps(concept, ensure_ascii=False, indent=2))

    mp3_path = generate_and_download_audio(concept)
    print(f"\n[완료] 음원 저장 경로: {mp3_path}")
