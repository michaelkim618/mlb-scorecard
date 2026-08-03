"""
MLB 일정 조회: statsapi.mlb.com
"""
import requests
from datetime import date
from typing import Optional, List, Dict


BASE = "https://statsapi.mlb.com/api/v1"

# ── 수동 선발 오버라이드 (API가 TBD로 표시될 때 직접 지정) ─────────────
# 형식: { "YYYY-MM-DD": { away_team_id_or_home_team_id: (pitcher_name, pitcher_id) } }
PITCHER_OVERRIDES: dict = {
    "2026-07-03": {
        137: ("Logan Webb", 657277),   # SF Giants away vs COL
    },
    "2026-07-05": {
        139: ("Griffin Jax", 643377),  # Tampa Bay Rays away vs HOU
    },
    "2026-07-07": {
        136: ("Bryan Woo", 693433),    # Seattle Mariners away vs MIA
    },
}


def get_games(game_date: Optional[str] = None) -> List[Dict]:
    """
    Returns list of game dicts for the given date (YYYY-MM-DD).
    Each dict: {gamePk, away_id, away_name, home_id, home_name,
                away_pitcher_id, home_pitcher_id, status}
    """
    if game_date is None:
        game_date = date.today().isoformat()

    url = f"{BASE}/schedule"
    params = {
        "sportId": 1,
        "date": game_date,
        "hydrate": "probablePitcher",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    games = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            if g.get("gameType") != "R":
                continue

            away = g["teams"]["away"]
            home = g["teams"]["home"]

            away_pitcher = away.get("probablePitcher") or {}
            home_pitcher = home.get("probablePitcher") or {}

            away_record = away.get("leagueRecord", {})
            home_record = home.get("leagueRecord", {})

            # 실제 경기 결과 (Final인 경우에만)
            status_state = g["status"]["abstractGameState"]
            actual_away  = away.get("score")   # None if not final
            actual_home  = home.get("score")
            actual_winner = None
            if status_state == "Final":
                if away.get("isWinner"):
                    actual_winner = away["team"]["name"]
                elif home.get("isWinner"):
                    actual_winner = home["team"]["name"]

            away_id = away["team"]["id"]
            home_id = home["team"]["id"]

            # ── 수동 오버라이드 적용 ────────────────────────────────
            day_overrides = PITCHER_OVERRIDES.get(game_date, {})
            if away_id in day_overrides and not away_pitcher.get("id"):
                name, pid = day_overrides[away_id]
                away_pitcher = {"fullName": name, "id": pid}
                print(f"  [오버라이드] {away['team']['name']} 선발: {name}")
            if home_id in day_overrides and not home_pitcher.get("id"):
                name, pid = day_overrides[home_id]
                home_pitcher = {"fullName": name, "id": pid}
                print(f"  [오버라이드] {home['team']['name']} 선발: {name}")

            games.append({
                "gamePk":          g["gamePk"],
                "gameDate":        g.get("officialDate", game_date),
                "status":          status_state,
                "away_id":         away_id,
                "away_name":       away["team"]["name"],
                "away_wins":       away_record.get("wins", 0),
                "away_losses":     away_record.get("losses", 0),
                "home_id":         home_id,
                "home_name":       home["team"]["name"],
                "home_wins":       home_record.get("wins", 0),
                "home_losses":     home_record.get("losses", 0),
                "away_pitcher_id": away_pitcher.get("id"),
                "away_pitcher":    away_pitcher.get("fullName", "TBD"),
                "home_pitcher_id": home_pitcher.get("id"),
                "home_pitcher":    home_pitcher.get("fullName", "TBD"),
                "actual_away":     actual_away,
                "actual_home":     actual_home,
                "actual_winner":   actual_winner,
            })

    return games


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    games = get_games(d)
    print(f"오늘 경기 수: {len(games)}")
    for g in games:
        print(f"  {g['away_name']} @ {g['home_name']}  "
              f"[선발: {g['away_pitcher']} vs {g['home_pitcher']}]  "
              f"상태: {g['status']}")
