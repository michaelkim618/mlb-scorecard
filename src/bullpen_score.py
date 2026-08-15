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

    시즌 ERA 60% + 최근 7일 ERA 40% 혼합
    → 피로한 불펜(최근 ERA 급등)은 점수 하락
    → 쉬고 있는 불펜(최근 ERA 낮음)은 점수 상승

    추가 보정:
    - 최근 7일 5회 이상 등판: 피로 페널티 -3pt
    - 최근 7일 데이터 없음: 시즌 ERA만 사용
    """
    season_era = stats.get("bullpen_era", 4.00)
    recent_era = stats.get("recent_era",  season_era)  # 없으면 시즌 ERA
    appearances = stats.get("recent_appearances", 0)

    # 시즌 ERA 점수 (60%)
    season_s = max(0.0, min(100.0, (6.5 - season_era) / 5.0 * 100.0))
    # 최근 7일 ERA 점수 (40%)
    recent_s = max(0.0, min(100.0, (6.5 - recent_era) / 5.0 * 100.0))

    # 혼합
    score = season_s * 0.60 + recent_s * 0.40

    # 피로도 페널티: 7일 내 5회 이상 등판 → -3pt, 7회 이상 → -6pt
    if appearances >= 7:
        score = max(0.0, score - 6.0)
    elif appearances >= 5:
        score = max(0.0, score - 3.0)

    score = min(score, BULLPEN_SCORE_CAP)
    return round(score, 1)


def _default_bullpen() -> dict:
    return {"bullpen_era": 4.00, "team_era": 4.00, "sample_games": 0}
