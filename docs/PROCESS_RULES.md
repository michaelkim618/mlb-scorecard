# MLB Scorecard 프로세스 규칙서

> 최초 작성: 2026-08-27  
> 목적: 매일 자동 실행 파이프라인의 동작 원리와 문제 발생 시 대응 규칙 정리

---

## 1. 매일 자동 실행 파이프라인

### 전체 흐름

```
00:01 AM PST — GitHub Actions daily_init
  └── scorecard_pipeline.py → 당일 예측 초기화 (선발 TBD 상태로 시작 가능)

매 30분 (오전 6시~자정 PST) — GitHub Actions prediction 모드
  └── check_and_post.py
        ├── 라인업 확정 감지 → run_pipeline() 재실행 → predictions.json 갱신
        └── 포스팅 타이밍 도달 → Instagram / Twitter 자동 포스팅

12:30 AM PST — launchd 로컬 자동 실행 (mac 상시 켜져있을 때)
  └── python3 main.py
        ├── 과거 경기 결과 업데이트
        ├── 당일(내일) 예측 생성 + predictions.json 웹 동기화
        ├── season_results.json 갱신
        ├── 전날 블로그 포스트 자동 생성
        └── GitHub push (predictions + season + blog 한번에)

06:00 AM PST — GitHub Actions results 모드
  └── check_and_post.py --results
        ├── 전날 최종 결과 SNS 포스팅
        ├── season_results.json 업데이트
        └── 블로그 포스트 생성 (ANTHROPIC_API_KEY 필요)
```

---

## 2. 핵심 파일 역할

| 파일 | 위치 | 역할 |
|------|------|------|
| `predictions.json` | `mlb-scorecard-web/public/` | 홈페이지가 읽는 당일 예측 데이터 |
| `season_results.json` | `mlb-scorecard-web/public/` | 시즌 누적 적중률 데이터 |
| `blog/posts/*.json` | `mlb-scorecard-web/public/blog/` | 블로그 포스트 |
| `blog/index.json` | `mlb-scorecard-web/public/blog/` | 블로그 목록 인덱스 |
| `post_state_YYYY-MM-DD.json` | `mlb-scorecard-web/public/` | 당일 포스팅 상태 (중복 방지, 라인업 갱신 추적) |
| `output/predictions_YYYY-MM-DD.js` | `mlb-predictor/output/` | 파이프라인 원본 출력 |
| `output/predictions.json` | `mlb-predictor/output/` | 파이프라인 최신 JSON 출력 |

---

## 3. 홈페이지 주요 필드 매핑

GameCard.jsx가 읽는 `predictions.json` 필드:

| 웹 표시 항목 | predictions.json 필드 | 비고 |
|------|------|------|
| 선발 투수 이름 | `away_pitcher`, `home_pitcher` | |
| 선발 미확정 표시 | `sp_tbd.away`, `sp_tbd.home` | true면 "TBD" 표시 |
| 라인업 확정 배지 | `lineup_confirmed` | true면 "Line Up Confirmed" |
| 승률 바 | `win_prob.away`, `win_prob.home` | 숫자 (ex: 63, 37) |
| 모델 픽 | `model_winner` | 팀 풀네임 |
| 신뢰도 배지 | `win_prob` ≥ 63% → "High Confidence" | |
| SP↔BAT 충돌 배지 | `sp_bat_conflict` + `sp_bat_conflict_detail` | |
| Value Bet | `value_bet`, `edge` | |
| 경기 결과 | `actual_winner`, `model_correct` | 경기 후 업데이트 |

---

## 4. 라인업 갱신 프로세스 규칙

### 자동 갱신 조건
- `check_and_post.py`가 MLB API에서 라인업 확정 감지 시 자동으로 `scorecard_pipeline.py` 재실행
- 재실행 조건: 해당 경기 그룹의 `lineup_refreshed` 상태가 아직 없을 때

### 주의사항 ⚠️
- **`post_state_YYYY-MM-DD.json`이 웹 레포에 없으면** GitHub Actions 매 실행마다 상태 리셋
- 상태 리셋 시: 라인업 갱신 여부 추적 불가 → 중복 재실행 or 누락 가능
- GitHub Actions는 웹 레포 커밋 시 `post_state_*.json`도 함께 push (정상 작동 시 자동 유지)

