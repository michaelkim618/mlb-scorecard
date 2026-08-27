# MLB Scorecard 예측 모델 변경 이력

## 2026-08-15 세션 요약

---

## 1. SP 점수 공식 개선 (v3)

### 변경 파일: `src/pitcher_recent_score.py`

### 문제
- K/9(탈삼진)이 공식에 전혀 없어서 Burns(K/9=10.2) 같은 투수가 Alcantara보다 낮은 점수
- QS Rate 10% 과대 → pitch-count 관리 시대에 맞지 않음
- recent_bad_start가 직전 2경기 중 1개만 나빠도 페널티 → 너무 가혹

### 수정 내용

| 항목 | 이전 | 이후 |
|------|------|------|
| 공식 | ERA 45% + WHIP 35% + IP 10% + QS 10% | ERA 40% + WHIP 30% + **K/9 20%** + IP 5% + QS 5% |
| recent_bad_start | 직전 2경기 중 1개라도 ERA≥6.0 | **직전 1경기만** ERA≥6.0 체크 |
| W-L 승률 | 미반영 | 75%↑ +3pt / 60%↑ +1.5pt / 40%↓ -2pt / 35%↓ -3pt (최소 5결정) |

### 검증 결과 (8/14 MIA@CIN)
- Burns (CIN): 48pt → **69pt** (+21pt)
- Alcantara (MIA): 72pt → **53pt** (-19pt)
- 실제 경기: CIN 승 → 예측 방향 일치 ✅

### SP 점수 상한선 72pt 유지 결정
- 80pt로 올리면 SP 의존도 증가 → 당일 컨디션 변수를 더 못 잡음
- Boyd(CHC) 8/15 경기: trend=hot, 65.5pt인데 5이닝 7실점 → 상한선이 오히려 보수적 역할
- **결론: 72pt 상한 그대로 유지**

---

## 2. 불펜 점수 개선 — 최근 7일 ERA 반영

### 변경 파일: `src/mlb_stats_fetcher.py`, `src/bullpen_score.py`, `src/scorecard_pipeline.py`

### 문제
- 시즌 ERA만 사용 → 피로한 불펜과 쉬고 있는 불펜을 구분 못함
- 예: BOS 불펜 시즌 ERA 3.06(우수)이지만 최근 7일 ERA 5.17(부진) → 구분 불가

### 수정 내용

**`get_bullpen_era_direct()`에 최근 7일 데이터 추가:**
- 각 불펜 투수의 최근 7일 게임로그 집계
- `recent_era`: 최근 7일 ERA (이닝 2.0 미만 시 시즌 ERA로 fallback)
- `recent_appearances`: 7일 내 등판 횟수 (피로도 지표)

**`bullpen_score()` 새 공식:**
```
score = 시즌ERA점수 × 60% + 최근7일ERA점수 × 40%
피로 페널티: 7일 내 5회↑ → -3pt / 7회↑ → -6pt
상한: 80pt
```

### 8/15 적용 예시 (주요 변화)

| 팀 | 시즌 ERA | 최근7일 ERA | 점수 변화 |
|----|---------|-----------|---------|
| NYM | 3.93 | **6.43** 🔴 | 51pt → 25pt |
| BAL | 4.13 | **7.65** 🔴 | 47pt → 22pt |
| BOS | 3.06 | **5.17** 🔴 | 68pt → 46pt |
| SD  | 2.67 | **0.86** 🟢 | → 80pt |
| ARI | 2.74 | **1.29** 🟢 | → 79pt |
| CHC | 3.18 | **1.93** 🟢 | → 70pt |

### 남은 한계
- 개별 투수 연속 등판 여부는 미반영 (팀 전체 합산)
- 당일 경기 데이터는 경기 종료 후 다음 파이프라인 실행 시 반영
- **다음 작업: BP 마무리 투수(클로저) 별도 가중치 추가 (3번)**

---

## 3. 타선 데이터 캐시 시스템

