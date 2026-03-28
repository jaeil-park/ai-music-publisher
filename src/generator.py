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
        __client 쿠키로 Clerk 2-step 인증을 거쳐 fresh JWT를 발급받습니다.
        GET last_active_token 재사용이 아닌 POST로 새 토큰을 발급받아 422 방지.
        """
        logger.info("Clerk API를 통해 최신 JWT 토큰을 발급받습니다...")
        self.refresh_token()

    def refresh_token(self):
        """
        Clerk 2-step 토큰 갱신:
          1) GET /v1/client  → 활성 session_id 획득
          2) POST /v1/client/sessions/{id}/tokens → 매번 새 JWT 발급

        GET의 last_active_token은 이전에 발급된 토큰을 재사용하므로
        Suno 서버가 "Token validation failed"(422)로 거부하는 경우가 있음.
        POST 엔드포인트는 호출 시점에 새 JWT를 발급하므로 이 문제를 해결.
        """
        client = self.client_cookie
        if not client:
            raise ValueError("__client 쿠키가 없습니다. SUNO_COOKIE를 브라우저에서 갱신하세요.")

        auth_headers = {**_SUNO_HEADERS, "Cookie": f"__client={client}"}
        qs           = f"_clerk_js_version={CLERK_JS_VERSION}"

        # Step 1: 활성 session_id 획득
        r1 = requests.get(
            f"https://auth.suno.com/v1/client?{qs}",
            headers=auth_headers,
            impersonate=REQUESTS_IMPERSONATE,
            timeout=15,
        )
        if not r1.ok:
            logger.error("Clerk GET /v1/client 실패 %d: %s", r1.status_code, r1.text[:300])
            r1.raise_for_status()

        # Clerk가 Set-Cookie로 새 __client를 발급하면 즉시 갱신 (세션 연장)
        m = re.search(r'__client=([^;,\s]+)', r1.headers.get("set-cookie", ""))
        if m:
            self._update_raw_cookie("__client", m.group(1))
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

        # Step 2: 새 JWT 발급 (POST → fresh token)
        r2 = requests.post(
            f"https://auth.suno.com/v1/client/sessions/{session_id}/tokens?{qs}",
            headers={**auth_headers, "Content-Length": "0"},
            impersonate=REQUESTS_IMPERSONATE,
            timeout=15,
        )
        if r2.ok:
            token = r2.json().get("jwt")
        else:
            logger.warning("POST 토큰 발급 실패(%d), last_active_token 폴백...", r2.status_code)
            token = sessions[0].get("last_active_token", {}).get("jwt")

        if not token:
            raise ValueError(f"JWT 파싱 실패. 응답: {str(r2.text)[:200]}")

        self._token = token

        # JWT payload 디코딩 (서명 검증 없음) → 만료·audience 즉시 확인
        try:
            padded = token.split(".")[1] + "=="
            payload = json.loads(base64.b64decode(padded).decode())
            exp     = payload.get("exp", 0)
            aud     = payload.get("aud", "?")
            exp_str = datetime.datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S") if exp else "?"
            logger.info("JWT 갱신 완료 | aud=%s | 만료=%s", aud, exp_str)
            if exp and exp < time.time():
                logger.warning(
                    "⚠️  발급된 JWT가 이미 만료되어 있습니다! "
                    "브라우저에서 Suno에 로그인 후 SUNO_COOKIE를 새로 복사하세요."
                )
        except Exception:
            logger.info("JWT 토큰 갱신 완료 (payload 파싱 불가).")

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
                    # Suno API의 'Token validation failed' (422) 에러에 대한 특수 처리
                    if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code == 422 and attempt < max_retries:
                        try:
                            if "token validation failed" in e.response.json().get("detail", "").lower():
                                logger.warning(
                                    "[%s] 토큰 인증 실패(422). 즉시 토큰을 갱신하고 재시도합니다. (시도 %d/%d)",
                                    func.__name__, attempt, max_retries
                                )
                                suno_auth.refresh_token()
                                continue  # 딜레이 없이 바로 다음 시도
                        except (json.JSONDecodeError, AttributeError):
                            # JSON 파싱 실패 등은 일반 에러로 간주하고 아래에서 처리
                            pass
                        except Exception as refresh_e:
                            logger.critical("토큰 갱신 중 치명적 오류 발생: %s", refresh_e)
                            raise refresh_e # 토큰 갱신 실패는 즉시 중단

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

    # 현재 KST(한국 시간) 계산 (GitHub Actions의 UTC 환경 보정)
    now_utc    = datetime.datetime.utcnow()
    now_kst    = now_utc + datetime.timedelta(hours=9)
    today      = now_kst.date()
    hour       = now_kst.hour

    weekday_kr = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"][today.weekday()]
    month      = today.month
    season     = (
        "봄" if 3 <= month <= 5 else
        "여름" if 6 <= month <= 8 else
        "가을" if 9 <= month <= 11 else
        "겨울"
    )

    # 시간대에 따른 분위기 키워드 분기
    if hour < 12:  # 오전 9시 실행
        time_of_day, time_mood = "아침", "상쾌하고 활기찬, 희망찬, 모닝 커피"
    else:  # 오후 6시 실행
        time_of_day, time_mood = "저녁", "차분하고 감성적인, 하루를 마무리하는, 칠링"

    system_prompt = (
        "당신은 트렌디하고 다재다능한 글로벌 음악 프로듀서입니다. 매일 전혀 다른 장르와 분위기의 히트곡 컨셉을 기획합니다.\n"
        "팝, R&B, 힙합, EDM, 록, 인디 등 다양한 장르를 넘나들며, 때로는(약 20% 확률로) 중독성 강한 밈(Meme) 스타일의 '트렌디한 어린이용 동요/댄스곡'도 기획합니다.\n"
        "반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.\n"
        "{\n"
        '  "genre": "Suno API에 들어갈 장르 및 스타일 태그 (영문, 쉼표로 구분. 예: trendy pop, upbeat / catchy children song, electro / acoustic chillhop)",\n'
        '  "mood": "분위기 키워드 (한글, 3단어 이내)",\n'
        '  "lyrics": "실제 생성될 노래의 가사 (한국어 위주. 최소 15~20줄 이상, [Verse], [Chorus] 등 구조 포함하여 1분 이상 길이의 곡이 나올 수 있도록 충분한 분량으로 작성)",\n'
        '  "title": "유튜브 쇼츠용 제목 (한글, 시선을 끄는 매력적인 유튜브 감성 제목, 30자 이내)",\n'
        '  "description": "유튜브 설명란 소개 및 트렌디한 해시태그 3~5개 포함 (한글, 100자 이내)"\n'
        "}"
    )
    
    weekend_guide = (
        "\n\n[특별 지침] 오늘은 주말(토/일)입니다! 주말의 들뜬 기분을 반영하여, 시간대와 상관없이 "
        "에너제틱한 파티 음악, EDM, 페스티벌, 드라이브에 어울리는 신나는 팝 등 경쾌한 장르를 최우선으로 기획하세요."
    ) if today.weekday() >= 5 else ""
    
    user_prompt = (
        f"현재 한국 시간: {now_kst.strftime('%Y-%m-%d %H:%M')} ({weekday_kr}, {season}, {time_of_day})\n\n"
        f"이 시간대의 특징('{time_of_day}' - {time_mood})과 계절감을 seed로 활용해 "
        "지금 이 순간 듣기 딱 좋은 독특하고 매력적인 K-POP 음악 컨셉 하나를 기획해 주세요.\n"
        "시간대마다 전혀 다른 느낌이 나도록 장르와 악기 구성을 창의적으로 제안해야 합니다. "
        "아이돌 그룹의 타이틀곡이나 인기 수록곡 같은 느낌으로, 대중적이면서도 세련된 사운드를 지향하세요."
        "\n\n[제목 작성 지침] 유튜브 제목('title')은 밋밋하게 쓰지 말고, 시청자의 호기심을 자극하고 클릭을 유도하는 '어그로/후킹' 감성으로 작성하세요. "
        "(예: '나만 알고 싶은 새벽 2시 감성 플리', '10초 만에 분위기 찢는 퇴근길 노래', '듣자마자 소름 돋는 아침 텐션업 플레이리스트')"
        "\n\n[설명 및 해시태그 지침] 유튜브 설명란('description')에는 시청자의 공감을 이끌어내는 감성적인 소개글과 함께, 검색 노출을 극대화할 수 있는 트렌디한 해시태그(#)를 3~5개 이상 풍부하게 포함하세요. "
        "(예: #감성음악 #출근길 #플레이리스트 #알고리즘 #인디음악)"
        f"{weekend_guide}"
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

    with requests.Session(impersonate=REQUESTS_IMPERSONATE) as session:
        session.headers.update(_SUNO_HEADERS)

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
    """매 요청마다 fresh browser-token을 포함한 인증 헤더 반환."""
    return {
        "Authorization": f"Bearer {suno_auth.get_token()}", # JWT 토큰
        "browser-token": _make_browser_token(),             # 브라우저 토큰
        "device-id":     suno_auth.device_id,               # 디바이스 ID
        # Suno API는 Authorization 헤더로 인증하므로, Cookie 헤더는 불필요하거나 문제가 될 수 있음.
        # Clerk 인증 시에만 __client 쿠키를 사용하고, 실제 API 호출 시에는 Authorization 헤더만으로 충분함.
    }


@with_retry()
def _request_suno_generation(concept: dict, session: requests.Session) -> list[str]:
    """Suno API에 음악 생성 요청 후 clip_id 목록 반환."""
    logger.info("Suno 생성 요청 → 장르: [%s] | 가사: '%s...'", concept.get("genre"), concept.get("lyrics", "")[:30])

    # 브라우저 Network 탭에서 확인한 실제 payload 구조 (v2-web 기준)
    # - mv: "chirp-crow" (실제 모델명, v4.5-all에 해당)
    # - generation_type: "TEXT" (프롬프트 기반 생성)
    # - transaction_uuid: 요청마다 새 UUID
    # - v2-web 엔드포인트는 Cloudflare Turnstile token 필요 → v2/ 사용
    payload = {
        "generation_type":   "TEXT",
        "prompt":            concept["lyrics"],
        "tags":              concept.get("genre", ""),
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


# ---------------------------------------------------------------------------
# 독립 실행 (단독 테스트용)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    concept = generate_daily_concept()
    print("\n[오늘의 컨셉]")
    print(json.dumps(concept, ensure_ascii=False, indent=2))

    mp3_path = generate_and_download_audio(concept)
    print(f"\n[완료] 음원 저장 경로: {mp3_path}")
