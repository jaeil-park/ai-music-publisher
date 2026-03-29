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
    FONT_PATH = "C:/Windows/Fonts/arial.ttf"
else:
    FONT_PATH = next(
        (p for p in (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ) if Path(p).exists()),
        "",
    )
    if not FONT_PATH:
        logger.warning("No suitable font found — title overlay will be skipped")

FONT_SIZE    = 80
FONT_COLOR   = "white"
BORDER_W     = 3
BORDER_COLOR = "black"

# DALL-E 3 세로형 해상도 (유튜브 쇼츠 9:16 비율)
IMAGE_SIZE  = "1024x1792"

# 배경 애니메이션: DALL-E 이미지를 110% 확대 후 sin/cos 표류 (GIF 대비 고품질, 인코딩 빠름)
# BIG = 확대된 크기, OUT = 최종 출력 크기, DRIFT = 표류 여유 절반
_OUT_W, _OUT_H = 1024, 1792
_BIG_W, _BIG_H = 1126, 1970          # _OUT * 1.099 (짝수)
_DRIFT_X = (_BIG_W - _OUT_W) // 2    # 51 px
_DRIFT_Y = (_BIG_H - _OUT_H) // 2    # 89 px

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

MAX_RETRIES = 3
RETRY_DELAY = 5

# ---------------------------------------------------------------------------
# 카테고리별 DALL-E 시각 스타일 매핑
# ---------------------------------------------------------------------------
_CATEGORY_VISUALS = {
    "girl_group":     "K-pop girl group aesthetic, sparkly stage, pastel neon bokeh lights, dreamy and glamorous atmosphere",
    "boy_group":      "dramatic cinematic concert stage, dark and intense, electric blue and silver lighting, powerful energy",
    "kpop_ballad":    "misty night cityscape with soft bokeh lights, emotional and romantic, blue hour, melancholic beauty",
    "kpop_rnb":       "upscale urban night vibes, warm amber and gold city glow, luxurious and smooth aesthetic",
    "trot":           "vibrant Korean folk festival, warm cheerful colors, traditional lanterns, lively and nostalgic",
    "indie_acoustic": "cozy sunlit cafe interior, warm golden hour, autumn leaves on window, intimate and peaceful",
    "band_rock":      "electrifying live concert stage, dynamic lighting rigs, vintage rock poster vibe, raw energy",
    "hiphop_trap":    "urban nightscape with neon signs and rain reflections, gritty streets, dark dramatic shadows",
    "edm_festival":   "massive outdoor festival stage, colorful laser beams, enormous euphoric crowd, pyrotechnics",
    "city_pop":       "1980s retro Tokyo at night, neon sign reflections on wet streets, synthwave sunset, nostalgic",
    "latin_pop":      "tropical beach party at golden hour, vibrant festive colors, palm trees and ocean breeze",
    "lofi_jazz":      "cozy studio room with vinyl records and warm lamp glow, rainy window, mellow and relaxing",
    "children":       "magical colorful cartoon world, cute smiling animals, rainbow sky, bright joyful and playful",
    "meme_dance":     "neon-lit dance floor with confetti explosion, fun and energetic party vibe, social media aesthetic",
    "workout":        "high-intensity gym or stadium at dawn, dramatic backlit silhouette, fire and sweat, motivational",
    "sleep_calm":     "peaceful moonlit ocean or forest, soft ethereal glow, serene starry sky, deeply tranquil",
}

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
    배경 이미지 생성 → Whisper 자막 생성 → FFmpeg 합성 파이프라인.

    Args:
        concept : generate_daily_concept() 반환값
                  ('genre', 'mood', 'title', 'category_id' 키 사용)
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
def _to_ascii_title(title: str) -> str:
    """Non-ASCII 문자 제거 후 영문/숫자만 남김. 비어 있으면 'Music' 반환."""
    ascii_only = "".join(c for c in title if ord(c) < 128).strip()
    # 연속 공백 정리
    import re
    return re.sub(r"\s+", " ", ascii_only) or "Music"


