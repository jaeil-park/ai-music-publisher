"""
video_maker.py
역할: DALL-E 3로 배경 이미지 생성 → FFmpeg로 이미지+음원 합성 → 유튜브 쇼츠용 mp4 출력
파이프라인 순서: brain.py -> generator.py -> [video_maker.py] -> uploader.py

의존성 설치:
    pip install ffmpeg-python openai requests python-dotenv
    (+ 시스템에 FFmpeg 바이너리 설치 필요: https://ffmpeg.org/download.html)
"""

import os
import time
import logging
import platform
import datetime
import requests
import textwrap
from pathlib import Path
from functools import wraps
from dotenv import load_dotenv
from openai import OpenAI

# ffmpeg-python 미설치 시 명확한 안내
try:
    import ffmpeg
except ImportError:
    raise ImportError(
        "ffmpeg-python 패키지가 없습니다. 설치 후 재실행하세요:\n"
        "    pip install ffmpeg-python\n"
        "시스템 FFmpeg 바이너리도 필요합니다: https://ffmpeg.org/download.html"
    )

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
# 상수
# ---------------------------------------------------------------------------
DATA_DIR    = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

BG_PATH     = DATA_DIR / "background.png"
OUTPUT_PATH = DATA_DIR / "output_shorts.mp4"

if platform.system() == "Windows":
    FONT_PATH = "C:/Windows/Fonts/malgun.ttf"
else:
    FONT_PATH = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"

FONT_SIZE   = 80
FONT_COLOR  = "white"
BORDER_W    = 3
BORDER_COLOR = "black"

# DALL-E 3 세로형 해상도 (유튜브 쇼츠 9:16 비율)
IMAGE_SIZE  = "1024x1792"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

MAX_RETRIES = 3
RETRY_DELAY = 5

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
                except Exception as e:
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
# 공개 진입점
# ---------------------------------------------------------------------------
def make_video(concept: dict, mp3_path: Path) -> Path:
    """
    배경 이미지 생성 → FFmpeg 합성 파이프라인.

    Args:
        concept : generate_daily_concept() 반환값
                  ('genre', 'mood', 'title' 키 사용)
        mp3_path: /data/*.mp3 절대 경로

    Returns:
        Path: 생성된 mp4 파일의 절대 경로 (data/output_shorts.mp4)
    """
    logger.info("=== 영상 제작 파이프라인 시작 ===")

    _generate_background(concept, BG_PATH)
    _compose_video(BG_PATH, mp3_path, concept["title"], OUTPUT_PATH)

    logger.info("=== 영상 제작 완료: %s ===", OUTPUT_PATH)
    return OUTPUT_PATH


# ---------------------------------------------------------------------------
# 내부 헬퍼 함수
# ---------------------------------------------------------------------------
@with_retry()
def _generate_background(concept: dict, out_path: Path) -> Path:
    """
    DALL-E 3로 장르/분위기에 어울리는 세로형 배경 이미지를 생성하고
    지정된 경로(out_path)에 저장.

    프롬프트: concept['mood'] + concept['genre'] 기반 영문 이미지 프롬프트
    해상도: 1024x1792 (유튜브 쇼츠 9:16)
    """
    genre = concept.get("genre", "music")
    mood  = concept.get("mood", "calm")

    # 현재 KST(한국 시간) 계산
    now_utc = datetime.datetime.utcnow()
    now_kst = now_utc + datetime.timedelta(hours=9)
    hour = now_kst.hour
    is_weekend = now_kst.weekday() >= 5

    # 시간대에 따른 시각적 분위기/색감(Lighting/Color palette) 영문 프롬프트 분기
    if 5 <= hour < 12:
        lighting = "Morning sunlight, bright and refreshing color palette, warm sunrise lighting"
    elif 12 <= hour < 17:
        lighting = "Clear daylight, vibrant and energetic color palette, vivid"
    elif 17 <= hour < 22:
        lighting = "Sunset golden hour, calm and warm color palette, twilight"
    else:
        lighting = "Night time, dark and moody color palette, neon or moonlight, lo-fi aesthetic"

    # 주말일 경우 파티/신나는 시각적 요소 추가
    weekend_visual = ", energetic party vibe, festival, dynamic and vibrant" if is_weekend else ""

    prompt = (
        f"Youtube shorts background, no text, emotional artwork. "
        f"Genre: {genre}, Mood: {mood}. "
        f"Visual style: {lighting}{weekend_visual}."
    )
    logger.info("DALL-E 3 이미지 생성 중... 프롬프트: '%s'", prompt[:80])

    client   = OpenAI(api_key=OPENAI_API_KEY)
    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size=IMAGE_SIZE,
        quality="standard",
        n=1,
    )

    image_url = response.data[0].url
    logger.info("이미지 URL 획득. 다운로드 중...")

    img_response = requests.get(image_url, timeout=60)
    img_response.raise_for_status()

    out_path.write_bytes(img_response.content)
    size_kb = out_path.stat().st_size / 1024
    logger.info("배경 이미지 저장 완료: %s (%.1f KB)", out_path.name, size_kb)
    return out_path


