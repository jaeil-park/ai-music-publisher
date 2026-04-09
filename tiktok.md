# TikTok App Review (Audit) Process Summary

본 문서는 TikTok Content Posting API 승인을 위해 진행된 심사 준비 과정과 설정 내역을 정리한 리포트입니다.

## 1. 개요 (Objective)
- **목적**: 틱톡 앱 심사(Audit) 통과를 통해 `unaudited_client` 제한을 해제하고, 공개 영상(`PUBLIC_TO_EVERYONE`) 자동 업로드 권한을 획득함.
- **현재 상태**: **In Review** (2026-04-10 제출 완료)

## 2. 제품 및 권한 설정 (Products & Scopes)
- **활성화된 제품**: Login Kit, Content Posting API
- **요구된 스코프**:
  - `user.info.basic`: 사용자 프로필 확인
  - `video.publish`: 영상 게시 권한
  - `video.upload`: 영상 데이터 전송 권한

## 3. 리디렉션 주소 설정 (Redirect URIs)
로컬 테스트 환경과 심사 기준을 모두 만족시키기 위해 이중 설정함.
- **Desktop 탭**: `http://localhost:8080/callback` (로컬 인증 스크립트용)
- **Web 탭**: `https://jaeil-park.github.io/ai-music-publisher/` (심사용 공식 도메인 기반)

## 4. 공식 웹사이트 및 도메인 검증 (GitHub Pages)
틱톡은 소유권이 확인된 도메인에 법적 문서를 게시할 것을 요구함.
- **공식 URL**: [https://jaeil-park.github.io/ai-music-publisher/](https://jaeil-park.github.io/ai-music-publisher/)
- **검증 방식**: URL Prefix (Signature File)
- **검증 파일**: `tiktokM6ftwjAruD8jn2zTGuiJIjozcmzFwVG3.txt` (저장소 루트에 위치)
- **법적 문서**:
  - [Privacy Policy](https://jaeil-park.github.io/ai-music-publisher/privacy.html)
  - [Terms of Service](https://jaeil-park.github.io/ai-music-publisher/terms.html)

## 5. 심사 제출 정보 (Submission Details)
- **Submission Reason**: "Requesting audit to enable the automated 'Content Posting API' for my personal AI music project to publish daily generated music videos."
- **Demo Video**: 
  - 인증(Auth) -> 업로드 실행(Log) -> 틱톡 프로필 확인 과정을 담은 화면 녹화본 제출 필요.
  - (테스트 당시의 실제 구동 화면을 녹화하여 제출함)

## 6. 향후 조치 사항 (Next Steps)
1. **심사 승인 완료 시**:
   - `.env` 파일의 `TIKTOK_PRIVACY_LEVEL`을 `SELF_ONLY`에서 `PUBLIC_TO_EVERYONE`으로 변경.
   - 틱톡 계정 설정을 다시 **비공개**에서 **공개** 또는 **비즈니스** 계정으로 전환 가능.
2. **반려(Reject) 시**:
   - 틱톡이 제기한 반려 사유(주로 데모 영상 부실)를 확인하여 보완 후 재제출.

---
*Created by Antigravity AI Assistant*
