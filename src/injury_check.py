"""
부상/주전 변경 체크 (점수 미반영, 텍스트 플래그만)
"""
from mlb_stats_fetcher import get_injured_players
from typing import Optional


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


if __name__ == "__main__":
    notes = get_injury_notes(117, 141, "Houston Astros", "Toronto Blue Jays")
    print(notes)
