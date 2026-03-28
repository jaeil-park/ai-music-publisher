"""
uploader_tiktok.py
역할: 완성된 쇼츠 영상을 TikTok에 업로드 (TikTok Content Posting API - FILE_UPLOAD 방식)
파이프라인 순서: brain.py -> generator.py -> video_maker.py -> uploader.py -> [uploader_tiktok.py]

사전 준비:
    1. TikTok for Developers 앱 생성
    2. Login Kit → Redirect URI: Desktop 탭에 http://localhost:8080/callback 등록
    3. Content Posting API → Direct Post 활성화
    4. scripts/tiktok_auth.py 실행하여 토큰 발급
    5. .env에 TIKTOK_ACCESS_TOKEN, TIKTOK_REFRESH_TOKEN, TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET 설정

필수 OAuth 스코프:
    - video.publish  (영상 게시)
    - video.upload   (영상 업로드)

의존성:
    pip install requests python-dotenv
"""

import os
import math
import time
import logging
from pathlib import Path
from functools import wraps

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 초기 설정
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# 설정값 로드
# ---------------------------------------------------------------------------
TIKTOK_ACCESS_TOKEN  = os.getenv("TIKTOK_ACCESS_TOKEN")
TIKTOK_REFRESH_TOKEN = os.getenv("TIKTOK_REFRESH_TOKEN")
TIKTOK_CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"
TOKEN_URL       = f"{TIKTOK_API_BASE}/oauth/token/"
ENV_PATH        = BASE_DIR / ".env"

MAX_RETRIES    = 3
RETRY_DELAY    = 10
POLL_INTERVAL  = 5    # 게시 상태 폴링 간격 (초)
POLL_TIMEOUT   = 300  # 게시 완료 최대 대기 시간 (초)
CHUNK_SIZE     = 10 * 1024 * 1024  # 10MB 청크

# ---------------------------------------------------------------------------
# 공통 재시도 데코레이터
# ---------------------------------------------------------------------------
def with_retry(max_retries: int = MAX_RETRIES, delay: int = RETRY_DELAY):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.RequestException as e:
                    logger.error("[%s] 네트워크 에러 (시도 %d/%d): %s", func.__name__, attempt, max_retries, e)
                    if attempt < max_retries:
                        logger.info("%d초 후 재시도합니다...", delay)
                        time.sleep(delay)
                    else:
                        raise
                except Exception as e:
                    logger.error("[%s] 예기치 않은 에러 (시도 %d/%d): %s", func.__name__, attempt, max_retries, e)
                    if attempt < max_retries:
                        time.sleep(delay)
                    else:
                        raise
        return wrapper
    return decorator

# ---------------------------------------------------------------------------
# 토큰 자동 갱신
# ---------------------------------------------------------------------------
def _refresh_access_token() -> str:
    """refresh_token으로 새로운 access_token을 발급받고 .env에 저장."""
    global TIKTOK_ACCESS_TOKEN, TIKTOK_REFRESH_TOKEN

    if not TIKTOK_REFRESH_TOKEN:
        raise EnvironmentError(
            "TIKTOK_REFRESH_TOKEN이 없습니다. "
            "scripts/tiktok_auth.py를 실행해 최초 토큰을 발급받으세요."
        )
    if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
        raise EnvironmentError(".env에 TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET이 설정되지 않았습니다.")

    logger.info("TikTok access_token 갱신 중...")
    payload = {
        "client_key":    TIKTOK_CLIENT_KEY,
        "client_secret": TIKTOK_CLIENT_SECRET,
        "grant_type":    "refresh_token",
        "refresh_token": TIKTOK_REFRESH_TOKEN,
    }
    resp = requests.post(TOKEN_URL, data=payload,
                         headers={"Content-Type": "application/x-www-form-urlencoded"},
                         timeout=30)
    resp.raise_for_status()

    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"토큰 갱신 실패: {data.get('error_description', data['error'])}")

    new_access  = data["access_token"]
    new_refresh = data.get("refresh_token", TIKTOK_REFRESH_TOKEN)

    try:
        from dotenv import set_key
        set_key(str(ENV_PATH), "TIKTOK_ACCESS_TOKEN",  new_access)
        set_key(str(ENV_PATH), "TIKTOK_REFRESH_TOKEN", new_refresh)
    except Exception as e:
        logger.warning(".env 토큰 저장 실패 (메모리에서만 갱신): %s", e)

    TIKTOK_ACCESS_TOKEN  = new_access
    TIKTOK_REFRESH_TOKEN = new_refresh
    logger.info("TikTok access_token 갱신 완료.")
    return new_access

# ---------------------------------------------------------------------------
# 내부 헬퍼: 설정값 검증 및 토큰 준비
# ---------------------------------------------------------------------------
def _validate_config():
    global TIKTOK_ACCESS_TOKEN
    if not TIKTOK_ACCESS_TOKEN:
        logger.info("TIKTOK_ACCESS_TOKEN이 없습니다. refresh_token으로 갱신을 시도합니다.")
        TIKTOK_ACCESS_TOKEN = _refresh_access_token()

def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {TIKTOK_ACCESS_TOKEN}",
        "Content-Type":  "application/json; charset=UTF-8",
    }

