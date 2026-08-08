"""
MLB Stats API 래퍼: 팀/선수 스탯 수집
"""
import requests
from functools import lru_cache

BASE = "https://statsapi.mlb.com/api/v1"
SEASON = "2026"


def _get(url: str, params: dict = None) -> dict:
    resp = requests.get(url, params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ─── 팀 타격 게임로그 ────────────────────────────────────────────────

def get_team_hitting_log(team_id: int, limit: int = 10) -> list:
    """최근 N경기 팀 타격 스탯 리스트 (최신순)"""
    data = _get(f"{BASE}/teams/{team_id}/stats", {
        "stats": "gameLog",
        "group": "hitting",
        "season": SEASON,
        "limit": limit,
    })
    splits = data.get("stats", [{}])[0].get("splits", [])
    return [s["stat"] for s in splits[:limit]]


def get_team_hitting_season(team_id: int) -> dict:
    """팀 시즌 타격 스탯 (리그 순위 계산용)"""
    data = _get(f"{BASE}/teams/{team_id}/stats", {
        "stats": "season",
        "group": "hitting",
        "season": SEASON,
    })
    splits = data.get("stats", [{}])[0].get("splits", [])
    return splits[0]["stat"] if splits else {}


# ─── 팀 투구 게임로그 ────────────────────────────────────────────────

def get_team_pitching_log(team_id: int, limit: int = 10) -> list:
    """최근 N경기 팀 투구 스탯 리스트"""
    data = _get(f"{BASE}/teams/{team_id}/stats", {
        "stats": "gameLog",
        "group": "pitching",
        "season": SEASON,
        "limit": limit,
    })
    splits = data.get("stats", [{}])[0].get("splits", [])
    return [s["stat"] for s in splits[:limit]]


# ─── 선발투수 개인 스탯 ──────────────────────────────────────────────

def get_pitcher_gamelog(pitcher_id: int, limit: int = 6) -> list:
    """선발투수 최근 N경기 스탯"""
    data = _get(f"{BASE}/people/{pitcher_id}/stats", {
        "stats": "gameLog",
        "group": "pitching",
        "season": SEASON,
        "limit": limit,
    })
    splits = data.get("stats", [{}])[0].get("splits", [])
    # 팀ID → 약자 매핑
    _ABBR = {
        108:'LAA',109:'OAK',110:'BAL',111:'BOS',112:'CHC',113:'CIN',114:'CLE',115:'COL',
        116:'DET',117:'HOU',118:'KC', 119:'LAD',120:'WSH',121:'NYM',133:'OAK',134:'PIT',
        135:'SD', 136:'SEA',137:'SF', 138:'STL',139:'TB', 140:'TEX',141:'TOR',142:'MIN',
        143:'PHI',144:'ATL',145:'CWS',146:'MIA',147:'NYY',158:'MIL',
    }
    result = []
    for s in splits[:limit]:
        entry = dict(s["stat"])
        if "date" in s:
            entry["_game_date"] = s["date"]  # 등판 날짜 (휴식일 계산용)
        opp = s.get("opponent", {}) or {}
        opp_id = opp.get("id")
        entry["_opponent"] = _ABBR.get(opp_id, opp.get("name", "")[:3].upper()) if opp_id else ""
        entry["_is_home"] = s.get("isHome", False)
        result.append(entry)
    return result


def get_pitcher_season(pitcher_id: int) -> dict:
    """선발투수 시즌 스탯"""
    data = _get(f"{BASE}/people/{pitcher_id}/stats", {
        "stats": "season",
        "group": "pitching",
        "season": SEASON,
    })
    splits = data.get("stats", [{}])[0].get("splits", [])
    return splits[0]["stat"] if splits else {}


# ─── 리그 순위 ───────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_standings_map() -> dict:
    """
    팀ID → 득실차 및 연속 스트릭 정보 매핑.
    {
      team_id: {
        "runs_scored": int,
        "runs_allowed": int,
        "games_played": int,
        "run_diff_per_game": float,
        "streak_wins": int,   # 양수=연승, 음수=연패
      }
    }
    """
    data = _get(f"{BASE}/standings", {
        "leagueId": "103,104",
        "season": SEASON,
        "hydrate": "division",
    })
    result = {}
    for record in data.get("records", []):
        div_info = record.get("division", {}) or {}
        div_short = div_info.get("nameShort", div_info.get("name", ""))
        for tr in record.get("teamRecords", []):
            tid = tr["team"]["id"]
            rs = int(tr.get("runsScored", 0) or 0)
            ra = int(tr.get("runsAllowed", 0) or 0)
            gp = int(tr.get("gamesPlayed", 0) or 0)
            rdpg = (rs - ra) / gp if gp > 0 else 0.0
            streak = tr.get("streak", {}) or {}
            streak_type = streak.get("streakType", "")
            streak_num = int(streak.get("streakNumber", 0) or 0)
            if streak_type == "wins":
                streak_wins = streak_num
            elif streak_type == "losses":
                streak_wins = -streak_num
            else:
                streak_wins = 0
            div_rank = int(tr.get("divisionRank", 0) or 0)
            wins  = int(tr.get("wins", 0) or 0)
            losses = int(tr.get("losses", 0) or 0)
            games_back = tr.get("gamesBack", "-") or "-"
            div_name = div_short

            # 홈/원정 분리 성적
            hr = tr.get("records", {}).get("splitRecords", [])
            home_wins = away_wins = home_losses = away_losses = 0
            for split in hr:
                if split.get("type") == "home":
                    home_wins   = int(split.get("wins", 0) or 0)
                    home_losses = int(split.get("losses", 0) or 0)
                elif split.get("type") == "away":
                    away_wins   = int(split.get("wins", 0) or 0)
                    away_losses = int(split.get("losses", 0) or 0)
            home_total = home_wins + home_losses
            away_total = away_wins + away_losses
            home_wpct = home_wins / home_total if home_total > 0 else 0.500
            away_wpct = away_wins / away_total if away_total > 0 else 0.500

            result[tid] = {
                "runs_scored": rs,
                "runs_allowed": ra,
                "games_played": gp,
                "run_diff_per_game": rdpg,
                "streak_wins": streak_wins,
                "div_rank": div_rank,
                "div_name": div_name,
                "wins": wins,
                "losses": losses,
                "games_back": games_back,
                "home_wins": home_wins,
                "home_losses": home_losses,
                "home_wpct": home_wpct,
                "away_wins": away_wins,
                "away_losses": away_losses,
                "away_wpct": away_wpct,
            }
    return result


@lru_cache(maxsize=1)
def get_league_standings() -> dict:
    """팀ID → leagueRank 매핑 (1 = 1위)"""
    data = _get(f"{BASE}/standings", {
        "leagueId": "103,104",
        "season": SEASON,
    })
    rank_map = {}
    for record in data.get("records", []):
        for tr in record.get("teamRecords", []):
            tid = tr["team"]["id"]
            rank = int(tr.get("leagueRank", 30))
            rank_map[tid] = rank
    return rank_map


# ─── 부상자 명단 ─────────────────────────────────────────────────────

def get_injured_players(team_id: int) -> list[str]:
    """현재 부상자 명단 (이름 리스트)"""
    data = _get(f"{BASE}/teams/{team_id}/roster", {
        "rosterType": "40Man",
        "season": SEASON,
    })
    injured = []
    for p in data.get("roster", []):
        desc = p.get("status", {}).get("description", "")
        if "Injured" in desc or "IL" in desc:
            injured.append(p["person"]["fullName"])
    return injured


# ─── 유틸 ────────────────────────────────────────────────────────────

def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    # 빠른 테스트
    print("=== 팀 타격 게임로그 (HOU 최근 3경기) ===")
    logs = get_team_hitting_log(117, limit=3)
    for i, s in enumerate(logs):
        print(f"  경기{i+1}: AVG={s.get('avg')}, 득점={s.get('runs')}, HR={s.get('homeRuns')}")

    print("\n=== 팀 투구 게임로그 (HOU 최근 3경기) ===")
    plogs = get_team_pitching_log(117, limit=3)
    for i, s in enumerate(plogs):
        print(f"  경기{i+1}: ERA={s.get('era')}, 실점={s.get('runs')}, WHIP={s.get('whip')}")

    print("\n=== 리그 순위 (HOU) ===")
    ranks = get_league_standings()
    print(f"  HOU 리그순위: {ranks.get(117, 'N/A')}")

    print("\n=== HOU 부상자 ===")
    il = get_injured_players(117)
    print(f"  {', '.join(il) if il else '없음'}")

    print("\n=== 선발투수 스탯 (Peter Lambert) ===")
    gs = get_pitcher_gamelog(663567, limit=3)
    for i, s in enumerate(gs):
        print(f"  선발{i+1}: ERA={s.get('era')}, WHIP={s.get('whip')}, IP={s.get('inningsPitched')}")
