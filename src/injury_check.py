"""
부상/주전 변경 체크
- get_injury_notes(): 텍스트 플래그만 반환 (기존 호환)
- get_injury_penalty(): 타자 주전 부상 여부를 판단해 타선 점수 페널티 반환
"""
from mlb_stats_fetcher import get_injured_players, get_injured_players_detail
from typing import Optional

# 타자 주전으로 분류하는 포지션 타입
_BATTER_POSITION_TYPES = {"Outfielder", "Infielder"}

# 부상자 수별 타선 페널티 (상한: 3명 이상)
_PENALTY_TABLE = {
    0: 0.0,
    1: 1.5,
    2: 3.0,
}
_PENALTY_MAX = 4.5  # 3명 이상 상한


def get_injury_notes(
    away_id: int,
    home_id: int,
    away_name: str,
    home_name: str,
) -> Optional[str]:
    """
    부상자가 있으면 'Away: X, Y / Home: A, B' 형태 반환.
    없으면 None.
    """
    away_il = get_injured_players(away_id)
    home_il = get_injured_players(home_id)

    parts = []
    if away_il:
        parts.append(f"{away_name} IL: {', '.join(away_il[:5])}"
                     + (" 외 다수" if len(away_il) > 5 else ""))
    if home_il:
        parts.append(f"{home_name} IL: {', '.join(home_il[:5])}"
                     + (" 외 다수" if len(home_il) > 5 else ""))

    return " / ".join(parts) if parts else None


def get_injury_penalty(away_id: int, home_id: int) -> dict:
    """
    각 팀의 타자 주전 부상자 수를 집계해 타선 점수 페널티를 반환.

    타자 주전 판단 기준:
      - position.type이 "Outfielder" 또는 "Infielder"인 경우
      - Pitcher는 제외 (SP/BP 점수에 이미 반영됨)

    페널티:
      - 1명: -1.5pt
      - 2명: -3.0pt
      - 3명 이상: -4.5pt (상한)

    반환:
      {
        "away_penalty": float,
        "home_penalty": float,
        "away_detail": list[dict],   # 원정팀 타자 주전 부상자 목록
        "home_detail": list[dict],   # 홈팀 타자 주전 부상자 목록
      }
    """
    try:
        away_il = get_injured_players_detail(away_id)
    except Exception:
        away_il = []
    try:
        home_il = get_injured_players_detail(home_id)
    except Exception:
        home_il = []

    away_batters = [p for p in away_il if p.get("position") in _BATTER_POSITION_TYPES]
    home_batters = [p for p in home_il  if p.get("position") in _BATTER_POSITION_TYPES]

    def _calc_penalty(n: int) -> float:
        if n == 0:
            return 0.0
        if n >= 3:
            return _PENALTY_MAX
        return _PENALTY_TABLE.get(n, 0.0)

    return {
        "away_penalty": _calc_penalty(len(away_batters)),
        "home_penalty": _calc_penalty(len(home_batters)),
        "away_detail":  away_batters,
        "home_detail":  home_batters,
    }


if __name__ == "__main__":
    notes = get_injury_notes(117, 141, "Houston Astros", "Toronto Blue Jays")
    print(notes)
    penalty = get_injury_penalty(117, 141)
    print(penalty)
