"""
uploader_ig.py
역할: 완성된 쇼츠 영상을 Instagram Reels에 업로드 (Facebook Graph API)
파이프라인 순서: brain.py -> generator.py -> video_maker.py -> uploader.py -> [uploader_ig.py]

사전 준비:
    1. Facebook Developer App 생성 및 Instagram Graph API 활성화
    2. 비즈니스/크리에이터 Instagram 계정 연결
    3. 장기 액세스 토큰 발급 (60일 유효, 주기적 갱신 필요)
    4. .env에 IG_USER_ID, IG_ACCESS_TOKEN, GITHUB_TOKEN, GITHUB_VIDEO_REPO 설정

필수 권한 (App 레벨):
    - instagram_basic
    - instagram_content_publish
    - pages_show_list
    - pages_read_engagement

의존성:
    pip install requests python-dotenv
"""

import os
import time
import logging
from pathlib import Path
from functools import wraps

import requests
from dotenv import load_dotenv
from src._hosting import upload_to_public_url, delete_github_release

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
IG_USER_ID      = os.getenv("IG_USER_ID")       # Instagram 비즈니스 계정 User ID (숫자)
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")   # Graph API 장기 액세스 토큰

GRAPH_API_BASE  = "https://graph.facebook.com/v21.0"
MAX_RETRIES     = 3
RETRY_DELAY     = 10
POLL_INTERVAL   = 5    # 컨테이너 상태 폴링 간격 (초)
POLL_TIMEOUT    = 300  # 컨테이너 완성 최대 대기 시간 (초)

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
# 내부 헬퍼: 설정값 검증
# ---------------------------------------------------------------------------
def _validate_config():
    if not IG_USER_ID:
        raise EnvironmentError(".env에 IG_USER_ID가 설정되지 않았습니다.")
    if not IG_ACCESS_TOKEN:
        raise EnvironmentError(".env에 IG_ACCESS_TOKEN이 설정되지 않았습니다.")

# ---------------------------------------------------------------------------
# Step 1: 미디어 컨테이너 생성
# ---------------------------------------------------------------------------
@with_retry()
def _create_media_container(video_url: str, caption: str) -> str:
    """
    Instagram 미디어 컨테이너를 생성하고 creation_id를 반환.

    Args:
        video_url: 공개 접근 가능한 mp4 영상 URL (S3, GCS 등)
        caption:   게시물 캡션 (해시태그 포함 권장)

    Returns:
        creation_id (str): 컨테이너 ID
    """
    url = f"{GRAPH_API_BASE}/{IG_USER_ID}/media"
    payload = {
        "media_type":    "REELS",
        "video_url":     video_url,
        "caption":       caption,
        "share_to_feed": "true",    # 피드에도 공유
        "access_token":  IG_ACCESS_TOKEN,
    }

    logger.info("Instagram 미디어 컨테이너 생성 요청 중... (video_url: %s)", video_url)
    resp = requests.post(url, data=payload, timeout=30)
    if not resp.ok:
        logger.error("Instagram API 에러 응답 본문: %s", resp.text)
    resp.raise_for_status()

    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"컨테이너 생성 실패, 응답: {data}")

    creation_id = data["id"]
    logger.info("미디어 컨테이너 생성 성공. creation_id: %s", creation_id)
    return creation_id

