"""
llm_agent.py
역할: OpenAI GPT-4o-mini API를 사용하여 숏폼 컨셉, 텍스트, 프롬프트를 JSON 형태로 기획합니다.
"""

import os
import json
import logging
import random
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

CATEGORY_WEIGHTS = {
    "걸그룹 K-POP (아이브/뉴진스 스타일)": 3,
    "보이그룹 K-POP (BTS/세븐틴 스타일)": 3,
    "K-POP 발라드 (드라마 OST 감성)": 3,
    "K-POP R&B (어반/그루비)": 3,
    "모던 트로트 (임영웅/영탁 스타일)": 1,
    "인디 어쿠스틱 (10cm/적재 감성)": 1,
    "인디 밴드 록 (DAY6/CNBLUE 스타일)": 1,
    "한국 힙합/트랩 (지코/기리보이 스타일)": 1,
    "EDM 빅룸 하우스 (페스티벌 트랙)": 1,
    "시티팝/레트로 80s (야마시타 타츠로 스타일)": 1,
    "라틴팝/레게톤 (댄서블한 리듬)": 1,
    "로파이 재즈 칠아웃 (공부/수면 음악)": 1,
    "어린이 동요/챈트 (유아 바이럴 훅)": 3,
    "틱톡 밈 댄스곡 (챌린지 바이럴)": 3,
    "헬스/운동 하이프 (BPM 140+ 동기부여)": 3,
    "힐링/수면 음악 (ASMR 감성)": 3,
}

def generate_daily_concept(ep_number: int) -> dict:
    logger.info("=== OpenAI API로 오늘의 숏폼 컨셉 기획 시작 ===")
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    random.seed(date.today().toordinal())
    
    categories = list(CATEGORY_WEIGHTS.keys())
    weights = list(CATEGORY_WEIGHTS.values())
    category = random.choices(categories, weights=weights, k=1)[0]
    
    logger.info("오늘의 선정 카테고리: %s", category)

    system_prompt = "당신은 숏폼 콘텐츠를 기획하는 전문 AI 디렉터입니다. 반드시 유효한 JSON 형식으로 응답하세요."
    user_prompt = f"""
    오늘의 테마는 '{category}' 입니다. 아래 JSON 스키마로 기획안을 작성해주세요.
    
    {{
      "title": "유튜브 쇼츠 영문 제목 (마지막에 #{ep_number} 포함)",
      "description": "유튜브 설명란에 들어갈 간략한 영문 곡 설명 (1~2 문장)",
      "lyrics": "생성될 노래의 전체 영문 가사 (Verse, Chorus, Bridge 등 구조 포함)",
      "tags": ["AImusic", "Shorts", "관련 태그 3개"],
      "on_screen_text": "영상 중앙에 띄울 짧고 감성적인 한국어 문구 (1~2줄)",
      "audio_prompt": "Stability Audio API용 영문 프롬프트 (2분 30초 길이의 곡. 보컬 특징, 악기, 분위기 쉼표 구분)",
      "image_prompt": "DALL-E 3 API용 영문 프롬프트 (9:16 비율 세로형 배경, 고화질 텍스트/워터마크 제외)"
    }}
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"}
    )

    return json.loads(response.choices[0].message.content)