### 변경 파일: `src/mlb_stats_fetcher.py`

### 문제
- MLB API(`statsapi.mlb.com`) 타임아웃 시 타선 데이터 전체가 기본값으로 대체
- 8/15 KC@LAA, TEX@OAK 경기가 API 장애로 예측 불가

### 수정 내용

**방향 1 — 캐시 레이어 (`output/cache/`)**
- API 성공 시 → `output/cache/hit_split_{team}_{date}.json` 저장
- API 실패 시 → 최근 3일 내 캐시 파일 자동 로드 (`[캐시 fallback]` 로그)

**방향 3 — 시즌 DB (`output/team_batting_season.json`)**
- 캐시도 없을 때 최후 fallback
- 팀별 최근 30경기 평균 + split_snapshot 저장
- 매 파이프라인 실행 시 자동 갱신

**적용 대상:**
- `get_team_hitting_log()`: 캐시 지원
- `get_team_hitting_log_split()`: 캐시 + 시즌 DB 지원
- `get_team_hitting_season()`: 캐시 지원

---

## 4. MLB API 헬스체크 자동화

### 변경 파일: `.github/workflows/api_health_check.yml`

- **실행 시간**: 매일 08:00 PST/PDT 자동 실행
- **체크 엔드포인트**: schedule, teams, people (3개)
- **결과 저장**: `mlb-scorecard-web/public/api_health.json`
- **알림**: API 이상 시 GitHub Actions 실패 → 자동 이메일 알림
- **수동 실행**: GitHub Actions → workflow_dispatch

---

## 5. DST 버그 수정

### 변경 파일: `.github/workflows/daily_post.yml`

### 문제
- results 모드(블로그 생성)가 여름(PDT) 동안 한 번도 실행 안 됨
- `0 14 * * *` = 6AM PST(겨울) = 7AM PDT(여름) → PST_HOUR 체크에서 "06" 실패

### 수정
- 13:00 UTC 크론 추가 (`06:00 PDT` 여름 대응)
- `30 13 * * *`도 추가 (6:30 PDT)
- 결과: 8/9 이후 블로그 자동 생성 재개

---

## 6. Toss-Up 경고 배지

### 변경 파일: `src/components/GameCard.jsx` (mlb-scorecard-web)

- 예측 확률 < 55% 경기에 ⚠️ **TOSS-UP** 배지 표시
- 회색 상단 배너 + 카드 좌측 테두리 회색으로 변경
- 역사적 정확도 42.9% (무작위보다 낮음) → 베팅 주의 경고

---

## 7. Pitcher Penalty 업데이트

### 변경 파일: `config/pitcher_penalties.json`

- **Cade Cavalli 제거**: 8/13 CIN전 완봉승 (ERA 3.0, K/9 10.0)
- 현재 페널티 목록: 8명 (Pérez, Carrasco, Brazobán, Sullivan, Weathers, Waldrep, López, Elder)

---

---

## 2026-08-19 세션 요약

---

## SP 점수 v4 (`pitcher_recent_score.py`)

| 개선 항목 | 내용 |
|-----------|------|
| avg_ip 기반 hot 보너스 감쇠 | 4이닝 미만 → 30%, 5이닝 미만 → 70%, 5.5이닝 미만 → 90% 적용 |
| 짧은 등판 페널티 | avg_ip < 4.0 → -4pt, < 5.0 → -2pt |
| QS율 상한 캡 | QS < 33% → max 50pt, QS < 50% → max 56pt |
| W-L 페널티 확장 | 기존 ≤0.40 외에 ≤0.48 구간 추가 (-1.0pt) |

## 불펜 점수 v2 (`bullpen_score.py`)

| 개선 항목 | 내용 |
|-----------|------|
| recent_era 클램핑 | 시즌ERA + 2.0 초과 시 제한 (소이닝 왜곡 방지) |
| 가중치 동적 조정 | recent_ip < 8이닝 → 85/15, < 15이닝 → 75/25, ≥15이닝 → 70/30 |
| 피로 페널티 개선 | 총 등판 수 → 투수 1인당 평균 등판 기준으로 변경 |