# ---------------------------------------------------------------------------
# Step 2: 컨테이너 상태 폴링 (FINISHED 대기)
# ---------------------------------------------------------------------------
def _wait_for_container(creation_id: str):
    """
    미디어 컨테이너가 처리 완료(FINISHED) 상태가 될 때까지 폴링.

    상태값:
        EXPIRED   - 유효기간 만료 (24시간 이내에 publish 필요)
        ERROR     - 처리 오류
        FINISHED  - 게시 가능 상태
        IN_PROGRESS - 처리 중
        PUBLISHED - 이미 게시된 상태
    """
    url = f"{GRAPH_API_BASE}/{creation_id}"
    params = {
        "fields":       "status_code",
        "access_token": IG_ACCESS_TOKEN,
    }

    elapsed = 0
    logger.info("컨테이너 처리 완료 대기 중... (최대 %d초)", POLL_TIMEOUT)

    while elapsed < POLL_TIMEOUT:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()

        status = resp.json().get("status_code", "UNKNOWN")
        logger.info("컨테이너 상태: %s (%d초 경과)", status, elapsed)

        if status == "FINISHED":
            logger.info("컨테이너 처리 완료!")
            return
        elif status == "ERROR":
            raise RuntimeError(f"Instagram 미디어 컨테이너 처리 오류. creation_id: {creation_id}")
        elif status == "EXPIRED":
            raise RuntimeError(f"Instagram 미디어 컨테이너가 만료됨. creation_id: {creation_id}")

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    raise TimeoutError(f"컨테이너 처리 타임아웃 ({POLL_TIMEOUT}초 초과). creation_id: {creation_id}")

# ---------------------------------------------------------------------------
# Step 3: 미디어 퍼블리시
# ---------------------------------------------------------------------------
@with_retry()
def _publish_container(creation_id: str) -> str:
    """
    처리 완료된 미디어 컨테이너를 Instagram에 게시.

    Returns:
        media_id (str): 게시된 Instagram 미디어 ID
    """
    url = f"{GRAPH_API_BASE}/{IG_USER_ID}/media_publish"
    payload = {
        "creation_id":  creation_id,
        "access_token": IG_ACCESS_TOKEN,
    }

    logger.info("Instagram Reels 게시 요청 중... creation_id: %s", creation_id)
    resp = requests.post(url, data=payload, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"Reels 게시 실패, 응답: {data}")

    media_id = data["id"]
    logger.info("Instagram Reels 게시 성공! media_id: %s", media_id)
    return media_id

# ---------------------------------------------------------------------------
# 메인 진입 함수
# ---------------------------------------------------------------------------
def upload_to_instagram(video_path: str, caption: str) -> str:
    """
    Instagram Reels에 영상을 업로드하는 메인 함수.

    Args:
        video_path: 로컬 mp4 파일의 절대 경로
        caption:    Instagram 게시물 캡션 (해시태그 포함 권장)

    Returns:
        게시된 Instagram 미디어 ID
    """
    logger.info("=== Instagram Reels 업로드 파이프라인 시작 ===")
    _validate_config()

    # Step 0: 로컬 파일 → GitHub Releases 공개 URL 변환
    logger.info("[IG Step 0] GitHub Releases에 영상 업로드 중... (%s)", video_path)
    video_url, release_id = upload_to_public_url(video_path)

    try:
        # Step 1: 미디어 컨테이너 생성
        logger.info("[IG Step 1] 미디어 컨테이너 생성 중...")
        creation_id = _create_media_container(video_url=video_url, caption=caption)

        # Step 2: 처리 완료 대기
        logger.info("[IG Step 2] 컨테이너 처리 완료 대기 중...")
        _wait_for_container(creation_id)

        # Step 3: 게시
        logger.info("[IG Step 3] Reels 게시 중...")
        media_id = _publish_container(creation_id)

    finally:
        # Step 4: GitHub 임시 릴리즈 삭제 (성공/실패 무관하게 정리)
        logger.info("[IG Step 4] GitHub 임시 릴리즈 정리 중...")
        delete_github_release(release_id)

    logger.info("=== Instagram Reels 업로드 완료! media_id: %s ===", media_id)
    logger.info("Instagram 게시물 링크: https://www.instagram.com/p/%s/", media_id)

    return media_id

# ---------------------------------------------------------------------------
# 독립 실행 (단독 테스트용)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_video_path = str(BASE_DIR / "data" / "output_shorts.mp4")
    test_caption = (
        "AI가 작곡한 오늘의 감성 멜로디 🎵\n"
        "하루를 산뜻하게 시작해보세요!\n\n"
        "#AI음악 #AIMusic #감성 #릴스 #Reels #Music"
    )

    media_id = upload_to_instagram(test_video_path, test_caption)
    print(f"\n[완료] Instagram 미디어 ID: {media_id}")
    print(f"       링크: https://www.instagram.com/p/{media_id}/")
