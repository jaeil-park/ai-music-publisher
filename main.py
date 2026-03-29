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
import datetime
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
from src.generator import generate_daily_concept, generate_and_download_audio, save_updated_cookie_to_env
from src.video_maker import make_video, BG_PATH
from src.uploader import upload_to_youtube
from src.uploader_tiktok import upload_to_tiktok
from src.uploader_ig import upload_to_instagram

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
        
        # 에피소드 번호 (2025-01-01 기준 누적 일수)
        ep = (datetime.date.today() - datetime.date(2025, 1, 1)).days + 1

        # 컨셉 데이터에서 유튜브 메타데이터 추출
        base_title = concept.get("title", "AI Music Vibes")
        title = f"{base_title} #{ep}"
        genre_tag  = concept.get("genre", "Music").split(",")[0].strip()
        mood_tag   = concept.get("mood", "chill").split()[0]

        description = (
            concept.get("description", "AI-composed melody of the day. Sit back and enjoy.")
            + "\n\n🎵 Save this track & subscribe for daily AI music drops."
        )
        tags = [
            "AImusic", "AIgenerated", "artificialintelligence",
            genre_tag, mood_tag,
            "Shorts", "YouTubeShorts", "music", "newmusic",
        ]

        # 생성된 가사가 있다면 유튜브 설명란에 추가
        if concept.get("lyrics"):
            description += f"\n\n[Lyrics]\n{concept['lyrics'].strip()}"

        # 실제 서비스 배포이므로 'public(공개)' 상태로 업로드
        video_id = upload_to_youtube(
            video_path=str(mp4_path),
            title=title,
            description=description,
            tags=tags,
            privacy_status="public",
            thumbnail_path=str(BG_PATH)
        )
        logger.info("유튜브 업로드 성공! Video URL: https://youtu.be/%s", video_id)

        # [Step 4] 틱톡 업로드 (YouTube 채널 크로스프로모 포함)
        logger.info("[Step 4] TikTok 업로드 시작")
        tiktok_title = f"{base_title} #{ep} 🎵 Full playlist on YouTube → @Chillhop_AI"
        upload_to_tiktok(video_path=str(mp4_path), title=tiktok_title)

        # [Step 5] Instagram Reels 업로드
        logger.info("[Step 5] Instagram Reels 업로드 시작")
        ig_caption = (
            f"{base_title} #{ep}\n\n"
            f"{concept.get('description', '')}\n\n"
            f"🎵 Full playlist on YouTube → @chillhop_ai\n\n"
            f"#AImusic #AIgenerated #chillhop #lofi #music #reels #newmusic"
        )
        upload_to_instagram(video_path=str(mp4_path), caption=ig_caption)

        # [Step 5] 임시 파일 정리 (아카이빙)
        logger.info("[Step 6] 임시 파일 정리 및 아카이빙")
        if mp3_path.exists():
            shutil.move(str(mp3_path), str(ARCHIVE_DIR / mp3_path.name))
        
        if BG_PATH.exists():
            bg_new_name = f"background_{int(time.time())}.png"
            shutil.move(str(BG_PATH), str(ARCHIVE_DIR / bg_new_name))

        logger.info("========== 🚀 모든 파이프라인이 성공적으로 완료되었습니다 ==========")
        
        success_msg = (
            f"🎉 **[성공] AI 음악 배포 완료!**\n"
            f"> **제목:** {title}\n"
            f"> **링크:** https://youtu.be/{video_id}\n"
            f"> **채널:** https://www.youtube.com/@Chillhop_AI"
        )
        send_discord_notification(success_msg)

    except Exception as e:
        logger.critical("파이프라인 실행 중 치명적인 오류 발생: %s", e, exc_info=True)
        fail_msg = f"🚨 **[실패] AI 음악 자동화 파이프라인 오류 발생**\n> **에러 내용:** `{str(e)}`"
        send_discord_notification(fail_msg)
        sys.exit(1)
    finally:
        # 성공/실패 무관하게 갱신된 SUNO_COOKIE를 .env에 저장 → GitHub Actions 로테이션에 반영
        save_updated_cookie_to_env()

if __name__ == "__main__":
    main()