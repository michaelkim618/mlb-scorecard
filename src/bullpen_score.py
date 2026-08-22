"""
팀 불펜 점수 계산
get_team_pitching_log() 반환값을 분석
"""
from pitcher_recent_score import _ip_to_float


def analyze_bullpen(pitch_logs: list, starter_avg_ip: float = 5.5) -> dict:
    """
    팀 투구 게임로그에서 불펜 ERA 추출
    팀 전체 이닝 - 선발 추정 이닝 = 불펜 이닝
    """
    if not pitch_logs:
        return _default_bullpen()

    total_bp_ip = 0.0
    total_bp_er = 0.0
    total_ip    = 0.0
    total_er    = 0.0

    for s in pitch_logs:
        g_ip = _ip_to_float(s.get("inningsPitched", "0"))
        g_er = float(s.get("earnedRuns", 0) or 0)

        total_ip += g_ip
        total_er += g_er

        bp_ip = max(0.0, g_ip - starter_avg_ip)
        bp_er = g_er * (bp_ip / g_ip) if g_ip > 0 else 0.0

        total_bp_ip += bp_ip
        total_bp_er += bp_er

    bullpen_era = round(total_bp_er / total_bp_ip * 9, 2) if total_bp_ip > 0 else 4.00
    team_era    = round(total_er    / total_ip    * 9, 2) if total_ip    > 0 else 4.00

    return {
        "bullpen_era":  bullpen_era,
        "team_era":     team_era,
        "sample_games": len(pitch_logs),
    }


BULLPEN_SCORE_CAP = 80.0  # 불펜 점수 상한 (극단값 방지)

def bullpen_score(stats: dict) -> float:
    """
    불펜 점수 계산 (0~80점)

    v1: 시즌ERA 60% + 최근7일ERA 40% 혼합, 등판 횟수 기반 피로 페널티

    v2 개선 (2026-08-19):
    ┌─ A. recent_era 클램핑 ────────────────────────────────────────
    │   최근7일 ERA가 시즌ERA + 2.0 초과 시 클램프
    │   이유: 단기 이상 또는 소이닝 표본 왜곡 방지
    │   예) 시즌ERA 4.01인 팀의 최근7일ERA 9.35 → 6.01로 클램프
    │
    ├─ B. 가중치 동적 조정 (recent_ip 신뢰도 기반) ─────────────────
    │   최근7일 이닝이 충분할수록 recent_era 비중 상향
    │   recent_ip < 8이닝  → 시즌85% / 최근15% (극소 표본)
    │   recent_ip < 15이닝 → 시즌75% / 최근25% (중간 표본)
    │   recent_ip ≥ 15이닝 → 시즌70% / 최근30% (충분한 표본)
    │   ※ 기존 60%/40%에서 시즌 비중 강화 (과소평가 방지)
    │
    └─ C. 피로도 페널티: 투수 1인당 등판 횟수 기준 ─────────────────
        기존: 총 등판 횟수(7회+→-6pt, 5회+→-3pt) — 팀 규모 무시
        개선: avg_app = appearances / bp_count (인당 평균 등판)
          avg ≥ 3.0 → -5pt (심각: 인당 3회+, 로스터 총 동원)
          avg ≥ 2.5 → -3pt (중간)
          avg ≥ 2.0 → -1.5pt (경미)
          그 외   → 페널티 없음
    """
    season_era  = stats.get("bullpen_era",          4.00)
    recent_era  = stats.get("recent_era",            season_era)
    appearances = stats.get("recent_appearances",    0)
    recent_ip   = stats.get("recent_ip",             0.0)
    bp_count    = max(stats.get("bp_count",          5), 1)   # 0 방지

    # ── A. recent_era 클램핑 ─────────────────────────────────────
    # 시즌ERA 대비 2.0 초과 급등은 단기 이상/소이닝 왜곡으로 간주해 제한
    RECENT_ERA_CAP_DELTA = 2.0
    recent_era_adj = min(recent_era, season_era + RECENT_ERA_CAP_DELTA)

    # ── B. 가중치: recent_ip 신뢰도 동적 조정 ────────────────────
    if recent_ip < 8.0:
        w_season, w_recent = 0.85, 0.15   # 극소 표본: 시즌 ERA 위주
    elif recent_ip < 15.0:
        w_season, w_recent = 0.75, 0.25   # 중간 표본
    else:
        w_season, w_recent = 0.70, 0.30   # 충분한 표본

    season_s = max(0.0, min(100.0, (6.5 - season_era)     / 5.0 * 100.0))
    recent_s = max(0.0, min(100.0, (6.5 - recent_era_adj) / 5.0 * 100.0))

    score = season_s * w_season + recent_s * w_recent

    # ── C. 피로도 페널티: 투수 1인당 평균 등판 기준 ──────────────
    avg_app_per_pitcher = appearances / bp_count
    if avg_app_per_pitcher >= 3.0:
        score = max(0.0, score - 5.0)    # 심각 (로스터 총동원)
    elif avg_app_per_pitcher >= 2.5:
        score = max(0.0, score - 3.0)    # 중간
    elif avg_app_per_pitcher >= 2.0:
        score = max(0.0, score - 1.5)    # 경미

    # ── D. 클로저 별도 가중치 (20%) ──────────────────────────────
    closer_era = stats.get("closer_era")
    if closer_era is not None:
        closer_score = max(0.0, min(100.0, (6.5 - closer_era) / 5.0 * 100.0))
        score = score * 0.80 + closer_score * 0.20
        # 클로저 ERA가 나쁘면 추가 페널티
        if closer_era > 4.5:
            score = max(0.0, score - 2.0)

    score = min(score, BULLPEN_SCORE_CAP)
    return round(score, 1)


def _default_bullpen() -> dict:
    return {"bullpen_era": 4.00, "team_era": 4.00, "sample_games": 0}
