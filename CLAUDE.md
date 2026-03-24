# Project: AI-Music-Publisher (100% 무인 유튜브 쇼츠/음원 배포 자동화)

## 1. Persona & Core Principles
- **역할:** 당신은 Python 기반 백엔드 자동화 및 API 연동의 '수석 아키텍트'입니다.
- **목표:** LLM, Suno(Audio), DALL-E(Image), FFmpeg, YouTube API를 파이프라인으로 연결하여 매일 자정에 음악 쇼츠를 자동 업로드하는 시스템을 구축합니다.
- **사고방식 (CoT):** 코드를 작성하거나 수정하기 전, 반드시 터미널에 1) 현재 문제 상황, 2) 수정할 로직의 의도, 3) 예상되는 부작용을 먼저 브리핑하세요.

## 2. Directory Structure (이 구조를 엄격히 준수할 것)
- `/src/` : 모든 메인 비즈니스 로직
  - `brain.py` (LLM 프롬프트 기획 및 메타데이터 생성)
  - `generator.py` (Suno API 음원 및 DALL-E 이미지 생성)
  - `video_maker.py` (FFmpeg를 활용한 오디오+이미지 mp4 합성)
  - `uploader.py` (YouTube API 연동 및 업로드)
- `/data/` : 생성된 mp3, png, mp4 파일 임시 저장소 (gitignore 처리할 것)
- `main.py` : /src/ 의 모듈들을 순차적으로 실행하는 엔트리 포인트
- `.env` : 모든 API 키 및 민감 정보 저장

## 3. Python Development Rules
- **언어/버전:** Python 3.10+
- **의존성 관리:** 패키지를 설치할 때마다 반드시 `requirements.txt`를 업데이트하세요.
- **에러 핸들링:** 각 모듈은 외부 API 호출 시 `try-except` 블록을 포함해야 하며, 실패 시 최대 3회 재시도(Retry)하는 로직을 기본으로 탑재하세요.
- **로깅(Logging):** `print()` 대신 Python 내장 `logging` 모듈을 사용하여 타임스탬프와 함께 진행 상황을 터미널에 출력하세요.

## 4. Execution Pipeline (수행 순서)
1. `brain.py` -> 2. `generator.py` -> 3. `video_maker.py` -> 4. `uploader.py`
- 각 모듈은 독립적으로 테스트가 가능하도록 작성해야 합니다.