"""
video_maker.py
역할: DALL-E 3로 배경 이미지 생성 → FFmpeg로 이미지+음원 합성 → 유튜브 쇼츠용 mp4 출력
파이프라인 순서: brain.py -> generator.py -> [video_maker.py] -> uploader.py

의존성 설치:
    pip install ffmpeg-python
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
def make_video(title: str, image_path: Path, audio_path: Path) -> Path:
    """
    이미지 + 음원 + 중앙 텍스트 자막을 합성하여 mp4 비디오 생성.

    Args:
        title: 영상 중앙에 들어갈 자막 텍스트
        image_path: 생성된 배경 이미지 경로 (9:16)
        audio_path: 생성된 오디오 경로

    Returns:
        Path: 생성된 mp4 파일의 절대 경로 (data/output_shorts.mp4)
    """
    logger.info("=== 영상 제작 파이프라인 시작 ===")

    _compose_video(image_path, audio_path, title, OUTPUT_PATH)

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
def _compose_video(bg_path: Path, audio_path: Path, title: str, output_path: Path) -> Path:
    """
    FFmpeg로 배경 이미지와 오디오를 합성하고 중앙에 텍스트를 오버레이하여 mp4 생성.
    """
    logger.info("FFmpeg 합성 시작: %s + %s → %s", bg_path.name, audio_path.name, output_path.name)

    ascii_title   = _to_ascii_title(title)
    wrapped_title = textwrap.fill(ascii_title, width=16)
    safe_title    = _escape_drawtext(wrapped_title)
    audio_stream  = ffmpeg.input(str(audio_path))

    # 배경 이미지 스트림 (루프, 프레임레이트, 9:16 비율로 스케일)
    video_bg = (
        ffmpeg.input(str(bg_path), loop=1, framerate=25)
        .filter("scale", w=1080, h=1920)
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
            y="(h-text_h)/2",
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