# ---------------------------------------------------------------------------
# Step 1: 게시 초기화 (FILE_UPLOAD) → publish_id + upload_url 반환
# ---------------------------------------------------------------------------
@with_retry()
def _init_file_upload(video_path: str, title: str) -> tuple[str, str]:
    """
    FILE_UPLOAD 방식으로 게시를 초기화.
    도메인 인증 없이 영상을 TikTok 서버에 직접 업로드.

    Returns:
        (publish_id, upload_url)
    """
    video_size        = os.path.getsize(video_path)
    total_chunk_count = math.ceil(video_size / CHUNK_SIZE)

    url     = f"{TIKTOK_API_BASE}/post/publish/video/init/"
    headers = _auth_headers()
    payload = {
        "post_info": {
            "title":                    title[:2200],
            "privacy_level":            "PUBLIC_TO_EVERYONE",
            "disable_duet":             False,
            "disable_comment":          False,
            "disable_stitch":           False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source":             "FILE_UPLOAD",
            "video_size":         video_size,
            "chunk_size":         CHUNK_SIZE,
            "total_chunk_count":  total_chunk_count,
        },
    }

    logger.info("TikTok 업로드 초기화 중... (%.1fMB, %d 청크)",
                video_size / (1024 * 1024), total_chunk_count)

    resp = requests.post(url, json=payload, headers=headers, timeout=30)

    if resp.status_code == 401:
        logger.warning("access_token 만료 감지. 자동 갱신 후 재시도합니다.")
        new_token = _refresh_access_token()
        headers["Authorization"] = f"Bearer {new_token}"
        resp = requests.post(url, json=payload, headers=headers, timeout=30)

    if not resp.ok:
        logger.error("TikTok API 에러 응답: %s", resp.text)
    resp.raise_for_status()

    data  = resp.json()
    error = data.get("error", {})
    if error.get("code", "ok") != "ok":
        raise RuntimeError(f"TikTok 업로드 초기화 실패: {error}")

    publish_id = data["data"]["publish_id"]
    upload_url = data["data"]["upload_url"]
    logger.info("업로드 초기화 완료. publish_id: %s", publish_id)
    return publish_id, upload_url

# ---------------------------------------------------------------------------
# Step 2: 영상 청크 업로드
# ---------------------------------------------------------------------------
def _upload_chunks(video_path: str, upload_url: str):
    """영상을 CHUNK_SIZE 단위로 분할하여 TikTok 서버에 업로드."""
    video_size = os.path.getsize(video_path)
    total_chunks = math.ceil(video_size / CHUNK_SIZE)

    with open(video_path, "rb") as f:
        for chunk_idx in range(total_chunks):
            start = chunk_idx * CHUNK_SIZE
            chunk = f.read(CHUNK_SIZE)
            end   = start + len(chunk) - 1

            headers = {
                "Content-Range":  f"bytes {start}-{end}/{video_size}",
                "Content-Type":   "video/mp4",
                "Content-Length": str(len(chunk)),
            }

            logger.info("청크 업로드 중... (%d/%d, %d-%d bytes)",
                        chunk_idx + 1, total_chunks, start, end)

            resp = requests.put(upload_url, data=chunk, headers=headers, timeout=120)
            if not resp.ok:
                logger.error("청크 업로드 에러 응답: %s", resp.text)
            resp.raise_for_status()

    logger.info("모든 청크 업로드 완료.")

# ---------------------------------------------------------------------------
# Step 3: 게시 상태 폴링
# ---------------------------------------------------------------------------
def _wait_for_publish(publish_id: str):
    """PUBLISH_COMPLETE 될 때까지 폴링."""
    url     = f"{TIKTOK_API_BASE}/post/publish/status/fetch/"
    payload = {"publish_id": publish_id}
    elapsed = 0

    logger.info("TikTok 게시 완료 대기 중... (최대 %d초)", POLL_TIMEOUT)

    while elapsed < POLL_TIMEOUT:
        resp = requests.post(url, json=payload, headers=_auth_headers(), timeout=15)
        resp.raise_for_status()

        data   = resp.json()
        status = data.get("data", {}).get("status", "UNKNOWN")
        logger.info("TikTok 게시 상태: %s (%d초 경과)", status, elapsed)

        if status == "PUBLISH_COMPLETE":
            logger.info("TikTok 게시 완료!")
            return
        elif status == "FAILED":
            fail_reason = data.get("data", {}).get("fail_reason", "알 수 없음")
            raise RuntimeError(f"TikTok 게시 실패. 이유: {fail_reason}")

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    raise TimeoutError(f"TikTok 게시 타임아웃 ({POLL_TIMEOUT}초 초과). publish_id: {publish_id}")

# ---------------------------------------------------------------------------
# 메인 진입 함수
# ---------------------------------------------------------------------------
def upload_to_tiktok(video_path: str, title: str) -> str:
    """
    TikTok에 영상을 직접 업로드 (FILE_UPLOAD 방식, 도메인 인증 불필요).

    Args:
        video_path: 로컬 mp4 파일의 절대 경로
        title:      TikTok 게시물 제목

    Returns:
        publish_id (str)
    """
    logger.info("=== TikTok 업로드 파이프라인 시작 ===")
    _validate_config()

    # Step 1: 게시 초기화
    logger.info("[TT Step 1] TikTok 업로드 초기화 중...")
    publish_id, upload_url = _init_file_upload(video_path, title)

    # Step 2: 청크 업로드
    logger.info("[TT Step 2] 영상 청크 업로드 중...")
    _upload_chunks(video_path, upload_url)

    # Step 3: 게시 완료 대기
    logger.info("[TT Step 3] TikTok 게시 완료 대기 중...")
    _wait_for_publish(publish_id)

    logger.info("=== TikTok 업로드 완료! publish_id: %s ===", publish_id)
    return publish_id

# ---------------------------------------------------------------------------
# 독립 실행 (단독 테스트용)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_video_path = str(BASE_DIR / "data" / "output_shorts.mp4")
    test_title = "AI가 작곡한 오늘의 감성 멜로디 🎵 #AI음악 #AIMusic #감성 #틱톡"

    publish_id = upload_to_tiktok(test_video_path, test_title)
    print(f"\n[완료] TikTok publish_id: {publish_id}")
