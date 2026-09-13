"""
부상/주전 변경 체크
- get_injury_notes(): 텍스트 플래그만 반환 (기존 호환)
- get_injury_penalty(): OPS 가중 부상 페널티 반환 (v2)

v2 개선사항:
  - 단순 인원 수 기반 고정 페널티 → 부상자 개인 OPS 기반 가중 페널티
  - 평균 이상(OPS > 0.720) 선수 부상일수록 더 큰 패널티
  - 상한: 10pt (극단적 팀 붕괴 상황 대비)
"""
from mlb_stats_fetcher import (
    get_injured_players,
    get_injured_players_detail,
    get_player_season_ops,
)
from typing import Optional

# 타자 주전으로 분류하는 포지션 타입
_BATTER_POSITION_TYPES = {"Outfielder", "Infielder"}

# OPS 가중 페널티 파라미터
_LEAGUE_AVG_OPS  = 0.720   # 리그 평균 OPS (기준선)
_OPS_SCALE       = 20.0    # (player_ops - avg_ops) * scale = 페널티 기여pt
_PER_PLAYER_MIN  = 1.0     # 평균 이하 타자도 최소 1pt 페널티
_PER_PLAYER_MAX  = 6.0     # 단일 선수 기여 상한 (슈퍼스타 과대 보정 방지)
_TOTAL_MAX       = 10.0    # 팀 전체 상한


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


def _calc_ops_penalty(batters: list[dict]) -> tuple[float, list[dict]]:
    """
    타자 부상자 목록을 받아 OPS 가중 페널티 합계를 계산.

    각 선수별:
      contribution = clamp((ops - LEAGUE_AVG) * OPS_SCALE, PER_MIN, PER_MAX)

    전체 페널티 = min(sum(contributions), TOTAL_MAX)

    반환: (total_penalty, enriched_batters)
      enriched_batters: 각 dict에 "ops", "penalty_contribution" 추가
    """
    if not batters:
        return 0.0, []

    enriched = []
    total = 0.0
    for p in batters:
        pid = p.get("player_id")
        ops = get_player_season_ops(pid) if pid else _LEAGUE_AVG_OPS
        raw = (ops - _LEAGUE_AVG_OPS) * _OPS_SCALE
        contribution = max(_PER_PLAYER_MIN, min(_PER_PLAYER_MAX, raw))
        total += contribution
        enriched.append({**p, "ops": round(ops, 3), "penalty_contribution": round(contribution, 1)})

    total = round(min(total, _TOTAL_MAX), 1)
    return total, enriched


def get_injury_penalty(away_id: int, home_id: int) -> dict:
    """
    각 팀의 타자 주전 부상자를 조회해 OPS 가중 페널티를 반환.

    타자 주전 판단 기준:
      - position.type이 "Outfielder" 또는 "Infielder"인 경우
      - Pitcher는 제외 (SP/BP 점수에 이미 반영됨)

    페널티 계산 (v2 — OPS 가중):
      per_player = clamp((player_ops - 0.720) * 20, 1.0, 6.0)
      total      = min(sum(per_player), 10.0)

    예시:
      Stanton (OPS 0.950): (0.950-0.720)*20 = 4.6pt
      Chisholm (OPS 0.800): (0.800-0.720)*20 = 1.6pt
      합계 → 6.2pt (기존 flat 3.0pt 대비 2배 이상)

    반환:
      {
        "away_penalty": float,
        "home_penalty": float,
        "away_detail": list[dict],   # 원정팀 타자 주전 부상자 (ops, penalty_contribution 포함)
        "home_detail": list[dict],   # 홈팀 타자 주전 부상자
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

    away_penalty, away_detail = _calc_ops_penalty(away_batters)
    home_penalty, home_detail = _calc_ops_penalty(home_batters)

    return {
        "away_penalty": away_penalty,
        "home_penalty": home_penalty,
        "away_detail":  away_detail,
        "home_detail":  home_detail,
    }


if __name__ == "__main__":
    notes = get_injury_notes(117, 141, "Houston Astros", "Toronto Blue Jays")
    print(notes)
    penalty = get_injury_penalty(117, 141)
    print(f"HOU penalty: {penalty['away_penalty']}pt → {penalty['away_detail']}")
    print(f"TOR penalty: {penalty['home_penalty']}pt → {penalty['home_detail']}")

    # NYY 테스트 (Stanton + Chisholm IL)
    print("\n=== NYY (team_id=147) IL penalty ===")
    pen_nyy = get_injury_penalty(121, 147)  # NYM away, NYY home
    print(f"NYM penalty: {pen_nyy['away_penalty']}pt → {[(p['name'], p.get('ops')) for p in pen_nyy['away_detail']]}")
    print(f"NYY penalty: {pen_nyy['home_penalty']}pt → {[(p['name'], p.get('ops')) for p in pen_nyy['home_detail']]}")