---

## 2026-08-20 세션 요약

---

## 선발투수 아스날 표시 기능 (`pitcher_recent_score.py`, `scorecard_pipeline.py`, `GameCard.jsx`)

- `get_pitcher_arsenal()` 함수 추가: MLB Stats API `pitchArsenal` 엔드포인트로 나이·구속·구종 수집
- `sp_detail`에 `age`, `fb_velo`, `secondary_pitches` 자동 머지
- 홈페이지 투수 이름 아래 인라인 표시: `Age 27 · 95.9 mph (FB) · SW / CB`

## 블로그 자동화 복구 (`~/bin/mlb_blog_runner.sh`, `generate_blog_post.py`)

- macOS launchd TCC 보안으로 Google Drive 경로 스크립트 실행 차단 → 로컬 래퍼 생성
- `model_correct=None` 시 `season_results.json`에서 fallback하는 `_patch_model_correct_from_season_results()` 추가
- 8/16~8/19 누락 블로그 4일치 소급 생성 배포

---

## 2026-08-21 세션 요약

---

## SP 점수 v5 — 패스트볼 구속 페널티 (`pitcher_recent_score.py`)

### 배경
P. Lambert (HOU) 8/20 LAA전 3.2IP 9실점 붕괴 분석에서 발견.
커리어 ERA 5.41이 보여주듯 구속 열세(~91 mph)가 구조적 약점임을 확인.
현대 MLB에서 92 mph 미만 패스트볼은 타자 타이밍 적응이 빠르고 실점 리스크가 높음.

### 적용 기준

| 패스트볼 구속 | 페널티 | 판단 근거 |
|--------------|--------|---------|
| ≥ 92.0 mph | 없음 | 정상 구위 |
| < 92.0 mph | **-3pt** | 기준 미달 — 타자 타이밍 잡기 쉬움 |
| < 90.0 mph | **-4pt** | 저속 — 커맨드 흔들리면 즉시 대량 실점 |
| < 88.0 mph | **-6pt** | 극저속 — 완전 무브먼트/커맨드 의존, 고위험 |
| 데이터 없음 | 없음 | 미적용 (안전 처리) |

### 구현 방법
- `pitcher_score()` 내부에서 `stats.get("fb_velo")` 직접 읽음
- `get_pitcher_arsenal()`이 `sp_detail`에 이미 머지돼 있어 파이프라인 별도 수정 불필요
- 파이프라인 로그에 `[저속91.5mph(-3pt)]` 태그 자동 표시

### 검증 시뮬레이션 (ERA 3.68, stable 기준)

| 구속 | 점수 |
|------|------|
| 93.0 mph | 56.0pt |
| 91.5 mph | 54.9pt (-3pt) |
| 89.5 mph | 53.9pt (-4pt) |
| 87.5 mph | 51.9pt (-6pt) |

### 커밋
`5dcf8ac` — feat: SP 점수 v5 — 패스트볼 구속 92 mph 미만 페널티 추가

---

## 현재 모델 가중치 구조 (v5 기준)

```
SP  30% — ERA 40% + WHIP 30% + K/9 20% + IP 5% + QS 5% (상한 72pt)
           ├── Hot 보너스: +1~3pt (샘플·avg_ip 감쇠)
           ├── Cold 페널티: -8pt
           ├── avg_ip < 5.0 → -2pt, < 4.0 → -4pt
           ├── QS < 33% → max 50pt, QS < 50% → max 56pt
           ├── recent_bad_start → -5~7pt
           ├── 단기휴식 → -5pt, 장기휴식 → -2pt
           ├── W-L 승률 보너스/페널티 (±1~3pt)
           └── [v5] fb_velo < 92 → -3pt, < 90 → -4pt, < 88 → -6pt

BP  20% — 시즌ERA × 동적가중치 + 최근7일ERA × 동적가중치 + 피로 페널티 (상한 80pt)
           ├── recent_ip < 8 → 85/15, < 15 → 75/25, ≥15 → 70/30
           └── recent_era 클램핑: 시즌ERA + 2.0 초과 시 제한

타선 35% — OPS, AVG, 득점, 홈/원정 split + 캐시 fallback 시스템

상황 15% — 연승/연패, 홈/원정 승률, 부상
```

