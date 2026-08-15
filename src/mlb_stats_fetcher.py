"""
MLB Stats API 래퍼: 팀/선수 스탯 수집
캐시 전략:
  - 방향1: 당일 API 실패 시 전날 캐시 파일로 fallback (output/cache/)
  - 방향3: 팀별 시즌 타선 DB를 로컬에 저장 (output/team_batting_season.json)
"""
import json
import os
import glob
import requests
from datetime import date, datetime, timedelta
from functools import lru_cache

BASE = "https://statsapi.mlb.com/api/v1"
SEASON = "2026"

# 캐시 디렉토리: scorecard_pipeline.py 기준 output/cache/
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "output", "cache")
_SEASON_DB  = os.path.join(os.path.dirname(__file__), "..", "output", "team_batting_season.json")


def _ensure_cache_dir():
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _cache_path(key: str, today: str = None) -> str:
    today = today or date.today().isoformat()
    return os.path.join(_CACHE_DIR, f"{key}_{today}.json")


def _save_cache(key: str, data, today: str = None):
    _ensure_cache_dir()
    path = _cache_path(key, today)
    with open(path, "w") as f:
        json.dump(data, f)


def _load_cache(key: str, max_days: int = 3):
    """오늘부터 max_days일 이내 가장 최신 캐시 파일 로드"""
    _ensure_cache_dir()
    today = date.today()
    for delta in range(max_days + 1):
        d = (today - timedelta(days=delta)).isoformat()
        path = _cache_path(key, d)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f), d
            except Exception:
                continue
    return None, None


def _load_season_db() -> dict:
    if os.path.exists(_SEASON_DB):
        try:
            with open(_SEASON_DB) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_season_db(db: dict):
    _ensure_cache_dir()
    os.makedirs(os.path.dirname(_SEASON_DB), exist_ok=True)
    with open(_SEASON_DB, "w") as f:
        json.dump(db, f, indent=2)


