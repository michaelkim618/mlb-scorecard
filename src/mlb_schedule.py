"""
MLB 일정 조회: statsapi.mlb.com
"""
import requests
from datetime import date, datetime, timezone, timedelta
from typing import Optional, List, Dict

# 미국 서부 시간 — DST 자동 대응 (PST=UTC-8 / PDT=UTC-7)
# ★ 수정: 고정 UTC-7 대신 ZoneInfo 사용 → PST/PDT 자동 전환
try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _LA_TZ = _ZoneInfo("America/Los_Angeles")
except ImportError:
    # Python 3.8 이하 fallback (고정 UTC-7, 겨울에 1시간 오차 감수)
    _LA_TZ = None

def _to_pt_str(game_date_utc: str) -> str:
    """'2026-08-07T22:40:00Z' → '3:40 PM PT' (DST 자동 대응)"""
    try:
        dt_utc = datetime.strptime(game_date_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if _LA_TZ:
            dt_pt = dt_utc.astimezone(_LA_TZ)
        else:
            # fallback: UTC-7 고정 (여름 PDT 기준, 겨울은 1시간 오차)
            dt_pt = dt_utc.astimezone(timezone(timedelta(hours=-7)))
        return dt_pt.strftime("%-I:%M %p PT")
    except Exception:
        return ""


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
    "2026-09-11": {
        119: ("Blake Snell", 605483),  # LAD away @ MIA (API가 Wrobleski로 잘못 표시)
        109: ("Merrill Kelly", 518876),  # ARI home vs TEX (API가 E. Rodriguez로 잘못 표시)
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
        "hydrate": "probablePitcher,linescore",
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

            # 실제 경기 결과 (Live 및 Final 모두 반영)
            status_state = g["status"]["abstractGameState"]
            actual_away  = away.get("score")   # None if Preview
            actual_home  = home.get("score")
            # linescore에서 보완 (Live 경기 스코어) — Preview(미시작)는 제외
            if status_state in ("Live", "Final") and (actual_away is None or actual_home is None):
                linescore = g.get("linescore", {})
                ls_teams  = linescore.get("teams", {})
                actual_away = ls_teams.get("away", {}).get("runs", actual_away)
                actual_home = ls_teams.get("home", {}).get("runs", actual_home)
            actual_winner = None
            if status_state == "Final":
                if away.get("isWinner"):
                    actual_winner = away["team"]["name"]
                elif home.get("isWinner"):
                    actual_winner = home["team"]["name"]

            away_id = away["team"]["id"]
            home_id = home["team"]["id"]

            # ── 수동 오버라이드 적용 (API 값과 무관하게 강제 적용) ──────
            day_overrides = PITCHER_OVERRIDES.get(game_date, {})
            if away_id in day_overrides:
                name, pid = day_overrides[away_id]
                prev = away_pitcher.get("fullName", "미정")
                away_pitcher = {"fullName": name, "id": pid}
                print(f"  [오버라이드] {away['team']['name']} 선발: {prev} → {name}")
            if home_id in day_overrides:
                name, pid = day_overrides[home_id]
                prev = home_pitcher.get("fullName", "미정")
                home_pitcher = {"fullName": name, "id": pid}
                print(f"  [오버라이드] {home['team']['name']} 선발: {prev} → {name}")

            game_time_pt = _to_pt_str(g.get("gameDate", ""))
            game_number   = g.get("gameNumber", 1)          # 더블헤더: 1 or 2
            double_header = g.get("doubleHeader", "N")      # "S"=스플릿 더블헤더, "Y"=전통, "N"=없음

            games.append({
                "gamePk":          g["gamePk"],
                "gameDate":        g.get("officialDate", game_date),
                "game_time":       game_time_pt,
                "game_number":     game_number,
                "doubleheader":    double_header != "N",
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
