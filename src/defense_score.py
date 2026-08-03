"""
수비 점수 계산 (100점 만점)
  - 선발투수 최근 5~6경기 ERA/WHIP         30%
  - 불펜 팀 단위 최근 10경기 승패/ERA      30%
  - 마무리 팀 단위 세이브 성공률/ERA       30%
  - 최근 10경기 평균 실점                  10%
"""
import json
from pathlib import Path
from mlb_stats_fetcher import (
    get_team_pitching_log,
    get_pitcher_gamelog,
    get_pitcher_season,
    safe_float,
)

_weights_path = Path(__file__).parent.parent / "config" / "weights.json"
_W = json.loads(_weights_path.read_text())["defense"]

# 정규화 기준 (낮을수록 좋음 → 역정규화)
_NORM = {
    "era_ref":    4.00,   # 리그 평균 ERA
    "whip_ref":   1.30,   # 리그 평균 WHIP
    "runs_ref":   4.5,    # 경기당 평균 실점
    "save_pct":   0.70,   # 마무리 세이브 성공률 기준
}


def _era_to_score(era: float, ref: float = _NORM["era_ref"]) -> float:
    """ERA가 낮을수록 점수 높음 → 0~100"""
    if era <= 0:
        return 100.0
    # ref 대비 절반이면 100점, 2배이면 0점
    ratio = era / ref
    score = (2.0 - ratio) / 1.0 * 50
    return max(0.0, min(score, 100.0))


def _whip_to_score(whip: float, ref: float = _NORM["whip_ref"]) -> float:
    """WHIP이 낮을수록 점수 높음 → 0~100"""
    if whip <= 0:
        return 100.0
    ratio = whip / ref
    score = (2.0 - ratio) / 1.0 * 50
    return max(0.0, min(score, 100.0))


def _calc_starter_score(pitcher_id: int) -> dict:
    """선발투수 최근 10경기 ERA/WHIP 점수"""
    if not pitcher_id:
        return {"score": 50.0, "era": None, "whip": None}

    logs = get_pitcher_gamelog(pitcher_id, limit=10)

    if not logs:
        season = get_pitcher_season(pitcher_id)
        era  = safe_float(season.get("era"),  4.50)
        whip = safe_float(season.get("whip"), 1.35)
        games = 0
    else:
        # 가중 평균: 이닝수 기준
        total_ip  = sum(safe_float(s.get("inningsPitched")) for s in logs)
        total_er  = sum(safe_float(s.get("earnedRuns") or safe_float(s.get("runs"))) for s in logs)
        total_bb  = sum(safe_float(s.get("baseOnBalls")) for s in logs)
        total_h   = sum(safe_float(s.get("hits")) for s in logs)
        era  = (total_er  / total_ip * 9) if total_ip > 0 else 4.50
        whip = ((total_bb + total_h) / total_ip) if total_ip > 0 else 1.35
        games = len(logs)

    era_s  = _era_to_score(era)
    whip_s = _whip_to_score(whip)
    score  = (era_s + whip_s) / 2

    return {
        "score":  round(score, 2),
        "era":    round(era, 2),
        "whip":   round(whip, 2),
        "games":  games,
    }


def _calc_bullpen_score(team_pitching_logs: list) -> dict:
    """
    팀 투구 게임로그로 불펜 성능 추정.
    팀 전체 ERA에서 선발 제외 방식 대신, 팀 ERA/승패로 간편 계산.
    """
    if not team_pitching_logs:
        return {"score": 50.0, "era": None, "wins": 0, "losses": 0}

    n  = len(team_pitching_logs)
    total_ip = sum(safe_float(s.get("inningsPitched")) for s in team_pitching_logs)
    total_er = sum(safe_float(s.get("earnedRuns") or safe_float(s.get("runs"))) for s in team_pitching_logs)
    wins     = sum(1 for s in team_pitching_logs if safe_float(s.get("wins")) > 0)
    losses   = sum(1 for s in team_pitching_logs if safe_float(s.get("losses")) > 0)

    era   = (total_er / total_ip * 9) if total_ip > 0 else 4.50
    win_pct = wins / n if n else 0.5

    era_s = _era_to_score(era)
    win_s = win_pct * 100

    score = (era_s * 0.6 + win_s * 0.4)

    return {
        "score":   round(score, 2),
        "era":     round(era, 2),
        "wins":    wins,
        "losses":  losses,
    }


def _calc_closer_score(team_pitching_logs: list) -> dict:
    """
    세이브 성공률/ERA 추정 (팀 투구 로그에서 세이브 데이터 활용).
    팀 로그에 saveOpportunities/saves가 없으면 saves/blownSaves로 대체.
    """
    if not team_pitching_logs:
        return {"score": 50.0, "save_pct": None}

    total_saves  = sum(safe_float(s.get("saves")) for s in team_pitching_logs)
    total_blown  = sum(safe_float(s.get("blownSaves")) for s in team_pitching_logs)
    total_opp    = total_saves + total_blown

    save_pct = (total_saves / total_opp) if total_opp > 0 else _NORM["save_pct"]

    total_ip = sum(safe_float(s.get("inningsPitched")) for s in team_pitching_logs)
    total_er = sum(safe_float(s.get("earnedRuns") or safe_float(s.get("runs"))) for s in team_pitching_logs)
    era      = (total_er / total_ip * 9) if total_ip > 0 else 4.50

    save_s = min(save_pct / _NORM["save_pct"], 1.0) * 100
    era_s  = _era_to_score(era)
    score  = (save_s * 0.6 + era_s * 0.4)

    return {
        "score":    round(score, 2),
        "save_pct": round(save_pct, 3),
        "era":      round(era, 2),
    }


def calc_defense_score(team_id: int, pitcher_id: int = None,
                       pitching_log: list = None) -> dict:
    """
    Returns:
        score      : 0~100 수비 점수
        components : 세부 기여값
        raw        : 원시 스탯
    """
    logs = pitching_log if pitching_log is not None else get_team_pitching_log(team_id, limit=10)

    starter  = _calc_starter_score(pitcher_id)
    bullpen  = _calc_bullpen_score(logs)
    closer   = _calc_closer_score(logs)

    # 최근 10경기 평균 실점
    n_games    = len(logs)
    total_runs = sum(safe_float(s.get("runs")) for s in logs)
    avg_allowed = (total_runs / n_games) if n_games else 4.5
    # 낮을수록 좋음 → 역정규화
    ra_score = max(0.0, min((_NORM["runs_ref"] * 2 - avg_allowed) / _NORM["runs_ref"] * 50, 100.0))

    score = (
        starter["score"]  * _W["starter_era_whip"]   +
        bullpen["score"]  * _W["bullpen_era_record"]  +
        closer["score"]   * _W["closer_save_era"]     +
        ra_score          * _W["runs_allowed_last10"]
    )

    return {
        "score": round(score, 2),
        "components": {
            "starter_score":  starter["score"],
            "bullpen_score":  bullpen["score"],
            "closer_score":   closer["score"],
            "ra_score":       round(ra_score, 2),
        },
        "raw": {
            "starter_era":   starter.get("era"),
            "starter_whip":  starter.get("whip"),
            "bullpen_era":   bullpen.get("era"),
            "closer_save_pct": closer.get("save_pct"),
            "avg_runs_allowed": round(avg_allowed, 2),
        },
    }


if __name__ == "__main__":
    print("=== HOU 수비 점수 (선발: Peter Lambert 663567) ===")
    r = calc_defense_score(117, pitcher_id=663567)
    print(f"  총점: {r['score']}")
    print(f"  세부: {r['components']}")
    print(f"  원시: {r['raw']}")
