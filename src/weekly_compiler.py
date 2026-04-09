import os
import sys
import time
import uuid
import logging
import subprocess
from pathlib import Path
from datetime import datetime

from src.uploader import upload_to_youtube
from src.uploader_tiktok import upload_to_tiktok

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

def get_video_duration(filepath):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(filepath)
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return float(out.decode("utf-8").strip())
    except Exception as e:
        logger.error(f"Failed to get duration for {filepath}: {e}")
        return 0.0

def build_intro():
    logger.info("인트로 타이틀 영상 생성 중...")
    intro_path = DATA_DIR / "weekly_intro.mp4"
    text = "Weekly Best AI Music"
    vf_expr = f"drawtext=text='{text}':fontcolor=white:fontsize=80:x=(w-tw)/2:y=(h-th)/2:alpha='if(lt(t,0.5),0,if(lt(t,2.5),(t-0.5)/2,1))'"
    cmd = [
        "ffmpeg", "-y", 
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=3",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=3",
        "-vf", vf_expr,
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
        str(intro_path)
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.error(f"인트로 생성 실패: {e}")
    return intro_path

def apply_fades(input_path, output_path, duration, fade_dur=1.5):
    logger.info(f"페이드 효과 적용 중: {input_path.name}")
    fade_out_st = duration - fade_dur
    vf = f"fade=t=in:st=0:d={fade_dur},fade=t=out:st={fade_out_st}:d={fade_dur}"
    af = f"afade=t=in:st=0:d={fade_dur},afade=t=out:st={fade_out_st}:d={fade_dur}"
    
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", vf, "-af", af,
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
        "-video_track_timescale", "90000",
        str(output_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    logger.info("=== Weekly Bundle 파이프라인 시작 ===")
    
    DATA_DIR.mkdir(exist_ok=True)
    music_files = sorted([f for f in DATA_DIR.glob("music_*.mp4") if f.is_file()])
    
    if not music_files:
        logger.warning("병합할 영상 파일이 data/ 폴더에 존재하지 않습니다!")
        sys.exit(0)
    
    logger.info(f"총 {len(music_files)}개의 영상을 병합합니다.")
    
    intro_file = build_intro()
    processed_files = [str(intro_file)]
    
    for idx, fpath in enumerate(music_files):
        dur = get_video_duration(fpath)
        if dur > 0:
            out_path = DATA_DIR / f"faded_music_{idx}.mp4"
            apply_fades(fpath, out_path, dur)
            processed_files.append(str(out_path))
    
    list_path = DATA_DIR / "concat_list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for pf in processed_files:
            f.write(f"file '{Path(pf).name}'\n")
            
    bundle_path = DATA_DIR / "weekly_bundle.mp4"
    logger.info("최종 영상 병합 중...")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
        "-i", str(list_path), 
        "-c", "copy", str(bundle_path)
    ]
    subprocess.run(cmd, cwd=str(DATA_DIR), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.info(f"주간 병합 영상 생성 완료: {bundle_path}")
    
    # 업로드 로직
    title = f"AI Music Weekly Best 🎵 (Week {datetime.now().strftime('%U')})"
    desc = "한 주간 가장 사랑받은 AI 생성 음악들의 편안한 감성 모음집입니다.\n\n#AIMusic #AI작곡 #수면음악 #휴식"
    tags = ["AI음악", "모음집", "음악추천", "수면음악", "공부음악", "힐링"]
    
    try:
        y_vid = upload_to_youtube(str(bundle_path), title, desc, tags, privacy_status="public")
        logger.info(f"YouTube 업로드 성공: {y_vid}")
    except Exception as e:
        logger.error(f"YouTube 업로드 실패: {e}")
        
    try:
        t_id = upload_to_tiktok(str(bundle_path), title)
        logger.info(f"TikTok 업로드 성공: {t_id}")
    except Exception as e:
        logger.error(f"TikTok 업로드 실패: {e}")
        
    logger.info("=== Weekly Bundle 파이프라인 종료 ===")

if __name__ == "__main__":
    main()
