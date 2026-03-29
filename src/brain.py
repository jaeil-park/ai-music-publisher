"""
brain.py
역할: Gemini 1.5 Flash (텍스트) API를 사용하여 오늘의 음악/영상 컨셉을 기획합니다.
      (과거 OpenAI의 역할을 대체합니다)
"""

import os
import json
import logging
import random
from datetime import date
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

CATEGORIES = [
    "걸그룹 K-POP (아이브/뉴진스 스타일)",
    "보이그룹 K-POP (BTS/세븐틴 스타일)",
    "K-POP 발라드 (드라마 OST 감성)",
    "K-POP R&B (어반/그루비)",
    "모던 트로트 (임영웅/영탁 스타일)",
    "인디 어쿠스틱 (10cm/적재 감성)",
    "인디 밴드 록 (DAY6/CNBLUE 스타일)",
    "한국 힙합/트랩 (지코/기리보이 스타일)",
    "EDM 빅룸 하우스 (페스티벌 트랙)",
    "시티팝/레트로 80s (야마시타 타츠로 스타일)",
    "라틴팝/레게톤 (댄서블한 리듬)",
    "로파이 재즈 칠아웃 (공부/수면 음악)",
    "어린이 동요/챈트 (유아 바이럴 훅)",
    "틱톡 밈 댄스곡 (챌린지 바이럴)",
    "헬스/운동 하이프 (BPM 140+ 동기부여)",
    "힐링/수면 음악 (ASMR 감성)",
]

def generate_daily_concept(ep_number: int) -> dict:
    logger.info("=== Gemini API로 오늘의 음악/영상 컨셉 기획 시작 ===")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

    # 날짜를 시드로 사용하여 매일 다른 카테고리 로테이션
    today_idx = date.today().toordinal()
    random.seed(today_idx)
    category = random.choice(CATEGORIES)
    logger.info("오늘의 선정 카테고리: %s", category)

    prompt = f"""
    당신은 숏폼 콘텐츠를 기획하는 전문 AI 디렉터입니다.
    오늘의 테마는 '{category}' 입니다. 이 테마에 맞춰 아래 JSON 스키마 구조로 기획안을 작성해주세요.
    
    {{
      "title": "유튜브 쇼츠에 올릴 영문 제목 (마지막에 #{ep_number} 포함)",
      "description": "유튜브 설명란에 들어갈 소개글과 해시태그 (영어)",
      "tags": ["AImusic", "Shorts", "관련 장르 태그 3개"],
      "audio_prompt": "Stability Audio AI용 프롬프트 (악기, 장르, 무드 위주의 쉼표 구분 영문. 보컬보다는 비트/악기 중심 묘사)",
      "image_prompt": "DALL-E 3 AI용 9:16 배경 이미지 영문 프롬프트 (풍경, 조명, 분위기 위주. 텍스트나 사람 얼굴 제외)"
    }}
    """

    response = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(response_mime_type="application/json")
    )

    try:
        concept = json.loads(response.text)
        return concept
    except json.JSONDecodeError as e:
        logger.error("JSON 파싱 에러: %s\n원본 응답: %s", e, response.text)
        raise