### 라인업 갱신 미반영 시 수동 대응
```bash
# 즉시 재실행으로 최신 라인업 반영
cd "mlb-predictor"
python3 main.py
```
→ 최신 라인업으로 재예측 후 GitHub 자동 push

---

## 5. 자동화 구성 요소

### GitHub Actions (`daily_post.yml`)
- **위치**: `mlb-predictor/.github/workflows/daily_post.yml`
- **실행 주체**: GitHub 서버 (클라우드)
- **역할**: SNS 포스팅, 라인업 갱신, season_results, 블로그(results 모드)
- **인터넷 연결 불필요**: 클라우드에서 실행되므로 Mac 꺼져있어도 동작

### launchd 로컬 자동 실행
- **Plist**: `~/Library/LaunchAgents/com.mlbscorecard.daily.plist`
- **스크립트**: `~/bin/mlb_daily_runner.sh`
- **실행 시간**: 매일 12:30 AM PST
- **역할**: 예측 + season + blog + GitHub push 통합 실행
- **전제 조건**: Mac이 켜져있고 Google Drive 마운트 상태
- **로그**: `~/Library/Logs/mlb-scorecard/daily_runner.log`

### Full Disk Access 설정 (1회 필요)
- System Settings → Privacy & Security → Full Disk Access → `bash` 토글 ON
- **이유**: launchd가 Google Drive 경로 접근하려면 필요

---

## 6. 문제 발생 시 체크리스트

### 홈페이지 경기 예측이 없을 때
```
□ predictions.json 날짜 확인 (오늘 날짜인지)
□ python3 main.py 수동 실행
□ GitHub 최신 커밋 확인
```

### Season Accuracy가 안 업데이트될 때
```
□ python3 src/update_season_results.py 수동 실행
□ 결과: mlb-scorecard-web/public/season_results.json 갱신
□ git push로 배포
```

### 블로그가 업데이트 안 될 때
```
□ python3 src/generate_blog_post.py --date YYYY-MM-DD --web-repo ../mlb-scorecard-web
□ python3 src/generate_blog_post.py 실행 후 update_index() 호출 확인
□ git push로 배포
```

### 통합 수동 복구 (가장 빠른 방법)
```bash
cd "mlb-predictor"
python3 main.py
# → 예측 + season + blog + GitHub push 한번에 해결
```

### launchd 실패 시
```bash
# 로그 확인
tail -50 ~/Library/Logs/mlb-scorecard/daily_runner.log

# 재시작
launchctl unload ~/Library/LaunchAgents/com.mlbscorecard.daily.plist
launchctl load ~/Library/LaunchAgents/com.mlbscorecard.daily.plist

# 수동 테스트
bash ~/bin/mlb_daily_runner.sh
```

---

## 7. 알려진 제한사항

| 제한 | 내용 | 대응 |
|------|------|------|
| 서부 늦은 경기 | 12:30 AM 실행 시 일부 경기 아직 진행 중 가능 | 다음날 results 모드에서 최종 반영 |
| Mac 꺼져있을 때 | launchd 미실행 | GitHub Actions가 백업 역할 |
| Google Drive 마운트 안 됨 | launchd 스크립트 30초 대기 후 종료 | Mac 재부팅 or 수동 마운트 |
| GitHub Actions 지연 | GitHub 스케줄은 최대 수십 분 지연 가능 | 긴급 시 `workflow_dispatch`로 수동 실행 |
| ANTHROPIC_API_KEY | GitHub Secrets에 등록 필요 (블로그 생성용) | Secrets 확인 |

---

## 8. 주요 커맨드 레퍼런스

```bash
# 오늘 예측 + 전체 자동화 실행
python3 main.py

# 특정 날짜 예측
python3 main.py --date 2026-08-27

# 과거 결과만 업데이트
python3 main.py --update-results

# season_results만 업데이트
python3 src/update_season_results.py

# 블로그 포스트 생성
python3 src/generate_blog_post.py --date 2026-08-26 --web-repo ../mlb-scorecard-web

# launchd 로그 확인
tail -f ~/Library/Logs/mlb-scorecard/daily_runner.log

# GitHub Actions 수동 실행 (GitHub 웹 UI에서)
# Repository → Actions → MLB Scorecard Daily Auto Post → Run workflow
```
