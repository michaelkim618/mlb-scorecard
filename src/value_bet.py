"""
Value Bet 판정
edge = 모델 예측 승률 - Kalshi implied probability
"""
import json
from pathlib import Path
from typing import Optional

_cfg_path = Path(__file__).parent.parent / "config" / "weights.json"
_vb_cfg   = json.loads(_cfg_path.read_text())["value_bet"]
_THRESHOLD         = float(_vb_cfg.get("edge_threshold_pct",        8.0))
_EXTREME_EDGE_WARN = float(_vb_cfg.get("extreme_edge_warning_pct", 30.0))


def evaluate(
    model_home_pct: float,
    kalshi_home_prob: Optional[float],
    home_name: str,
    away_name: str,
    model_pct: float = 0.0,
    lineup_confirmed: bool = True,
) -> dict:
    """
    Args:
        model_home_pct   : 모델 홈팀 승률 (0~100)
        kalshi_home_prob : Kalshi 홈팀 implied prob (0~100) or None
        home_name, away_name : 팀명
        model_pct        : 모델 최고 확률 (0~100). 0이면 model_home_pct로 자동 계산
        lineup_confirmed : 라인업 확정 여부. False이면 강제 패스

    Returns dict with:
        kalshi_prob   : float or None
        edge          : float or None  (model - market)
        value_bet     : str 판정 텍스트
        extreme_edge  : bool — edge 절대값이 extreme_edge_warning_pct 이상
    """
    # 라인업 미확정 → 강제 패스
    if not lineup_confirmed:
        return {
            "kalshi_prob": kalshi_home_prob,
            "edge": None,
            "value_bet": "⏭️ 패스 (라인업 미확정)",
            "extreme_edge": False,
        }

    if kalshi_home_prob is None:
        return {
            "kalshi_prob": None,
            "edge": None,
            "value_bet": "마켓 없음",
            "extreme_edge": False,
        }

    edge = round(model_home_pct - kalshi_home_prob, 1)
    threshold = _THRESHOLD
    abs_edge = abs(edge)

    # 극단적 edge 감지: 모델과 시장이 30%p 이상 차이 → 칼시 데이터 이상 가능성
    extreme_edge = (abs_edge >= _EXTREME_EDGE_WARN)

    # 모델 최고 확률 계산 (파라미터 미제공 시 자동 계산)
    max_model_pct = model_pct if model_pct > 0.0 else max(model_home_pct, 100.0 - model_home_pct)

    # 박빙 필터: 모델 최고 확률 55% 미만이면 edge가 있어도 신뢰도 낮음 → 패스
    if max_model_pct < 55.0 and abs_edge >= threshold:
        return {
            "kalshi_prob": kalshi_home_prob,
            "edge": edge,
            "value_bet": "⏭️ 패스 (박빙 경기 — 예측 신뢰도 낮음)",
            "extreme_edge": extreme_edge,
        }

    # 극단적 예측(75%+) + 시장과 크게 반대(-15%p 이상)이면 최고 위험 경고
    if max_model_pct >= 75.0 and edge <= -15.0:
        label = f"🚨 극회피 — 극단적 예측({max_model_pct:.0f}%)이나 시장 반대 ({home_name} 시장 우위)"
    elif extreme_edge and edge > 0:
        # 모델이 시장보다 30%p+ 높게 봄 → 칼시 마켓 데이터 의심
        label = f"⚠️ 극단적 edge {edge:+.1f}%p — 마켓 데이터 이상 의심, VB 신중 검토 ({home_name})"
    elif extreme_edge and edge < 0:
        label = f"⚠️ 극단적 역edge {edge:+.1f}%p — 마켓 데이터 이상 의심 ({home_name} 시장 과대평가)"
    elif max_model_pct >= 75.0 and edge >= 15.0:
        label = f"🚨 극단 Edge (마켓 이상 의심) — {home_name} edge {edge:+.1f}%p"
    elif abs_edge >= 25.0:
        label = f"🚨 극단 Edge (마켓 이상 의심) — {home_name} edge {edge:+.1f}%p"
    elif edge >= 15.0:
        label = f"🔥 Strong Value Bet ({home_name}) — edge {edge:+.1f}%p"
    elif edge >= threshold:
        label = f"✅ Value Bet 후보 ({home_name})"
    elif edge <= -threshold:
        label = f"⚠️ 시장이 더 높게 평가 (회피 고려: {home_name})"
    else:
        label = "➖ 시장과 유사"

    return {
        "kalshi_prob":  kalshi_home_prob,
        "edge":         edge,
        "value_bet":    label,
        "extreme_edge": extreme_edge,
    }


if __name__ == "__main__":
    print(evaluate(63.0, 37.0, "San Francisco Giants", "Atlanta Braves"))
    print(evaluate(55.0, 45.0, "Boston Red Sox", "New York Yankees"))
    print(evaluate(70.0, None, "Houston Astros", "Toronto Blue Jays"))
