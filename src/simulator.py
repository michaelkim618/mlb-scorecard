"""
Monte Carlo 시뮬레이션 (포아송 분포 기반, 10,000회)
공격점수 vs 상대 수비점수 → 예상 득점 λ → 포아송 시뮬레이션
"""
import json
import math
import random
from pathlib import Path

_cfg_path = Path(__file__).parent.parent / "config" / "weights.json"
_CFG = json.loads(_cfg_path.read_text())


def _score_to_lambda(offense: float, defense: float,
                     scale: float = None, home_bonus: float = 0.0) -> float:
    """
    offense, defense: 0~100 점수
    λ = scale * (offense/100) * (1 - defense/200) * (1 + home_bonus)

    defense 100점 → 상대 λ에 최대 50% 감소 효과 (defense/200)
    """
    scale = scale if scale is not None else _CFG["simulation"]["score_scale"]
    net = (offense / 100) * (1.0 - defense / 200.0)
    lam = scale * net * (1.0 + home_bonus)
    return max(lam, 0.1)


def _poisson_sample(lam: float) -> int:
    """numpy 없이 Donald Knuth 알고리즘으로 포아송 샘플"""
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= random.random()
    return k - 1


def simulate(
    away_offense: float, away_defense: float,
    home_offense: float, home_defense: float,
    iterations: int = None,
    home_bonus: float = None,
    scale: float = None,
    seed: int = 42,
) -> dict:
    """
    Returns:
        away_win_pct  : float (0~100)
        home_win_pct  : float (0~100)
        expected_away : float (예상 득점)
        expected_home : float (예상 득점)
        tie_pct       : float (무승부 — MLB에선 연장 가능성)
    """
    if iterations is None:
        iterations = _CFG["simulation"]["iterations"]
    if home_bonus is None:
        home_bonus = _CFG["home_bonus"]
    if scale is None:
        scale = _CFG["simulation"]["score_scale"]

    random.seed(seed)

    # 각 팀의 λ: 내 공격 vs 상대 수비
    lam_away = _score_to_lambda(away_offense, home_defense, scale=scale, home_bonus=0.0)
    lam_home = _score_to_lambda(home_offense, away_defense, scale=scale, home_bonus=home_bonus)

    away_wins = home_wins = ties = 0
    total_away = total_home = 0

    for _ in range(iterations):
        a = _poisson_sample(lam_away)
        h = _poisson_sample(lam_home)
        total_away += a
        total_home += h
        if a > h:
            away_wins += 1
        elif h > a:
            home_wins += 1
        else:
            ties += 1

    n = iterations
    return {
        "away_win_pct":  round(away_wins / n * 100, 1),
        "home_win_pct":  round(home_wins / n * 100, 1),
        "tie_pct":       round(ties      / n * 100, 1),
        "expected_away": round(total_away / n, 1),
        "expected_home": round(total_home / n, 1),
        "lambda_away":   round(lam_away, 3),
        "lambda_home":   round(lam_home, 3),
    }


if __name__ == "__main__":
    # HOU 공격(73.6) vs TOR 수비(~55) / TOR 공격 vs HOU 수비(55.4)
    result = simulate(
        away_offense=73.6, away_defense=55.4,
        home_offense=65.0, home_defense=60.0,
    )
    print("=== 시뮬레이션 결과 (HOU @ TOR) ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