---

## 다음 작업 후보

| 우선순위 | 작업 | 예상 효과 |
|---------|------|---------|
| 1 | BP 클로저 별도 가중치 | 접전 경기 정확도 향상 |
| 2 | 개별 불펜 투수 연속 등판 체크 | 피로한 핵심 셋업맨 탐지 |
| 3 | SP 샘플 신뢰도 UI 표시 | 루키/복귀 투수 과대평가 방지 |
| 4 | 구속 저하 트렌드 감지 | 부상 징후 투수 사전 탐지 |

---

## 2026-08-21 세션 요약 (야간)

---

## 8/21 경기 결과 분석 → 5대 모델 개선 (v6)

### 8/21 예측 성적: 12W-3L (80.0%)

| 틀린 경기 | 예측 | 실제 | 분석 |
|-----------|------|------|------|
| OAK @ HOU | OAK 52% | HOU 4-0 | 박빙 원정픽, Kalshi HOU 62% 무시 |
| LAA @ TEX | LAA 52% | TEX 2-1 | 박빙 원정픽, 홈이점 과소평가 |
| CHC @ SEA | CHC 61% | SEA 6-5 | 주전 부상자 미반영, 홈이점 과소평가 |

---

## 1. 홈팀 박빙 보정 (`scorecard_pipeline.py`)

- 원정팀 픽 확률이 50~55% 구간일 때 → -2.5%p 보정 (최소 50% 유지)
- 50% 미만으로 내려가면 홈팀으로 픽 전환
- 로그: `[🏠 박빙홈보정] 원정픽 XX% → 홈이점 -2.5% → 결과`

## 2. 부상자 핵심도 구분 (`injury_check.py`, `mlb_stats_fetcher.py`)

- 기존: IL 명단 텍스트 표시만 (점수 미반영)
- 개선: 타자 주전(Infielder/Outfielder) IL 인원수 기반 타선 페널티 적용
  - 1명: -1.5pt / 2명: -3.0pt / 3명 이상: -4.5pt (상한)
  - 투수 부상은 SP/BP 점수에 이미 반영 → 제외
- 신규 함수: `get_injured_players_detail()`, `get_injury_penalty()`

## 3. Kalshi 괴리 보정 (`scorecard_pipeline.py`)

- |edge| ≥ 15%p → 모델을 Kalshi 방향으로 25% 당김
  - 예: model=52%, kalshi=67% → gap=15, 조정 +3.75%p
- 로그: `[🔄 Kalshi보정] edge +/-XX.X%p ≥ 15 → 보정`

## 4. BP 클로저 별도 가중치 (`bullpen_score.py`, `mlb_stats_fetcher.py`)

- 마무리 투수 식별: 세이브 기회 최다 투수 (최소 3회 이상)
- 클로저 ERA를 불펜 점수에 20% 반영: `score = score*0.80 + closer_score*0.20`
- 클로저 ERA > 4.5 → 추가 -2.0pt 페널티
- 반환 필드: `closer_era`, `closer_name`

## 5. Value Bet 기준 정교화 (`value_bet.py`)

- 박빙 경기(max_model_pct < 55%) → Value Bet 자동 패스
- lineup_confirmed=False → 라인업 미확정 패스 (evaluate 내부 통합)
- edge 등급 세분화:
  - 8~14%p: `✅ Value Bet 후보`
  - 15~24%p: `🔥 Strong Value Bet`
  - 25%p 이상: `🚨 극단 Edge (마켓 이상 의심)`

### 커밋: `676c569`


---

# 2026-08-26 ~ 2026-08-27 업데이트

