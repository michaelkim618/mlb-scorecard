"""
공격 점수 계산 (100점 만점)
  - 최근 10경기 타율       40%
  - 최근 10경기 평균 득점  40%
  - 최근 10경기 홈런 합계  10%
  - 리그 순위              10%
"""
import json
from pathlib import Path
from mlb_stats_fetcher import get_team_hitting_log, get_league_standings, safe_float

_weights_path = Path(__file__).parent.parent / "config" / "weights.json"
_W = json.loads(_weights_path.read_text())["offense"]

# 정규화 기준값 (리그 평균 기준)
_NORM = {
    "avg":        0.260,   # 리그 평균 타율
    "runs":       4.5,     # 경기당 평균 득점
    "hr_per10":   10.0,    # 10경기 홈런 평균
    "rank_worst": 30,      # 최하위
}


def _normalize(val: float, ref: float, cap: float = 2.0) -> float:
    """val/ref 비율 → 0~1 클리핑 (cap=2.0 → 200% 이상은 1.0으로)"""
    ratio = val / ref if ref else 0.0
    return min(ratio / cap, 1.0)


def calc_offense_score(team_id: int, game_log: list = None) -> dict:
    """
    Returns:
        score      : 0~100 공격 점수
        components : 세부 기여값 dict
        raw        : 원시 스탯 dict
    """
    logs = game_log if game_log is not None else get_team_hitting_log(team_id, limit=10)

    if not logs:
        return {"score": 50.0, "components": {}, "raw": {}}

    # 최근 10경기 집계
    total_ab   = sum(safe_float(s.get("atBats")) for s in logs)
    total_hits = sum(safe_float(s.get("hits")) for s in logs)
    total_runs = sum(safe_float(s.get("runs")) for s in logs)
    total_hr   = sum(safe_float(s.get("homeRuns")) for s in logs)
    n_games    = len(logs)

    avg_avg    = (total_hits / total_ab) if total_ab else 0.0
    avg_runs   = total_runs / n_games if n_games else 0.0
    total_hr10 = total_hr  # 10경기 홈런 합계

    # 리그 순위 (1=최상, 30=최하)
    ranks  = get_league_standings()
    rank   = ranks.get(team_id, 15)
    # rank가 낮을수록 좋음 → 역정규화
    rank_score = ((_NORM["rank_worst"] - rank) / (_NORM["rank_worst"] - 1))

    # 각 항목 0~100 변환
    avg_score   = _normalize(avg_avg,    _NORM["avg"],      cap=1.5) * 100
    runs_score  = _normalize(avg_runs,   _NORM["runs"],     cap=2.0) * 100
    hr_score    = _normalize(total_hr10, _NORM["hr_per10"], cap=2.0) * 100
    rank_score  = max(0.0, min(rank_score * 100, 100.0))

    score = (
        avg_score  * _W["batting_avg_last10"]  +
        runs_score * _W["runs_per_game_last10"] +
        hr_score   * _W["hr_last10"]            +
        rank_score * _W["league_rank"]
    )

    return {
        "score": round(score, 2),
        "components": {
            "avg_score":  round(avg_score,  2),
            "runs_score": round(runs_score, 2),
            "hr_score":   round(hr_score,   2),
            "rank_score": round(rank_score, 2),
        },
        "raw": {
            "batting_avg":  round(avg_avg, 3),
            "avg_runs":     round(avg_runs, 2),
            "hr_last10":    int(total_hr10),
            "league_rank":  rank,
            "games_used":   n_games,
        },
    }


if __name__ == "__main__":
    print("=== HOU 공격 점수 ===")
    r = calc_offense_score(117)
    print(f"  총점: {r['score']}")
    print(f"  세부: {r['components']}")
    print(f"  원시: {r['raw']}")
