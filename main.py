"""
main.py
역할: AI-Music-Publisher 프로젝트의 마스터 컨트롤러 (전체 파이프라인 실행)
실행: python main.py
"""

import sys
import time
import shutil
import logging
from pathlib import Path

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# 각 단계 모듈 임포트
from src.generator import generate_daily_concept, generate_and_download_audio
from src.video_maker import make_video, BG_PATH
from src.uploader import upload_to_youtube

# 경로 및 아카이브 설정
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
ARCHIVE_DIR = DATA_DIR / "archive"

def main():
    logger.info("========== AI Music Publisher 자동화 공장 가동 ==========")
    ARCHIVE_DIR.mkdir(exist_ok=True)

    try:
        # [Step 1] 기획 및 음원 생성
        logger.info("[Step 1] 오늘의 음악 컨셉 기획 및 Suno 음원 생성 시작")
        concept = generate_daily_concept()
        mp3_path = generate_and_download_audio(concept)

        # [Step 2] 영상 합성 (DALL-E 이미지 + 음원)
        logger.info("[Step 2] DALL-E 배경 이미지 생성 및 FFmpeg 쇼츠 영상 합성 시작")
        mp4_path = make_video(concept, mp3_path)

        # [Step 3] 유튜브 쇼츠 업로드
        logger.info("[Step 3] YouTube Data API를 통한 쇼츠 업로드 시작")
        
        # 컨셉 데이터에서 유튜브 메타데이터 추출
        title = concept.get("title", "AI 감성 음악 #Shorts")
        description = concept.get("description", "AI가 작곡한 오늘의 감성 멜로디입니다.")
        tags = ["AI음악", concept.get("genre", "Music"), concept.get("mood", "감성"), "Shorts"]

        # 실제 서비스 배포이므로 'public(공개)' 상태로 업로드
        video_id = upload_to_youtube(
            video_path=str(mp4_path),
            title=title,
            description=description,
            tags=tags,
            privacy_status="public"
        )
        logger.info("🎉 유튜브 업로드 성공! Video URL: https://youtu.be/%s", video_id)

        # [Step 4] 임시 파일 정리 (아카이빙)
        logger.info("[Step 4] 임시 파일 정리 및 아카이빙")
        if mp3_path.exists():
            shutil.move(str(mp3_path), str(ARCHIVE_DIR / mp3_path.name))
        
        if BG_PATH.exists():
            bg_new_name = f"background_{int(time.time())}.png"
            shutil.move(str(BG_PATH), str(ARCHIVE_DIR / bg_new_name))

        logger.info("========== 🚀 모든 파이프라인이 성공적으로 완료되었습니다 ==========")

    except Exception as e:
        logger.critical("파이프라인 실행 중 치명적인 오류 발생: %s", e, exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()