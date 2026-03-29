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
CLERK_API_VERSION    = "2025-11-10"
REQUESTS_IMPERSONATE = "chrome110"

# ---------------------------------------------------------------------------
# 음악 컨셉 카테고리 풀
# (label: LLM에 전달할 지시문 seed, weight: 선택 확률 가중치)
# ---------------------------------------------------------------------------
CONCEPT_POOL = [
    # ── K-pop 계열 ──────────────────────────────────────────────────
    {"id": "girl_group",   "weight": 8,
     "label": "최신 걸그룹 K-POP 타이틀곡 (아이브/뉴진스/르세라핌 스타일. 세련되고 중독적인 훅)",
     "genre_hint": "K-pop girl group, trendy pop, synth-driven"},
    {"id": "boy_group",    "weight": 8,
     "label": "최신 보이그룹 K-POP 타이틀곡 (BTS/세븐틴/스트레이키즈 스타일. 강렬한 드롭)",
     "genre_hint": "K-pop boy group, powerful pop, cinematic"},
    {"id": "kpop_ballad",  "weight": 7,
     "label": "K-POP 감성 발라드 (드라마 OST 느낌. 피아노+스트링, 감동적인 클라이맥스)",
     "genre_hint": "K-pop ballad, emotional, piano-driven, orchestral"},
    {"id": "kpop_rnb",     "weight": 6,
     "label": "K-POP R&B / 소울 트랙 (그루비한 비트, 부드러운 보컬, 어반 감성)",
     "genre_hint": "K-pop R&B, neo soul, groovy, urban"},
    # ── 국내 특수 장르 ───────────────────────────────────────────────
    {"id": "trot",         "weight": 6,
     "label": "모던 트로트 / 뽕끼 (임영웅·영탁 스타일. 중독적인 멜로디, 한국적 정서)",
     "genre_hint": "Korean trot, upbeat, addictive melody, traditional Korean pop"},
    {"id": "indie_acoustic","weight": 6,
     "label": "인디 어쿠스틱 (잔잔한 기타, 서정적인 가사, 10cm·적재 감성)",
     "genre_hint": "Korean indie, acoustic guitar, lo-fi, introspective"},
    {"id": "band_rock",    "weight": 5,
     "label": "인디 밴드 록 (라이브 드럼+기타, 에너지 넘치는 사운드, DAY6·CNBLUE 스타일)",
     "genre_hint": "Korean indie rock, live band, guitar-driven, energetic"},
    {"id": "hiphop_trap",  "weight": 6,
     "label": "한국 힙합 / 트랩 (딥한 808 베이스, 라임 있는 랩 가사, 지코·기리보이 스타일)",
     "genre_hint": "Korean hip-hop, trap, 808 bass, rhythmic rap"},
    # ── 글로벌 장르 ─────────────────────────────────────────────────
    {"id": "edm_festival", "weight": 7,
     "label": "EDM 빅룸 하우스 / 페스티벌 트랙 (빌드업-드롭 구조, 신나는 에너지)",
     "genre_hint": "big room EDM, festival house, energetic drop, euphoric"},
    {"id": "city_pop",     "weight": 6,
     "label": "시티팝 / 레트로 80s (야마시타 타츠로 스타일, 부드러운 신스, 드라이빙 바이브)",
     "genre_hint": "city pop, retro 80s, synthwave, smooth, nostalgic"},
    {"id": "latin_pop",    "weight": 5,
     "label": "라틴팝 / 레게톤 (댄서블한 리듬, 한국어+스페인어 믹스 가능)",
     "genre_hint": "latin pop, reggaeton, danceable, vibrant"},
    {"id": "lofi_jazz",    "weight": 12,   # ↑ 공부/작업 플레이리스트 → 구독 의도 최상위
     "label": "로파이 재즈 칠아웃 (공부할 때 듣는 음악, 재즈 코드, 릴렉싱 비트)",
     "genre_hint": "lo-fi jazz, chillhop, study music, mellow"},
    # ── 특별 카테고리 ────────────────────────────────────────────────
    {"id": "children",     "weight": 12,   # ↑ 부모가 반복 재생 → 구독 후 저장
     "label": "중독성 강한 어린이 동요/챈트 (유아가 따라 부르기 쉬운 단순 멜로디, "
              "동물·음식·색깔 등 소재, 틱톡에서 바이럴될 만한 귀여운 훅 포함)",
     "genre_hint": "catchy children song, playful, simple melody, nursery rhyme vibes"},
    {"id": "meme_dance",   "weight": 8,
     "label": "틱톡/릴스 챌린지 밈 댄스곡 (짧고 강렬한 훅, 반복적 안무 구간, 유머러스한 가사)",
     "genre_hint": "viral dance pop, TikTok challenge, catchy hook, fun"},
    {"id": "workout",      "weight": 10,   # ↑ 헬스 루틴 = 플레이리스트 저장 → 구독
     "label": "헬스장/운동 하이프 트랙 (빠른 BPM 140+, 강렬한 베이스, 동기부여 가사)",
     "genre_hint": "workout hype, high BPM, motivational, bass-heavy"},
    {"id": "sleep_calm",   "weight": 10,   # ↑ 수면 루틴 = 매일 재방문 → 구독
     "label": "힐링/수면 유도 음악 (ASMR 감성, 부드러운 보컬, 자연 소리 연상)",
     "genre_hint": "healing, sleep music, ambient, soft vocal, peaceful"},
]

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

        # Step 2: POST /touch → sid가 포함된 최신 JWT 발급
        # GET last_active_token은 구형 템플릿(sid 없음) JWT를 반환할 수 있음.
        # /touch는 브라우저와 동일한 방식으로 현재 JWT 템플릿(sid 포함)을 사용해 신규 발급.
        token = None
        r2 = requests.post(
            f"https://auth.suno.com/v1/client/sessions/{session_id}/touch?{qs}",
            headers={**auth_headers, "Content-Length": "0"},
            impersonate=REQUESTS_IMPERSONATE,
            timeout=15,
        )
        if r2.ok:
            touch_data    = r2.json()
            touch_session = touch_data.get("response") or touch_data.get("client", {}).get("sessions", [{}])[0]
            if isinstance(touch_session, dict):
                token = touch_session.get("last_active_token", {}).get("jwt")

        # Step 3: /touch 실패 시 POST /tokens 폴백
        if not token:
            logger.warning("POST /touch 실패 (%d). POST /tokens로 폴백합니다...",
                           r2.status_code if r2 else -1)
            r3 = requests.post(
                f"https://auth.suno.com/v1/client/sessions/{session_id}/tokens?{qs}",
                headers={**auth_headers, "Content-Length": "0"},
                impersonate=REQUESTS_IMPERSONATE,
                timeout=15,
            )
            if r3.ok:
                token = r3.json().get("jwt")
            if not token:
                raise ValueError(f"JWT 발급 실패. 응답: {str(r3.text)[:200]}")

        self._token = token

        # JWT 디코딩 → kid·sid·만료 즉시 검증
        try:
            header  = json.loads(base64.b64decode(token.split(".")[0] + "==").decode())
            padded  = token.split(".")[1] + "=="
            payload = json.loads(base64.b64decode(padded).decode())
            exp     = payload.get("exp", 0)
            aud     = payload.get("aud", "?")
            kid     = header.get("kid", "?")
            sid     = payload.get("sid", "MISSING")
            exp_str = datetime.datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S") if exp else "?"
            logger.info("JWT 갱신 완료 | aud=%s | kid=%s | sid=%s | 만료=%s",
                        aud, kid, sid[:24] if sid != "MISSING" else "MISSING", exp_str)
            if sid == "MISSING":
                logger.warning("⚠️  JWT에 sid 클레임 없음. Suno가 거부할 수 있습니다.")
            if kid != "suno-api-rs256-key-1":
                logger.warning("⚠️  예상치 못한 JWT kid=%s.", kid)
            if exp and exp < time.time():
                logger.warning("⚠️  발급된 JWT가 이미 만료됨. SUNO_COOKIE를 갱신하세요.")
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
                                time.sleep(15)  # Suno 서버 측 세션 동기화 대기
                                continue
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

    # 현재 KST 계산
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_kst = now_utc + datetime.timedelta(hours=9)
    today   = now_kst.date()
    hour    = now_kst.hour

    month  = today.month
    season = (
        "봄" if 3 <= month <= 5 else
        "여름" if 6 <= month <= 8 else
        "가을" if 9 <= month <= 11 else
        "겨울"
    )

    # ── 카테고리 랜덤 선택 ─────────────────────────────────────────
    # 날짜 + 시간대(오전/오후)를 시드로 사용 → 같은 실행 슬롯에서는 동일 결과,
    # 하루 2회 실행(오전/오후)이면 서로 다른 카테고리가 나옴.
    rng = random.Random(today.toordinal() * 2 + (0 if hour < 12 else 1))
    pool_weights   = [c["weight"] for c in CONCEPT_POOL]
    concept_choice = rng.choices(CONCEPT_POOL, weights=pool_weights, k=1)[0]

    logger.info("오늘의 카테고리: [%s] (%s)", concept_choice["id"], concept_choice["label"][:30])

    # 어린이 동요 카테고리는 가사 지침을 별도 적용
    if concept_choice["id"] == "children":
        lyrics_guide = (
            "가사는 유아(3~7세)가 따라 부르기 쉬운 짧고 반복적인 구조로 작성하세요. "
            "동물, 음식, 색깔, 계절 등 친숙한 소재를 사용하고, 의성어·의태어를 적극 활용하세요. "
            "[Verse], [Chorus] 구조를 유지하되 각 라인은 짧게(7자 이내) 작성하세요."
        )
    else:
        lyrics_guide = (
            "가사는 한국어 위주로, 최소 15~20줄 이상, [Verse]/[Pre-Chorus]/[Chorus]/[Bridge] "
            "등 구조를 포함해 1분 이상 길이의 곡이 나올 수 있도록 충분한 분량으로 작성하세요."
        )

    system_prompt = (
        "You are a global music producer who moves freely across genres.\n"
        "Plan hit song concepts true to the given 'today's genre category'.\n"
        "Reflect the genre's characteristics accurately — do not default everything to K-POP.\n"
        "Respond ONLY in the JSON format below. No other text.\n"
        "{\n"
        '  "genre": "Suno API genre/style tags (English, comma-separated, specific tags that work well in Suno)",\n'
        '  "mood": "mood keywords (English, max 3 words)",\n'
        f' "lyrics": "Song lyrics. {lyrics_guide}",\n'
        '  "title": "YouTube Shorts title (English, hooky and emotional, max 60 chars, no hashtags)",\n'
        '  "description": "YouTube description (English) + 3~5 trending hashtags, max 150 chars"\n'
        "}"
    )

    user_prompt = (
        f"[Today's Genre Category]\n{concept_choice['label']}\n\n"
        f"[Suno Genre Hint]\n{concept_choice['genre_hint']}\n\n"
        f"[Context] Korea: {season} season, {'morning' if hour < 12 else 'afternoon'} "
        f"(feel free to weave this into lyrics naturally, but prioritize the genre above all)\n\n"
        "Plan a unique, original music concept that perfectly fits the category above.\n"
        "Write the genre field as specific English Suno tags based on the hint.\n\n"
        "[Title] Write in English — hooky, emotional, and scroll-stopping.\n"
        "(e.g. 'This Beat Will Stay in Your Head All Day', "
        "'The Hidden Gem the Algorithm Buried', 'You've Never Heard This Genre Sound This Fresh')\n\n"
        "[Description] Write in English — a short intro that resonates with this genre's listeners + search hashtags."
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
    concept["category_id"] = concept_choice["id"]
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
