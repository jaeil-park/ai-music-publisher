# AI Music Publisher

매일 자정 자동으로 AI 음악을 생성하고 YouTube Shorts / TikTok / Instagram Reels에 업로드하는 완전 무인 자동화 파이프라인.

## 파이프라인 구조

```
OpenAI GPT-4o-mini     → 음악 컨셉 기획 (장르, 가사, 제목, 설명)
       ↓
Suno API (chirp-crow)  → 음원 생성 및 mp3 다운로드
       ↓
OpenAI Whisper         → 음원 → SRT 자막 생성
OpenAI DALL-E 3        → 카테고리 맞춤 배경 이미지 생성
       ↓
FFmpeg                 → 이미지(Ken Burns 애니메이션) + 음원 + 자막 → mp4 합성
       ↓
YouTube Data API v3    → YouTube Shorts 업로드 (공개)
TikTok Content API     → TikTok 업로드 (비공개)
Instagram Graph API    → Instagram Reels 업로드
```

## 디렉토리 구조

```
ai-music-publisher/
├── main.py                  # 전체 파이프라인 엔트리 포인트
├── src/
│   ├── generator.py         # Suno 음원 생성 + OpenAI 컨셉 기획
│   ├── video_maker.py       # DALL-E 배경 생성 + FFmpeg 영상 합성
│   ├── uploader.py          # YouTube Data API 업로드
│   ├── uploader_tiktok.py   # TikTok Content Posting API 업로드
│   ├── uploader_ig.py       # Instagram Graph API 업로드
│   └── _hosting.py          # GitHub Releases 임시 공개 URL 유틸리티
├── scripts/
│   └── tiktok_auth.py       # TikTok OAuth PKCE 인증 스크립트
├── data/                    # 생성된 mp3, png, mp4 임시 저장 (gitignore)
│   └── archive/             # 업로드 완료된 파일 보관
├── .github/
│   └── workflows/
│       └── schedule.yml     # GitHub Actions 자동 실행 (매일 KST 9시/18시)
├── .env                     # API 키 및 인증 정보 (gitignore)
├── client_secrets.json      # YouTube OAuth 앱 인증 파일 (gitignore)
├── token.json               # YouTube OAuth 토큰 (gitignore)
└── requirements.txt
```

## 핵심 기능

### 음악 카테고리 풀 (가중치 랜덤 선택)
16개 카테고리를 날짜 기반 시드(`random.Random(날짜순번 × 2 + 슬롯)`)로 매일 다른 장르 생성:

| 카테고리 | 설명 |
|----------|------|
| 걸그룹 K-POP | 아이브/뉴진스/르세라핌 스타일 |
| 보이그룹 K-POP | BTS/세븐틴/스트레이키즈 스타일 |
| K-POP 발라드 | 드라마 OST 감성 |
| K-POP R&B | 어반/그루비 |
| 모던 트로트 | 임영웅·영탁 스타일 |
| 인디 어쿠스틱 | 10cm·적재 감성 |
| 인디 밴드 록 | DAY6·CNBLUE 스타일 |
| 한국 힙합/트랩 | 지코·기리보이 스타일 |
| EDM 빅룸 하우스 | 페스티벌 트랙 |
| 시티팝/레트로 80s | 야마시타 타츠로 스타일 |
| 라틴팝/레게톤 | 댄서블한 리듬 |
| 로파이 재즈 칠아웃 | 공부/수면 음악 |
| 어린이 동요/챈트 | 유아 바이럴 훅 |
| 틱톡 밈 댄스곡 | 챌린지 바이럴 |
| 헬스/운동 하이프 | BPM 140+ 동기부여 |
| 힐링/수면 음악 | ASMR 감성 |

### 영상 합성 특징
- **Ken Burns 애니메이션**: 배경 이미지를 110% 확대 후 sin/cos 표류 크롭으로 생동감 있는 배경 구현
- **Whisper 자막**: 음원을 자동 전사하여 가사 자막을 노래에 맞춰 하단에 오버레이
- **카테고리 맞춤 DALL-E 프롬프트**: 장르별 최적화된 시각 스타일로 배경 생성

