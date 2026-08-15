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

## 현재 모델 가중치 구조

```
SP  30% — ERA 40% + WHIP 30% + K/9 20% + IP 5% + QS 5% (상한 72pt)
BP  20% — 시즌ERA 60% + 최근7일ERA 40% + 피로 페널티 (상한 80pt)
타선 35% — OPS, AVG, 득점, 홈/원정 split
상황 15% — 연승/연패, 홈/원정 승률, 부상
```

---

## 다음 작업 후보

| 우선순위 | 작업 | 예상 효과 |
|---------|------|---------|
| 1 | BP 클로저 별도 가중치 | 접전 경기 정확도 향상 |
| 2 | 개별 불펜 투수 연속 등판 체크 | 피로한 핵심 셋업맨 탐지 |
| 3 | SP 샘플 신뢰도 UI 표시 | 루키/복귀 투수 과대평가 방지 |