## 1. SP vs BAT 충돌 감지 플래그 (`scorecard_pipeline.py`, `GameCard.jsx`)

### 배경
- 8/26 오답 분석 결과, SP 점수 우위 팀 ≠ BAT 점수 우위 팀인 경기에서 예측 신뢰도 저하 확인
- DET vs TB: SP 우위 DET(+39.6pt) ↔ BAT 우위 TB(+10pt) → 실제 TB 승리 → 모델 오답

### 구현
**파이프라인 (스텝 9.5)**
- 조건: `abs(sp_gap) ≥ 10pt AND abs(bat_gap) ≥ 8pt AND sp/bat가 다른 팀 가리킴`
- 출력 JSON 신규 필드:
  ```json
  "sp_bat_conflict": true,
  "sp_bat_conflict_detail": {
    "sp_favors": "Detroit Tigers",
    "bat_favors": "Tampa Bay Rays",
    "sp_gap": -39.6,
    "bat_gap": 10.0
  }
  ```
- 파이프라인 로그: `[⚠️ SP↔BAT 충돌] SP우위=XXX(+Xpt) ↔ BAT우위=YYY(+Ypt) → 예측 신뢰도 주의`

**웹사이트 (GameCard.jsx)**
- `sp_bat_conflict: true`인 경기에 `⚡ SP↔BAT Conflict — Lower Confidence` 주황색 배지 표시
- 마우스 오버 시 SP/BAT 우위 팀과 점수 차이 툴팁 제공

---

## 2. 웹사이트 자동 배포 자동화 (`scorecard_pipeline.py`, `main.py`) — 2026-08-27

### 문제
- `scorecard_pipeline.py` 실행 후 `mlb-predictor/output/predictions_YYYY-MM-DD.js`는 생성되지만
- 웹사이트가 읽는 `mlb-scorecard-web/public/predictions.json`이 자동 업데이트되지 않아
- 매일 수동으로 복사 + GitHub push 해야 하는 상황이 반복됨

### 근본 원인
파이프라인(`scorecard_pipeline.py`)과 웹사이트 repo(`mlb-scorecard-web`)가 별개로 운영되는데,
두 경로를 연결하는 자동 동기화 로직이 없었음

### 해결책

**`scorecard_pipeline.py` — 저장 직후 웹싱크**
```python
WEB_PUBLIC_DIRS = [
    Path(__file__).parent.parent.parent / "mlb-scorecard-web" / "public",
    Path(__file__).parent.parent.parent / "mlb-scorecard-web" / "dist",
]
for web_dir in WEB_PUBLIC_DIRS:
    target = web_dir / "predictions.json"
    if web_dir.exists():
        target.write_text(json_payload, encoding="utf-8")
```
→ 파이프라인 실행 시마다 `public/predictions.json` 자동 덮어쓰기

**`main.py` — `cmd_deploy_web()` 신규 함수**
- `scorecard_pipeline.run()` 완료 후 자동 호출
- 동작: `git stash → pull rebase → stash pop → add → commit → push`
- 에러 처리: "nothing to commit", "No stash entries" 등 정상 케이스 구분

### 적용 후 플로우
```
python3 main.py
  ↓
scorecard_pipeline.run()
  ├── output/predictions_YYYY-MM-DD.js  저장
  ├── output/predictions.json           저장
  ├── mlb-scorecard-web/public/predictions.json  ← 자동 동기화 (NEW)
  └── mlb-scorecard-web/dist/predictions.json    ← 자동 동기화 (NEW)
  ↓
cmd_deploy_web()  ← 자동 호출 (NEW)
  └── GitHub push → 사이트 자동 배포
```

### 파일 변경
| 파일 | 변경 내용 |
|------|-----------|
| `src/scorecard_pipeline.py` | 저장 후 웹 public 폴더 자동 동기화 추가 |
| `main.py` | `cmd_deploy_web()` 함수 추가, `cmd_predict_scorecard()` 에서 자동 호출 |