### Suno 인증 자동화
수동으로 JWT를 복사할 필요 없이 `SUNO_COOKIE`의 `__client` 쿠키로 자동 갱신:
1. Clerk `GET /v1/client` → 세션 ID 확인
2. `POST /touch/{session_id}` → `sid` 클레임 포함 최신 JWT 자동 발급
3. `POST /api/c/check` → `session-id` 헤더 획득 후 생성 요청

`__client` 쿠키 만료: **약 1년** → 연 1회 쿠키 갱신만 필요

## 초기 설정

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

시스템 FFmpeg (libass 포함 버전) 설치 필요:
- **Windows**: gyan.dev/ffmpeg → `ffmpeg-git-full.7z` 다운로드 후 `bin` 폴더를 Path 환경변수에 추가

### 2. .env 설정

```env
# OpenAI
OPENAI_API_KEY=sk-...

# Stability AI (Audio & Image)
STABILITY_API_KEY=sk-...

# YouTube (client_secrets.json 별도 설정)
# (OAuth 인증은 최초 1회 브라우저 로그인으로 token.json 자동 저장)

# TikTok
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
TIKTOK_ACCESS_TOKEN=act....
TIKTOK_REFRESH_TOKEN=rft....
TIKTOK_OPEN_ID=...
TIKTOK_PRIVACY_LEVEL=SELF_ONLY   # SELF_ONLY | PUBLIC_TO_EVERYONE

# Instagram Graph API
IG_USER_ID=...
IG_ACCESS_TOKEN=IGAAf...
GITHUB_TOKEN=ghp_...              # 영상 임시 호스팅용
GITHUB_VIDEO_REPO=username/repo

# Discord 알림 (선택)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

### 3. YouTube 인증

```bash
python src/uploader.py   # 최초 실행 시 브라우저 로그인 → token.json 자동 저장
```

### 4. TikTok 인증

```bash
python scripts/tiktok_auth.py   # PKCE OAuth → .env에 토큰 자동 저장
```

## 실행

```bash
# 전체 파이프라인 실행
python main.py

# 모듈별 단독 테스트
python src/generator.py     # 컨셉 기획 + 음원 생성
python src/video_maker.py   # 영상 합성
python src/uploader.py      # YouTube 업로드
```

## GitHub Actions 자동화

`.github/workflows/schedule.yml`이 **매일 KST 9시, 18시**에 자동 실행됩니다.

### GitHub Secrets 설정 필요

| Secret 이름 | 내용 |
|-------------|------|
| `ENV_FILE` | `.env` 파일 전체 내용 |
| `CLIENT_SECRETS_JSON` | YouTube OAuth `client_secrets.json` |
| `TOKEN_JSON` | YouTube OAuth `token.json` |
| `GH_PAT` | GitHub Personal Access Token (Secrets 갱신용) |

파이프라인 실행 후 갱신된 `SUNO_COOKIE`가 `ENV_FILE` secret에 자동으로 덮어쓰여 세션이 유지됩니다.

## 사용 API 및 서비스

| 서비스 | 용도 | 비용 |
|--------|------|------|
| OpenAI GPT-4o-mini | 컨셉/가사 생성 | 유료 (소량) |
| OpenAI DALL-E 3 | 배경 이미지 생성 | 유료 (장당) |
| OpenAI Whisper | 음원 자막 전사 | 유료 (분당) |
| Suno API | 음원 생성 | 유료 구독 |
| YouTube Data API v3 | 영상 업로드 | 무료 (할당량 내) |
| TikTok Content Posting API | 영상 업로드 | 무료 |
| Instagram Graph API | 릴스 업로드 | 무료 |
| GitHub Releases | 영상 임시 호스팅 (IG용) | 무료 |

## 주의사항

- `.env`, `client_secrets.json`, `token.json`은 절대 커밋하지 마세요 (`.gitignore` 처리됨)
- Suno 계정에서 로그아웃하면 `SUNO_COOKIE` 재발급 필요
- TikTok `TIKTOK_PRIVACY_LEVEL=SELF_ONLY`로 설정 시 업로드된 영상은 본인만 확인 가능 (앱 심사 전 샌드박스 모드)
- Instagram Access Token은 60일 유효, 만료 전 갱신 필요