def _to_ffmpeg_path(path: Path) -> str:
    """
    FFmpeg subtitles 필터용 경로 이스케이프.
    Windows 드라이브 문자 뒤의 콜론을 이스케이프 (C:/ → C\\:/).
    """
    p = str(path).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = p[0] + "\\:" + p[2:]
    return p


@with_retry()
def _generate_background(concept: dict, out_path: Path) -> Path:
    """
    DALL-E 3로 카테고리/장르에 어울리는 세로형 배경 이미지를 생성하고
    지정된 경로(out_path)에 저장.

    프롬프트: category_id 기반 _CATEGORY_VISUALS + mood + genre
    해상도: 1024x1792 (유튜브 쇼츠 9:16)
    """
    genre       = concept.get("genre", "music")
    mood        = concept.get("mood", "calm")
    category_id = concept.get("category_id", "")

    visual_style = _CATEGORY_VISUALS.get(
        category_id,
        "vibrant music artwork, cinematic lighting, professional quality"
    )

    prompt = (
        "Youtube Shorts vertical background image, no text, no watermark, no people faces. "
        f"Music genre: {genre}. Mood: {mood}. "
        f"Visual style: {visual_style}. "
        "High quality digital art, cinematic composition, 9:16 aspect ratio."
    )
    logger.info("DALL-E 3 이미지 생성 중... 카테고리: [%s]", category_id or "default")

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
    FFmpeg로 애니메이션 배경 + mp3를 합성하여 mp4 생성.

    배경 효과:
      - 110% 스케일업 후 sin/cos 표류 크롭 (Ken Burns-lite)
    레이어:
      - [하] 표류 배경
      - [상] 영문 제목 drawtext (상단, FONT_PATH가 있을 때만)
    """
    logger.info("FFmpeg 합성 시작: %s + %s → %s", bg_path.name, mp3_path.name, output_path.name)

    ascii_title   = _to_ascii_title(title)
    wrapped_title = textwrap.fill(ascii_title, width=16)
    safe_title    = _escape_drawtext(wrapped_title)
    audio_stream  = ffmpeg.input(str(mp3_path))

    # ── 배경: 110% 확대 후 사인파 표류 (Ken Burns-lite) ──────────────────
    video_bg = (
        ffmpeg.input(str(bg_path), loop=1, framerate=25)
        .filter("scale", _BIG_W, _BIG_H)
        .filter(
            "crop",
            _OUT_W, _OUT_H,
            f"{_DRIFT_X}+{_DRIFT_X // 2}*sin(t*0.08)",
            f"{_DRIFT_Y}+{_DRIFT_Y // 2}*cos(t*0.06)",
        )
    )

    # 영문 제목 오버레이 (폰트 없으면 건너뜀)
    if FONT_PATH:
        video_final = video_bg.filter(
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
            y="h*0.06",
            line_spacing=20,
        )
    else:
        video_final = video_bg

    output_kwargs = dict(
        vcodec="libx264",
        acodec="aac",
        audio_bitrate="192k",
        pix_fmt="yuv420p",
        shortest=None,
    )

    (
        ffmpeg.output(video_final, audio_stream, str(output_path), **output_kwargs)
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
    # 테스트용 더미 컨셉
    test_concept = {
        "genre":       "Chillhop, lo-fi jazz",
        "mood":        "fresh spring breeze",
        "title":       "Spring Breeze Chillhop",
        "category_id": "lofi_jazz",
    }

    # 지정된 오디오 경로로 단독 테스트
    test_mp3_path = DATA_DIR / "20260324_audio.mp3"
    print(f"[테스트] mp3: {test_mp3_path}")

    output = make_video(test_concept, test_mp3_path)
    print(f"\n[완료] 영상 저장 경로: {output}")
