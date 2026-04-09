"""
test_local.py
업로드 로직(유튜브, 틱톡, 인스타그램)을 제외하고, 
[기획 -> 이미지 생성 -> 음원 생성 -> 영상 합성(mp4)] 까지만 실행하는 로컬 테스트 스크립트입니다.

실행 방법: python test_local.py
"""

import sys
import logging
from pathlib import Path

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("test_local")

from src.llm_agent import generate_daily_concept
from src.media_generator import generate_and_download_audio
from src.video_maker import make_video
def main():
    logger.info("========== [로컬 테스트] AI Music 파이프라인 가동 (업로드 제외) ==========")

    try:
        # 테스트용 에피소드 번호 (임의 지정)
        ep = 9999
        
        concept = generate_daily_concept(ep_number=ep)
        on_screen_text = concept.get("on_screen_text", "AI Music Test")
        
        logger.info("[Step 1] DALL-E 3 배경 이미지 생성 (건너뜀 - 검정색 배경으로 대체)")

        logger.info("[Step 2] Suno 음원 생성")
        audio_path = generate_and_download_audio(concept)

        logger.info("[Step 3] FFmpeg 영상 합성")
        mp4_path = make_video(on_screen_text=on_screen_text, audio_path=audio_path)

        logger.info("========== 🎉 로컬 테스트 완료! ==========")
        logger.info("최종 생성된 영상 위치: %s", mp4_path)
        logger.info("data 폴더로 이동하여 자막 위치, 음악, 줌인 애니메이션을 점검하세요.")

    except Exception as e:
        logger.critical("로컬 테스트 중 치명적인 오류 발생: %s", e, exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()