@with_retry()
def _compose_video(bg_path: Path, mp3_path: Path, title: str, output_path: Path) -> Path:
    """
    FFmpeg로 정적 이미지 + mp3를 합성하여 mp4 생성.

    - 오디오 길이에 맞춰 이미지를 루프
    - drawtext 필터로 영상 중앙에 제목 자막 삽입
    - pix_fmt yuv420p: 유튜브/SNS 호환성 확보
    """
    logger.info("FFmpeg 합성 시작: %s + %s → %s", bg_path.name, mp3_path.name, output_path.name)

    # 텍스트가 화면을 벗어나지 않도록 파이썬 textwrap으로 줄바꿈 처리 (가로 해상도 고려 약 14자 기준)
    wrapped_title = textwrap.fill(title, width=14)

    # FFmpeg 특수문자 이스케이프 (drawtext 필터용)
    safe_title = _escape_drawtext(wrapped_title)

    video_stream = ffmpeg.input(str(bg_path), loop=1, framerate=25)
    audio_stream = ffmpeg.input(str(mp3_path))

    video_with_text = video_stream.filter(
        "drawtext",
        fontfile=FONT_PATH,
        text=safe_title,
        fontsize=FONT_SIZE,
        fontcolor=FONT_COLOR,
        borderw=BORDER_W,
        bordercolor=BORDER_COLOR,
        shadowx=2,
        shadowy=2,
        shadowcolor="black",
        box=1,
        boxcolor="black@0.5",
        x="(w-text_w)/2",
        y="(h-text_h)/2 - 100",
        line_spacing=20,  # 여러 줄일 경우 가독성을 위한 줄 간격 추가
    )

    (
        ffmpeg
        .output(
            video_with_text,
            audio_stream,
            str(output_path),
            vcodec="libx264",
            acodec="aac",
            audio_bitrate="192k",  # 유튜브 쇼츠 고음질 유지를 위한 오디오 비트레이트 고정
            pix_fmt="yuv420p",
            shortest=None,       # 오디오 길이에서 자동 종료
        )
        .overwrite_output()
        .run(quiet=True)
    )

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("영상 합성 완료: %s (%.1f MB)", output_path.name, size_mb)
    return output_path


def _escape_drawtext(text: str) -> str:
    """
    FFmpeg drawtext 필터의 특수문자 이스케이프.
    콜론(:), 작은따옴표('), 백슬래시(\\)를 이스케이프.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace("'",  "\\'")
    text = text.replace(":",  "\\:")
    return text


# ---------------------------------------------------------------------------
# 독립 실행 (단독 테스트용)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    from pathlib import Path

    # 테스트용 더미 컨셉
    test_concept = {
        "genre": "Chillhop",
        "mood":  "청량한 봄",
        "title": "청량한 봄 Chillhop",
    }

    # 지정된 오디오 경로로 단독 테스트
    test_mp3_path = DATA_DIR / "20260324_audio.mp3"
    print(f"[테스트] mp3: {test_mp3_path}")

    output = make_video(test_concept, test_mp3_path)
    print(f"\n[완료] 영상 저장 경로: {output}")