def _get(url: str, params: dict = None) -> dict:
    resp = requests.get(url, params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ─── 팀 타격 게임로그 ────────────────────────────────────────────────

def get_team_hitting_log(team_id: int, limit: int = 10) -> list:
    """최근 N경기 팀 타격 스탯 리스트 (최신순). API 실패 시 캐시 fallback."""
    cache_key = f"hit_log_{team_id}_{limit}"
    try:
        data = _get(f"{BASE}/teams/{team_id}/stats", {
            "stats": "gameLog",
            "group": "hitting",
            "season": SEASON,
            "limit": limit,
        })
        splits = data.get("stats", [{}])[0].get("splits", [])
        result = [s["stat"] for s in splits[-limit:]]
        if result:
            _save_cache(cache_key, result)
        return result
    except Exception:
        cached, cache_date = _load_cache(cache_key)
        if cached:
            print(f"  [캐시 fallback] hit_log team={team_id} ({cache_date})")
            return cached
        return []


def get_team_hitting_log_split(team_id: int, n: int = 10) -> dict:
    """
    홈/원정 분리 타격 게임로그 (각 최근 N경기). API 실패 시 캐시 fallback.
    Returns: {
        "home": [stat dict, ...],   # 최근 N홈경기 (최신순)
        "away": [stat dict, ...],   # 최근 N원정경기 (최신순)
        "all":  [stat dict, ...],   # 전체 최근 N경기
    }
    """
    cache_key = f"hit_split_{team_id}_{n}"
    empty = {"home": [], "away": [], "all": []}
    try:
        fetch_limit = max(n * 4, 40)
        data = _get(f"{BASE}/teams/{team_id}/stats", {
            "stats": "gameLog",
            "group": "hitting",
            "season": SEASON,
            "limit": fetch_limit,
        })
        splits = data.get("stats", [{}])[0].get("splits", [])

        home_logs, away_logs, all_logs = [], [], []
        for s in splits:
            stat = dict(s["stat"])
            stat["_is_home"] = s.get("isHome", False)
            stat["_date"]    = s.get("date", "")
            all_logs.append(stat)
            if s.get("isHome"):
                home_logs.append(stat)
            else:
                away_logs.append(stat)

        result = {"home": home_logs[-n:], "away": away_logs[-n:], "all": all_logs[-n:]}
        if all_logs:
            _save_cache(cache_key, result)
            # 방향3: 시즌 DB 업데이트 (최근 30경기 평균으로 시즌 proxy)
            _update_season_db(team_id, all_logs)
        return result
    except Exception:
        cached, cache_date = _load_cache(cache_key)
        if cached:
            print(f"  [캐시 fallback] hit_split team={team_id} ({cache_date})")
            return cached
        # 방향3: 시즌 DB에서 최후 fallback
        season_db = _load_season_db()
        db_entry = season_db.get(str(team_id))
        if db_entry:
            print(f"  [시즌DB fallback] team={team_id} (updated {db_entry.get('updated')})")
            return db_entry.get("split_snapshot", empty)
        return empty


def _update_season_db(team_id: int, all_logs: list):
    """최근 게임로그로 시즌 DB 업데이트 (방향3)"""
    if not all_logs:
        return
    try:
        db = _load_season_db()
        # 최근 30경기로 팀 타선 평균 계산
        logs = all_logs[-30:]
        def _avg(key):
            vals = [float(g.get(key, 0) or 0) for g in logs if g.get(key) is not None]
            return round(sum(vals) / len(vals), 3) if vals else 0.0

        home_logs = [g for g in logs if g.get("_is_home")]
        away_logs = [g for g in logs if not g.get("_is_home")]

        db[str(team_id)] = {
            "updated": date.today().isoformat(),
            "avg":     _avg("avg"),
            "ops":     _avg("ops"),
            "runs_per_game": _avg("runs"),
            "split_snapshot": {
                "home": home_logs[-10:],
                "away": away_logs[-10:],
                "all":  logs[-10:],
            }
        }
        _save_season_db(db)
    except Exception:
        pass


def get_team_hitting_season(team_id: int) -> dict:
    """팀 시즌 타격 스탯 (리그 순위 계산용). API 실패 시 캐시 fallback."""
    cache_key = f"hit_season_{team_id}"
    try:
        data = _get(f"{BASE}/teams/{team_id}/stats", {
            "stats": "season",
            "group": "hitting",
            "season": SEASON,
        })
        splits = data.get("stats", [{}])[0].get("splits", [])
        result = splits[0]["stat"] if splits else {}
        if result:
            _save_cache(cache_key, result)
        return result
    except Exception:
        cached, cache_date = _load_cache(cache_key)
        if cached:
            print(f"  [캐시 fallback] hit_season team={team_id} ({cache_date})")
            return cached
        return {}


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


# ─── 팀 불펜 직접 스탯 (Option 2) ───────────────────────────────────

def get_bullpen_era_direct(team_id: int, starter_gs_threshold: int = 5) -> dict:
    """
    팀 활성 로스터에서 투수를 가져와 gamesStarted 기준으로
    선발/불펜 분리 → 불펜 실제 시즌 ERA 계산

    starter_gs_threshold: 이 이상 선발 등판하면 '선발'로 분류 (기본 5)
    Returns:
        {
          "bullpen_era": float,   # 불펜 합산 ERA
          "team_era":   float,   # 팀 전체 ERA (참고용)
          "bp_ip":      float,   # 불펜 이닝
          "bp_count":   int,     # 불펜 투수 수
        }
    """
    # 1. 활성 로스터에서 투수 목록 가져오기
    try:
        roster_data = _get(f"{BASE}/teams/{team_id}/roster", {
            "rosterType": "active",
            "season": SEASON,
        })
    except Exception:
        return {"bullpen_era": 4.00, "team_era": 4.00, "bp_ip": 0.0, "bp_count": 0}

    roster = roster_data.get("roster", [])
    pitcher_ids = [
        p["person"]["id"]
        for p in roster
        if p.get("position", {}).get("type") == "Pitcher"
    ]

    if not pitcher_ids:
        return {"bullpen_era": 4.00, "team_era": 4.00, "bp_ip": 0.0, "bp_count": 0}

    # 2. 각 투수 시즌 스탯 조회 & 선발/불펜 분류
    bp_ip = 0.0
    bp_er = 0.0
    all_ip = 0.0
    all_er = 0.0
    bp_count = 0

    def _parse_ip(ip_str) -> float:
        try:
            s = str(ip_str)
            if '.' in s:
                full, thirds = s.split('.')
                return int(full) + int(thirds) / 3.0
            return float(s)
        except Exception:
            return 0.0

    # 최근 7일 기준일 계산
    from datetime import date, timedelta
    cutoff_date = date.today() - timedelta(days=7)

    # 최근 7일 불펜 집계용
    recent_bp_ip = 0.0
    recent_bp_er = 0.0
    recent_bp_appearances = 0  # 7일 내 등판 횟수 (피로도 지표)

    for pid in pitcher_ids:
        try:
            # ① 시즌 스탯
            data = _get(f"{BASE}/people/{pid}/stats", {
                "stats": "season",
                "group": "pitching",
                "season": SEASON,
                "gameType": "R",
            })
            splits = data.get("stats", [{}])[0].get("splits", [])
            if not splits:
                continue
            s = splits[0]["stat"]
            gs  = int(s.get("gamesStarted", 0) or 0)
            ip  = _parse_ip(s.get("inningsPitched", "0"))
            er  = float(s.get("earnedRuns", 0) or 0)

            all_ip += ip
            all_er += er

            is_bullpen = gs < starter_gs_threshold
            if is_bullpen:
                bp_ip += ip
                bp_er += er
                bp_count += 1

            # ② 최근 7일 게임로그 (불펜 투수만)
            if is_bullpen:
                try:
                    gl_data = _get(f"{BASE}/people/{pid}/stats", {
                        "stats": "gameLog",
                        "group": "pitching",
                        "season": SEASON,
                        "gameType": "R",
                        "limit": 10,
                    })
                    gl_splits = gl_data.get("stats", [{}])[0].get("splits", [])
                    for gl in gl_splits:
                        game_date_str = gl.get("date", "")
                        if not game_date_str:
                            continue
                        try:
                            game_date = date.fromisoformat(game_date_str[:10])
                        except Exception:
                            continue
                        if game_date >= cutoff_date:
                            gl_stat = gl.get("stat", {})
                            r_ip = _parse_ip(gl_stat.get("inningsPitched", "0"))
                            r_er = float(gl_stat.get("earnedRuns", 0) or 0)
                            recent_bp_ip += r_ip
                            recent_bp_er += r_er
                            recent_bp_appearances += 1
                except Exception:
                    pass

        except Exception:
            continue

    bullpen_era = round(bp_er / bp_ip * 9, 2) if bp_ip > 0 else 4.00
    team_era    = round(all_er / all_ip * 9, 2) if all_ip > 0 else 4.00

    # 최근 7일 ERA (샘플 부족 시 시즌 ERA로 fallback)
    recent_era = round(recent_bp_er / recent_bp_ip * 9, 2) if recent_bp_ip >= 2.0 else bullpen_era

    return {
        "bullpen_era":          bullpen_era,
        "recent_era":           recent_era,           # 최근 7일 ERA
        "recent_appearances":   recent_bp_appearances, # 7일 내 등판 횟수 (피로도)
        "recent_ip":            round(recent_bp_ip, 1),
        "team_era":             team_era,
        "bp_ip":                round(bp_ip, 1),
        "bp_count":             bp_count,
    }


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
