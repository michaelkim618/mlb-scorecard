"""
스코어카드 종합 → 승률 산출
sigmoid 함수로 점수 차이를 승률로 변환
"""
import math
from typing import Optional


def build_scorecard(
    away_sp: float, home_sp: float,
    away_bp: float, home_bp: float,
    away_bat: float, home_bat: float,
    away_sit: float, home_sit: float,
    sp_w: float = 0.30,
    bp_w: float = 0.20,
    bat_w: float = 0.35,
    sit_w: float = 0.15,
    home_bonus: float = 3.0,
    sigmoid_k: float = 0.055,
) -> dict:
    """
    4개 요소를 가중 합산 → sigmoid로 승률 변환

    net = home_total + home_bonus - away_total
    home_win = sigmoid(K * net) * 100
    """
    away_total = away_sp*sp_w + away_bp*bp_w + away_bat*bat_w + away_sit*sit_w
    home_total = home_sp*sp_w + home_bp*bp_w + home_bat*bat_w + home_sit*sit_w

    # ── Option A: 타선 미달 드래그 ─────────────────────────────────────
    # 타선이 극도로 약한 팀(Bat < 28)은 불펜이 좋아도 실제 득점 부족으로 승리 어려움
    # (28 ≈ 약 3.5득점/경기 수준. 페널티 최대 5.6pt)
    BAT_FLOOR = 28.0
    DRAG_RATE = 0.25
    if away_bat < BAT_FLOOR:
        away_total = max(away_total - (BAT_FLOOR - away_bat) * DRAG_RATE, away_total * 0.90)
    if home_bat < BAT_FLOOR:
        home_total = max(home_total - (BAT_FLOOR - home_bat) * DRAG_RATE, home_total * 0.90)

    net = (home_total + home_bonus) - away_total
    home_win_prob = 1.0 / (1.0 + math.exp(-sigmoid_k * net))

    home_win_pct = round(home_win_prob * 100, 1)
    away_win_pct = round(100.0 - home_win_pct, 1)

    return {
        "away_win_pct": away_win_pct,
        "home_win_pct": home_win_pct,
        "away_total":   round(away_total, 1),
        "home_total":   round(home_total, 1),
        "breakdown": {
            "away": {"sp": away_sp, "bp": away_bp, "bat": away_bat, "sit": away_sit},
            "home": {"sp": home_sp, "bp": home_bp, "bat": home_bat, "sit": home_sit},
        },
    }
