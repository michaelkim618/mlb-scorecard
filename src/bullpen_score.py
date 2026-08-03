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


BULLPEN_SCORE_CAP = 80.0  # 불펜 점수 상한 (NYM 82.6처럼 극단값 방지)

def bullpen_score(stats: dict) -> float:
    """불펜 ERA → 0~100 점수 (1.5ERA=100점, 6.5ERA=0점)
    상한 80점 캡 적용 — 불펜 과대평가 방지
    """
    era = stats.get("bullpen_era", 4.00)
    score = max(0.0, min(100.0, (6.5 - era) / 5.0 * 100.0))
    score = min(score, BULLPEN_SCORE_CAP)  # 상한 캡 적용
    return round(score, 1)


def _default_bullpen() -> dict:
    return {"bullpen_era": 4.00, "team_era": 4.00, "sample_games": 0}
