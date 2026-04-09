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
from src.llm_agent import generate_daily_concept
from src.media_generator import generate_and_download_audio
from src.video_maker import make_video
from src.uploader import upload_to_youtube
from src.uploader_tiktok import upload_to_tiktok
# from src.uploader_ig import upload_to_instagram

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
        # 에피소드 번호 (2025-01-01 기준 누적 일수)
        ep = (datetime.date.today() - datetime.date(2025, 1, 1)).days + 1
        
        # [Step 0] 오늘의 컨셉 기획 (OpenAI GPT-4o-mini)
        concept = generate_daily_concept(ep_number=ep)
        title = concept.get("title", f"AI Music Shorts #{ep}")
        description = concept.get("description", "Daily AI generated music.")
        lyrics = concept.get("lyrics", "")
        tags = concept.get("tags", ["AImusic", "Shorts", "Music"])
        on_screen_text = concept.get("on_screen_text", "AI Music Vibes")
        if "Shorts" not in tags:
            tags.append("Shorts")

        # [Step 1] DALL-E 3 배경 이미지 생성 (제거됨 - 검은 배경의 오디오 비주얼라이저로 대체)

        # [Step 2] 음원 생성 (Stability Audio API)
        logger.info("[Step 2] Stability Audio 음원 생성 시작")
        audio_path = generate_and_download_audio(concept["audio_prompt"])

        # [Step 3] 영상 합성 (FFmpeg)
        logger.info("[Step 3] FFmpeg 오디오 비주얼라이저 합성 시작")
        mp4_path = make_video(on_screen_text=on_screen_text, audio_path=audio_path)

        # [Step 4] 유튜브 쇼츠 업로드
        logger.info("[Step 4] YouTube Data API를 통한 쇼츠 업로드 시작")

        # 최종 유튜브 설명란 구성 (곡 설명 + 가사 + 추천 해시태그)
        final_description = f"{description}\n\n🎵 Subscribe for daily AI music drops!"
        if lyrics:
            final_description += f"\n\n[Lyrics]\n{lyrics}"
        
        # 추천 해시태그 추가
        final_description += "\n\n#AImusic #Shorts #Lofi #Chillhop #Music #AI #GenerativeAI"
        
        # 실제 서비스 배포이므로 'public(공개)' 상태로 업로드
        video_id = upload_to_youtube(
            video_path=str(mp4_path),
            title=title,
            description=final_description,
            tags=tags,
            privacy_status="public",
        )
        logger.info("유튜브 업로드 성공! Video URL: https://youtu.be/%s", video_id)

        # [Step 5] 틱톡 업로드 (YouTube 채널 크로스프로모 포함)
        logger.info("[Step 5] TikTok 업로드 시작")
        tiktok_title = f"{title} 🎵 Full playlist on YouTube → @Chillhop_AI"
        upload_to_tiktok(video_path=str(mp4_path), title=tiktok_title)

        # [Step 6] Instagram Reels 업로드 (토큰 문제로 임시 비활성화)
        # logger.info("[Step 6] Instagram Reels 업로드 시작")
        # ig_caption = (
        #     f"{title}\n\n"
        #     f"{description}\n\n"
        #     f"[Lyrics]\n{lyrics if lyrics else 'AI Generated Music'}\n\n"
        #     f"🎵 Full playlist on YouTube → @chillhop_ai\n\n"
        #     f"#AImusic #AIgenerated #chillhop #lofi #music #reels #newmusic"
        # )
        # upload_to_instagram(video_path=str(mp4_path), caption=ig_caption)

        # [Step 7] 임시 파일 정리 (아카이빙)
        logger.info("[Step 7] 임시 파일 정리 및 아카이빙")
        if audio_path.exists():
            shutil.move(str(audio_path), str(ARCHIVE_DIR / audio_path.name))

        logger.info("========== 🚀 모든 파이프라인이 성공적으로 완료되었습니다 ==========")
        
        success_msg = (
            f"🎉 **[성공] AI 음악 배포 완료!**\n"
            f"> **제목:** {title}\n"
            f"> **링크:** https://youtu.be/{video_id}\n"
            f"> **채널:** https://www.youtube.com/@Chillhop_AI"
        )
        send_discord_notification(success_msg)

    except OSError as e:
        if "GITHUB_VIDEO_REPO" in str(e) or "GITHUB_TOKEN" in str(e):
            logger.critical("인스타그램 업로드용 GitHub 호스팅 설정 오류: %s", e, exc_info=True)
            fail_msg = f"🚨 **[설정 오류] 인스타그램 업로드 실패**\n> `.env` 또는 GitHub Secrets에 `GITHUB_TOKEN`과 `GITHUB_VIDEO_REPO`를 설정해야 합니다. `README.md`를 참고하여 PAT 토큰을 발급하고 설정해주세요."
            send_discord_notification(fail_msg)
            sys.exit(1)
    except Exception as e:
        logger.critical("파이프라인 실행 중 치명적인 오류 발생: %s", e, exc_info=True)
        fail_msg = f"🚨 **[실패] AI 음악 자동화 파이프라인 오류 발생**\n> **에러 내용:** `{str(e)}`"
        send_discord_notification(fail_msg)
        sys.exit(1)

if __name__ == "__main__":
    main()