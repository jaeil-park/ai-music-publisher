"""
main.py
역할: AI-Music-Publisher 프로젝트의 마스터 컨트롤러 (전체 파이프라인 실행)
실행: python main.py
"""

import sys
import os
import time
import shutil
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

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

# 환경변수 및 디스코드 웹훅 설정
load_dotenv(dotenv_path=BASE_DIR / ".env")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def send_discord_notification(message: str):
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL이 설정되지 않아 디스코드 알림을 건너뜁니다.")
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10)
    except Exception as e:
        logger.warning("디스코드 알림 전송 실패: %s", e)

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

        # 생성된 가사가 있다면 유튜브 설명란에 추가
        if concept.get("lyrics"):
            description += f"\n\n[가사]\n{concept['lyrics'].strip()}"

        # 실제 서비스 배포이므로 'public(공개)' 상태로 업로드
        video_id = upload_to_youtube(
            video_path=str(mp4_path),
            title=title,
            description=description,
            tags=tags,
            privacy_status="public",
            thumbnail_path=str(BG_PATH)
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
        
        success_msg = f"🎉 **[성공] AI 음악 배포 완료!**\n> **제목:** {title}\n> **링크:** https://youtu.be/{video_id}"
        send_discord_notification(success_msg)

    except Exception as e:
        logger.critical("파이프라인 실행 중 치명적인 오류 발생: %s", e, exc_info=True)
        fail_msg = f"🚨 **[실패] AI 음악 자동화 파이프라인 오류 발생**\n> **에러 내용:** `{str(e)}`"
        send_discord_notification(fail_msg)
        sys.exit(1)

if __name__ == "__main__":
    main()