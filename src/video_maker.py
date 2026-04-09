"""
video_maker.py
역할: FFmpeg로 동적 애니메이션(Ken Burns) 적용 이미지 + 음원 합성 → 유튜브 쇼츠용 mp4 출력
파이프라인 순서: llm_agent.py -> media_generator.py -> [video_maker.py] -> uploader.py

의존성 설치:
    pip install ffmpeg-python
    (+ 시스템에 FFmpeg 바이너리 설치 필요: https://ffmpeg.org/download.html)
"""

import os
import time
import random
import logging
import platform
import requests
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
    FONT_PATH = "C:/Windows/Fonts/malgun.ttf"
else:
    FONT_PATH = next(
        (p for p in (
            "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ) if Path(p).exists()),
        "",
    )
    if not FONT_PATH:
        logger.warning("No suitable font found — title overlay will be skipped")

FONT_SIZE    = 58          # 80 → 58: 한글 2줄이 1080px 너비에 여유있게 들어가는 크기
FONT_COLOR   = "white"
BORDER_W     = 4
BORDER_COLOR = "black"

# 영상 기준 텍스트 최대 너비 (1080px 기준, 폰트 58px 한글 1글자 ≈ 58px)
_CHARS_PER_LINE = 12       # 1줄 최대 12글자 (≈ 696px, 여백 충분)
_MAX_LINES      = 2        # 최대 2줄

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
def make_video(on_screen_text: str, audio_path: Path) -> Path:
    """
    오디오 주파수 시각화 + 중앙 텍스트 자막을 합성하여 mp4 비디오 생성 (이미지 없음).

    Args:
        on_screen_text: 영상 중앙에 들어갈 한국어 감성 자막 텍스트
        audio_path: 생성된 오디오 경로

    Returns:
        Path: 생성된 mp4 파일의 절대 경로 (data/output_shorts.mp4)
    """
    logger.info("=== 영상 제작 파이프라인 시작 ===")

    _compose_video(audio_path, on_screen_text, OUTPUT_PATH)

    logger.info("=== 영상 제작 완료: %s ===", OUTPUT_PATH)
    return OUTPUT_PATH


# ---------------------------------------------------------------------------
# 내부 헬퍼 함수
# ---------------------------------------------------------------------------
@with_retry()
def _compose_video(audio_path: Path, on_screen_text: str, output_path: Path) -> Path:
    """
    FFmpeg로 오디오 주파수 바(showfreqs) + 텍스트를 합성하여 mp4 생성.
    
    레이아웃 (세로 1920px 기준):
        - 0 ~ 1920px  : 검은색 배경
        - y=460 ~ 1460 : 대형 화면 중앙 오디오 주파수 바
        - y≈1620~1750 : 감성 텍스트
    """
    logger.info("FFmpeg 합성 시작 (오디오 단독 렌더링): %s → %s", audio_path.name, output_path.name)

    audio_in = ffmpeg.input(str(audio_path))

    _OUT_W, _OUT_H = 1080, 1920

    # ── 오디오 주파수 바 시각화 (화면 중앙에 큼직하게 배치) ──
    _VIS_W  = 1000
    _VIS_H  = 1000
    _VIS_X  = 40
    _VIS_Y  = 460

    # ── 다양한 시각화 연출을 위한 4가지 모드 랜덤 선택 ──
    vis_modes = ["freq_bar", "freq_line", "waves", "cqt"]
    vis_mode = random.choice(vis_modes)

    # 10단계 무지개 그라데이션
    rainbow_colors = "0xFF0000|0xFF7F00|0xFFFF00|0x7FFF00|0x00FF00|0x00FF7F|0x00FFFF|0x0000FF|0x7F00FF|0xFF00FF"

    if vis_mode == "freq_bar":
        # 1. 10색 무지개 막대 이퀄라이저
        visualizer = audio_in.filter(
            "showfreqs", size=f"{_VIS_W}x{_VIS_H}", rate=25, mode="bar", ascale="log",
            fscale=random.choice(["lin", "log"]), win_size=2048, averaging=2, colors=rainbow_colors
        )
    elif vis_mode == "freq_line":
        # 2. 부드러운 오션 웨이브풍 라인 스펙트럼
        visualizer = audio_in.filter(
            "showfreqs", size=f"{_VIS_W}x{_VIS_H}", rate=25, mode="line", ascale="log",
            fscale="log", win_size=2048, averaging=2, colors=rainbow_colors
        )
    elif vis_mode == "waves":
        # 3. 심장 박동 파형 스타일
        visualizer = audio_in.filter(
            "showwaves", size=f"{_VIS_W}x{_VIS_H}", rate=25, mode="cline",
            colors=rainbow_colors
        )
    else:  # cqt
        # 4. 음악 피아노 음계 컬러 스펙트럼
        visualizer = audio_in.filter(
            "showcqt", size=f"{_VIS_W}x{_VIS_H}", fps=25
        )

    # 시각화 화면을 검은 배경 위에 올리기 (크기 매칭)
    vis_padded = visualizer.filter("pad", _OUT_W, _OUT_H, x=_VIS_X, y=_VIS_Y, color="black")

    # 투명도 혼합이나 블렌드 없이 덮어씌움 (어차피 패딩외곽이 검정이므로 그대로 사용가능)
    video_with_vis = vis_padded

    # ── 텍스트 오버레이 (주파수 바 아래) ──
    video_final = video_with_vis
    if FONT_PATH and on_screen_text:
        lines_raw = on_screen_text.split('\n')
        lines = []
        for raw in lines_raw:
            while len(raw) > _CHARS_PER_LINE:
                lines.append(raw[:_CHARS_PER_LINE])
                raw = raw[_CHARS_PER_LINE:]
            if raw:
                lines.append(raw)
        lines = lines[:_MAX_LINES]

        num_lines    = len(lines)
        line_height  = FONT_SIZE + 16
        total_height = num_lines * line_height - 16

        # 텍스트: 주파수 바 하단(y≈1560)에서 40px 아래 시작
        block_top = _VIS_Y + _VIS_H + 40

        for i, line in enumerate(lines):
            safe_line = _escape_drawtext(line)
            y_pos = block_top + i * line_height
            video_final = video_final.filter(
                "drawtext",
                fontfile=FONT_PATH,
                text=safe_line,
                fontsize=FONT_SIZE,
                fontcolor=FONT_COLOR,
                borderw=BORDER_W,
                bordercolor=BORDER_COLOR,
                x="(w-text_w)/2",
                y=str(y_pos),
            )

    output_kwargs = dict(
        vcodec="libx264",
        acodec="aac",
        audio_bitrate="192k",
        pix_fmt="yuv420p",
        shortest=None,
    )

    (
        ffmpeg.output(video_final, audio_in, str(output_path), **output_kwargs)
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
