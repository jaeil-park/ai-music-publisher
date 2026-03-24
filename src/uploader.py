"""
uploader.py
역할: 완성된 쇼츠 영상(mp4)을 YouTube Data API v3를 활용해 유튜브에 업로드
파이프라인 순서: brain.py -> generator.py -> video_maker.py -> [uploader.py]

의존성 설치:
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
"""

import os
import time
import logging
from pathlib import Path
from functools import wraps

# Google API 라이브러리 (없을 시 설치 안내)
try:
    import google_auth_oauthlib.flow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
except ImportError:
    raise ImportError(
        "Google API 관련 패키지가 없습니다. 설치 후 재실행하세요:\n"
        "    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
    )

# ---------------------------------------------------------------------------
# 초기 설정
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수 및 경로
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.parent
CLIENT_SECRETS_FILE = BASE_DIR / "client_secrets.json"
TOKEN_FILE          = BASE_DIR / "token.json"
DATA_DIR            = BASE_DIR / "data"

# 업로드 권한 스코프
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"

MAX_RETRIES = 3
RETRY_DELAY = 10

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
                except HttpError as e:
                    logger.error("[%s] API 에러 응답 (시도 %d/%d): %s", func.__name__, attempt, max_retries, e)
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
# 인증 및 API 서비스 초기화
# ---------------------------------------------------------------------------
def get_authenticated_service():
    """
    OAuth 2.0 인증을 수행하고 YouTube 리소스 객체를 반환.
    최초 실행 시 브라우저를 통해 인증 코드를 받아오고, token.json 에 저장.
    이후 실행 시 token.json 을 사용해 자동 인증.
    """
    creds = None
    
    if TOKEN_FILE.exists():
        logger.info("기존 token.json을 로드하여 인증을 시도합니다.")
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        
    # 자격 증명이 없거나 유효하지 않은 경우 (만료 등)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("만료된 토큰을 자동 갱신합니다.")
            creds.refresh(Request())
        else:
            logger.info("최초 인증을 위해 브라우저 창을 엽니다. 로그인을 진행해 주세요.")
            if not CLIENT_SECRETS_FILE.exists():
                raise FileNotFoundError(f"인증 파일이 없습니다: {CLIENT_SECRETS_FILE}")
                
            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
            
        # 성공적으로 인증된 자격 증명을 token.json 에 저장
        with open(TOKEN_FILE, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())
        logger.info("새로운 인증 토큰이 token.json에 저장되었습니다.")
        
    return build(API_SERVICE_NAME, API_VERSION, credentials=creds)

# ---------------------------------------------------------------------------
# 업로드 메인 로직
# ---------------------------------------------------------------------------
@with_retry()
def upload_to_youtube(video_path: str, title: str, description: str, tags: list, privacy_status: str = "private", thumbnail_path: str = None) -> str:
    """
    지정된 영상을 유튜브 쇼츠로 업로드.
    
    Args:
        video_path: 업로드할 mp4 영상의 절대 경로
        title: 유튜브 제목
        description: 유튜브 설명란 내용
        tags: 검색 태그 리스트
        privacy_status: 영상 공개 상태 (public, private, unlisted)
        thumbnail_path: 업로드할 맞춤 썸네일 이미지의 절대 경로 (선택)
        
    Returns:
        업로드 성공 시 생성된 유튜브 비디오 ID 반환
    """
    logger.info("=== 유튜브 업로드 파이프라인 시작 ===")
    
    # 쇼츠 포맷 필수 조건 강제 포함
    if "#Shorts" not in title and "#Shorts" not in description:
        description += "\n\n#Shorts"

    youtube = get_authenticated_service()
    
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "10"  # 10 = Music 카테고리
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False
        }
    }
    
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media
    )
    
    logger.info("유튜브 API로 영상 전송 중... (%s)", video_path)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("업로드 진행률: %d%%", int(status.progress() * 100))
            
    video_id = response.get("id")
    logger.info("=== 유튜브 업로드 성공! ===")
    logger.info("비디오 ID: %s", video_id)

    # 맞춤 썸네일 업로드
    if thumbnail_path and os.path.exists(thumbnail_path):
        logger.info("맞춤 썸네일 업로드 중... (%s)", thumbnail_path)
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path)
            ).execute()
            logger.info("썸네일 업로드 성공!")
        except HttpError as e:
            logger.warning("썸네일 업로드 실패 (채널 인증/권한 부족일 수 있습니다): %s", e)

    logger.info("유튜브 스튜디오 링크: https://studio.youtube.com/video/%s/edit", video_id)
    
    return video_id

# ---------------------------------------------------------------------------
# 독립 실행 (단독 테스트용)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 테스트 영상 경로
    test_video_path = str(DATA_DIR / "output_shorts.mp4")
    
    if not os.path.exists(test_video_path):
        logger.error("업로드할 테스트 영상이 없습니다: %s", test_video_path)
        logger.error("먼저 video_maker.py를 실행하여 영상을 만들어 주세요.")
        exit(1)
        
    test_title = "청량한 봄의 감성 멜로디 (AI 음악) #Shorts"
    test_description = "AI가 생성한 봄 분위기의 감성 칠합(Chillhop) 음악입니다.\n하루를 산뜻하게 시작해보세요!"
    test_tags = ["AI음악", "Chillhop", "봄", "수면음악", "감성"]
    
    video_id = upload_to_youtube(test_video_path, test_title, test_description, test_tags)
    print(f"\n[완료] 업로드된 비디오 URL: https://youtu.be/{video_id}")