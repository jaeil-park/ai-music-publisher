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
    FONT_PATH = "C:/Windows/Fonts/malgun.ttf"
else:
    FONT_PATH = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"

FONT_SIZE   = 80
FONT_COLOR  = "white"
BORDER_W    = 3
BORDER_COLOR = "black"

if not Path(FONT_PATH).exists():
    raise FileNotFoundError(f"폰트 파일을 찾을 수 없습니다: {FONT_PATH}\n해당 경로에 폰트가 설치되어 있는지 확인해주세요.")

# DALL-E 3 세로형 해상도 (유튜브 쇼츠 9:16 비율)
IMAGE_SIZE  = "1024x1792"

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

    # Whisper로 가사 자막 생성 (실패해도 영상 합성은 계속)
    srt_path = _transcribe_to_srt(mp3_path)

    _compose_video(BG_PATH, mp3_path, concept["title"], srt_path, OUTPUT_PATH)

    # SRT 임시 파일 정리
    if srt_path and srt_path.exists():
        srt_path.unlink(missing_ok=True)

    logger.info("=== 영상 제작 완료: %s ===", OUTPUT_PATH)
    return OUTPUT_PATH


# ---------------------------------------------------------------------------
# 내부 헬퍼 함수
# ---------------------------------------------------------------------------
def _transcribe_to_srt(mp3_path: Path) -> Path | None:
    """
    OpenAI Whisper API로 mp3 → SRT 자막 파일 생성.
    실패 시 None 반환 (자막 없이 계속 진행).
    """
    logger.info("Whisper 자막 생성 시작...")
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        with open(mp3_path, "rb") as f:
            srt_content = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="srt",
            )
        srt_path = mp3_path.with_suffix(".srt")
        srt_path.write_text(srt_content, encoding="utf-8")
        logger.info("자막 파일 생성 완료: %s", srt_path.name)
        return srt_path
    except Exception as e:
        logger.warning("Whisper 자막 생성 실패 (자막 없이 진행): %s", e)
        return None


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
def _compose_video(bg_path: Path, mp3_path: Path, title: str, srt_path: Path | None, output_path: Path) -> Path:
    """
    FFmpeg로 정적 이미지 + mp3를 합성하여 mp4 생성.

    - 오디오 길이에 맞춰 이미지를 루프
    - 제목은 상단 drawtext, Whisper SRT 자막은 하단 subtitles 필터로 오버레이
    - libass 미지원 환경에서는 자막 없이 폴백 합성
    - pix_fmt yuv420p: 유튜브/SNS 호환성 확보
    """
    logger.info("FFmpeg 합성 시작: %s + %s → %s", bg_path.name, mp3_path.name, output_path.name)

    wrapped_title = textwrap.fill(title, width=14)
    safe_title    = _escape_drawtext(wrapped_title)

    video_stream = ffmpeg.input(str(bg_path), loop=1, framerate=25)
    audio_stream = ffmpeg.input(str(mp3_path))

    # 제목은 상단 배치 (하단 자막 영역 확보)
    video_with_title = video_stream.filter(
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

    output_kwargs = dict(
        vcodec="libx264",
        acodec="aac",
        audio_bitrate="192k",
        pix_fmt="yuv420p",
        shortest=None,
    )

    # Whisper 자막 오버레이 시도 (libass 필요)
    if srt_path and srt_path.exists():
        try:
            video_with_subs = video_with_title.filter(
                "subtitles",
                _to_ffmpeg_path(srt_path),
                force_style=(
                    "FontName=Malgun Gothic,"
                    "FontSize=20,"
                    "Bold=1,"
                    "PrimaryColour=&H00FFFFFF,"
                    "OutlineColour=&H00000000,"
                    "Outline=2,"
                    "Alignment=2,"
                    "MarginV=80"
                ),
            )
            (
                ffmpeg.output(video_with_subs, audio_stream, str(output_path), **output_kwargs)
                .overwrite_output()
                .run(quiet=True)
            )
            logger.info("자막 포함 영상 합성 완료: %s (%.1f MB)", output_path.name, output_path.stat().st_size / (1024 * 1024))
            return output_path
        except ffmpeg.Error as e:
            err = e.stderr.decode(errors="ignore") if e.stderr else str(e)
            logger.warning("자막 필터 실패 (libass 미지원 가능). 자막 없이 재합성합니다.\n%s", err[:300])

    # 폴백: 자막 없이 합성
    (
        ffmpeg.output(video_with_title, audio_stream, str(output_path), **output_kwargs)
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
        "mood":        "청량한 봄",
        "title":       "청량한 봄 Chillhop",
        "category_id": "lofi_jazz",
    }

    # 지정된 오디오 경로로 단독 테스트
    test_mp3_path = DATA_DIR / "20260324_audio.mp3"
    print(f"[테스트] mp3: {test_mp3_path}")

    output = make_video(test_concept, test_mp3_path)
    print(f"\n[완료] 영상 저장 경로: {output}")
