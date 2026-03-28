"""
_hosting.py
역할: Instagram/TikTok 업로드를 위한 GitHub Releases 임시 공개 URL 호스팅 공통 유틸리티
      (외부에서 직접 실행하지 않음, uploader_ig.py / uploader_tiktok.py에서 임포트)
"""

import os
import time
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env")


def _get_github_config() -> tuple[str, str]:
    github_repo  = os.getenv("GITHUB_VIDEO_REPO")
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_repo or not github_token:
        raise EnvironmentError(".env에 GITHUB_VIDEO_REPO, GITHUB_TOKEN이 설정되지 않았습니다.")
    return github_repo, github_token


def create_github_release(tag: str) -> int:
    """GitHub Releases에 임시 릴리즈를 생성하고 release_id 반환."""
    github_repo, github_token = _get_github_config()
    url = f"https://api.github.com/repos/{github_repo}/releases"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "tag_name":   tag,
        "name":       f"Video {tag}",
        "body":       "Auto-generated release for social media upload. Will be deleted after upload.",
        "draft":      False,
        "prerelease": True,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    release_id = resp.json()["id"]
    logger.info("GitHub 릴리즈 생성 완료. release_id: %d, tag: %s", release_id, tag)
    return release_id


def upload_asset_to_release(release_id: int, local_video_path: str) -> str:
    """릴리즈에 mp4 파일을 에셋으로 업로드하고 공개 다운로드 URL 반환."""
    github_repo, github_token = _get_github_config()
    filename = Path(local_video_path).name
    upload_url = f"https://uploads.github.com/repos/{github_repo}/releases/{release_id}/assets?name={filename}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Content-Type": "video/mp4",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    file_size_mb = os.path.getsize(local_video_path) / (1024 * 1024)
    logger.info("GitHub에 영상 업로드 중... (%.1fMB)", file_size_mb)
    with open(local_video_path, "rb") as f:
        resp = requests.post(upload_url, data=f, headers=headers, timeout=300)
    resp.raise_for_status()
    download_url = resp.json()["browser_download_url"]

    # GitHub release URL은 CDN으로 리다이렉트됨.
    # Instagram/TikTok API가 리다이렉트를 따라가지 않으므로 실제 CDN URL로 해석.
    cdn_resp = requests.head(download_url, allow_redirects=True, timeout=15)
    final_url = cdn_resp.url
    logger.info("GitHub 업로드 완료. CDN URL: %s", final_url)
    return final_url


def delete_github_release(release_id: int):
    """Instagram/TikTok 업로드 완료 후 GitHub 임시 릴리즈 삭제."""
    github_repo, github_token = _get_github_config()
    url = f"https://api.github.com/repos/{github_repo}/releases/{release_id}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        requests.delete(url, headers=headers, timeout=15)
        logger.info("GitHub 임시 릴리즈 삭제 완료. release_id: %d", release_id)
    except Exception as e:
        logger.warning("GitHub 릴리즈 삭제 실패 (수동 삭제 필요): %s", e)


def upload_to_public_url(local_video_path: str) -> tuple[str, int]:
    """로컬 mp4를 GitHub Releases에 업로드하고 (공개 URL, release_id) 반환."""
    tag = f"video-{int(time.time())}"
    release_id   = create_github_release(tag)
    download_url = upload_asset_to_release(release_id, local_video_path)
    return download_url, release_id
