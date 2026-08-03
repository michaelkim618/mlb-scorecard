"""
과거 시즌 데이터 수집 + 디스크 캐시
- 시즌 전체 경기 결과 (팀 기록, 선발투수, 스코어)
- 투수 ERA 캐시 (pitcher_era_cache.json)
"""
import json
import time
import requests
from pathlib import Path
from typing import Optional

BASE         = "https://statsapi.mlb.com/api/v1"
DATA_DIR     = Path(__file__).parent.parent / "data"
ERA_CACHE    = DATA_DIR / "pitcher_era_cache.json"
STATS_CACHE  = DATA_DIR / "pitcher_stats_cache.json"

DATA_DIR.mkdir(exist_ok=True)


# ── 투수 스탯 캐시 (신규: era + ip + wins + losses) ────────────────

def _load_stats_cache() -> dict:
    if STATS_CACHE.exists():
        return json.loads(STATS_CACHE.read_text(encoding="utf-8"))
    return {}

def _save_stats_cache(cache: dict):
    STATS_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def get_pitcher_stats(pitcher_id: int, season: int,
                      cache: dict = None, save: bool = True) -> dict:
    """투수 시즌 스탯 반환 (era, ip, wins, losses). 캐시 우선."""
    if cache is None:
        cache = _load_stats_cache()
    key = f"{pitcher_id}_{season}"
    if key in cache:
        return cache[key]

    try:
        resp = requests.get(f"{BASE}/people/{pitcher_id}/stats", params={
            "stats": "season", "group": "pitching", "season": season,
        }, timeout=10)
        resp.raise_for_status()
        splits = resp.json().get("stats", [{}])[0].get("splits", [])
        stat = splits[0]["stat"] if splits else {}
        era_str = stat.get("era", "")
        era = float(era_str) if era_str and era_str not in ("-.--", "∞", "") else 4.50
        try:
            ip = float(stat.get("inningsPitched", 0) or 0)
        except (TypeError, ValueError):
            ip = 0.0
        wins   = stat.get("wins", 0) or 0
        losses = stat.get("losses", 0) or 0
    except Exception:
        era, ip, wins, losses = 4.50, 0.0, 0, 0

    result = {"era": era, "ip": ip, "wins": wins, "losses": losses}
    cache[key] = result
    if save:
        _save_stats_cache(cache)
    return result


def apply_era_correction(era: float, ip: float, min_ip: float = 20.0) -> float:
    """이닝 수가 적을수록 ERA를 리그 평균(4.50)에 가깝게 보정."""
    LEAGUE_AVG_ERA = 4.50
    if ip >= min_ip:
        return era
    ratio = ip / min_ip
    return round(era * ratio + LEAGUE_AVG_ERA * (1 - ratio), 2)


# ── 투수 ERA 캐시 (하위호환 유지) ─────────────────────────────────

def _load_era_cache() -> dict:
    if ERA_CACHE.exists():
        return json.loads(ERA_CACHE.read_text(encoding="utf-8"))
    return {}

def _save_era_cache(cache: dict):
    ERA_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

def get_pitcher_era(pitcher_id: int, season: int,
                    cache: dict = None, save: bool = True) -> float:
    """투수 시즌 ERA 반환 (없으면 4.50). 하위호환 유지 — 내부적으로 get_pitcher_stats() 사용."""
    stats_cache = _load_stats_cache()
    result = get_pitcher_stats(pitcher_id, season, cache=stats_cache, save=save)
    return result["era"]


# ── 시즌 스케줄 수집 ────────────────────────────────────────────────

def _parse_game(g: dict, season: int) -> Optional[dict]:
    """단일 경기 dict 파싱. 완료 경기만 반환."""
    if g.get("gameType") != "R":
        return None
    if g["status"]["abstractGameState"] != "Final":
        return None

    away = g["teams"]["away"]
    home = g["teams"]["home"]

    # 승자 없는 경기(취소 등) 제외
    if not away.get("isWinner") and not home.get("isWinner"):
        return None

    away_rec = away.get("leagueRecord", {})
    home_rec = home.get("leagueRecord", {})
    away_p   = (away.get("probablePitcher") or {})
    home_p   = (home.get("probablePitcher") or {})

    away_w = away_rec.get("wins", 0)
    away_l = away_rec.get("losses", 0)
    home_w = home_rec.get("wins", 0)
    home_l = home_rec.get("losses", 0)

    return {
        "gamePk":           g["gamePk"],
        "date":             g.get("officialDate", ""),
        "season":           season,
        "away_id":          away["team"]["id"],
        "away_name":        away["team"]["name"],
        "away_wins":        away_w,
        "away_losses":      away_l,
        "home_id":          home["team"]["id"],
        "home_name":        home["team"]["name"],
        "home_wins":        home_w,
        "home_losses":      home_l,
        "away_pitcher_id":  away_p.get("id"),
        "home_pitcher_id":  home_p.get("id"),
        "away_score":       away.get("score"),
        "home_score":       home.get("score"),
        "home_win":         1 if home.get("isWinner") else 0,
    }


def fetch_season(year: int, force: bool = False) -> list:
    """
    한 시즌 전체 완료 경기 리스트 반환.
    data/historical_{year}.json에 캐시 저장.
    """
    cache_path = DATA_DIR / f"historical_{year}.json"
    if cache_path.exists() and not force:
        print(f"  [{year}] 캐시 로드: {cache_path}")
        return json.loads(cache_path.read_text(encoding="utf-8"))

    print(f"  [{year}] MLB API에서 수집 중…")
    url = f"{BASE}/schedule"
    params = {
        "sportId":   1,
        "season":    year,
        "gameType":  "R",
        "hydrate":   "probablePitcher",
        "startDate": f"{year}-01-01",
        "endDate":   f"{year}-12-31",
    }
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    games = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            parsed = _parse_game(g, year)
            if parsed:
                games.append(parsed)

    cache_path.write_text(json.dumps(games, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [{year}] {len(games)}경기 저장 → {cache_path}")
    return games


def fetch_all_seasons(years: list = None, force: bool = False) -> list:
    """여러 시즌 합산 반환."""
    if years is None:
        years = [2023, 2024, 2025]
    all_games = []
    for y in years:
        all_games.extend(fetch_season(y, force=force))
    print(f"  총 {len(all_games)}경기 수집 완료")
    return all_games


def enrich_with_streak(games: list) -> list:
    """
    각 경기 이전까지의 팀별 연승/연패 스트릭 추가.
    away_streak, home_streak: 양수=연승, 음수=연패, 0=첫경기
    """
    from collections import defaultdict

    sorted_games = sorted(games, key=lambda g: (g.get("date", ""), g.get("gamePk", 0)))
    team_streak: dict = defaultdict(int)  # 팀ID → 현재 스트릭

    for g in sorted_games:
        away_id = g.get("away_id")
        home_id = g.get("home_id")

        # 경기 전 스트릭 기록
        g["away_streak"] = team_streak[away_id]
        g["home_streak"] = team_streak[home_id]

        # 경기 결과로 스트릭 업데이트
        home_win = g.get("home_win")
        if home_win is not None:
            if home_win == 1:
                team_streak[home_id] = max(team_streak[home_id], 0) + 1
                team_streak[away_id] = min(team_streak[away_id], 0) - 1
            else:
                team_streak[away_id] = max(team_streak[away_id], 0) + 1
                team_streak[home_id] = min(team_streak[home_id], 0) - 1

    return games


def enrich_with_run_diff(games: list) -> list:
    """
    각 경기에 away_run_diff_per_game, home_run_diff_per_game 추가.
    해당 경기 이전까지의 누적 득실차(RS-RA)/경기수 사용.
    경기 수 < 10인 경우 0.0 반환.
    """
    from collections import defaultdict

    # 날짜순 정렬
    sorted_games = sorted(games, key=lambda g: g.get("date", ""))

    # 팀별 누적 스탯 추적
    team_stats: dict = defaultdict(lambda: {"rs": 0, "ra": 0, "gp": 0})

    for g in sorted_games:
        away_id = g.get("away_id")
        home_id = g.get("home_id")
        away_score = g.get("away_score")
        home_score = g.get("home_score")

        # 경기 전 누적 스탯으로 피처 계산
        a_stats = team_stats[away_id]
        h_stats = team_stats[home_id]

        if a_stats["gp"] >= 10:
            g["away_run_diff_per_game"] = (a_stats["rs"] - a_stats["ra"]) / a_stats["gp"]
        else:
            g["away_run_diff_per_game"] = 0.0

        if h_stats["gp"] >= 10:
            g["home_run_diff_per_game"] = (h_stats["rs"] - h_stats["ra"]) / h_stats["gp"]
        else:
            g["home_run_diff_per_game"] = 0.0

        # 경기 결과로 누적 스탯 업데이트
        if away_score is not None and home_score is not None:
            try:
                a_sc = int(away_score)
                h_sc = int(home_score)
                team_stats[away_id]["rs"] += a_sc
                team_stats[away_id]["ra"] += h_sc
                team_stats[away_id]["gp"] += 1
                team_stats[home_id]["rs"] += h_sc
                team_stats[home_id]["ra"] += a_sc
                team_stats[home_id]["gp"] += 1
            except (TypeError, ValueError):
                pass

    return games


def enrich_with_era(games: list, verbose: bool = True) -> list:
    """
    각 경기에 away_starter_era, home_starter_era (보정 ERA) 및
    away_starter_ip, home_starter_ip 추가.
    투수별로 캐시하며 API 호출 최소화.
    """
    cache = _load_stats_cache()

    # 미캐시 투수 목록 추출
    needed = set()
    for g in games:
        for pid, season in [(g.get("away_pitcher_id"), g["season"]),
                            (g.get("home_pitcher_id"), g["season"])]:
            if pid and f"{pid}_{season}" not in cache:
                needed.add((pid, season))

    if needed and verbose:
        print(f"  투수 스탯 수집 필요: {len(needed)}명 (캐시 없음)")

    for i, (pid, season) in enumerate(needed):
        get_pitcher_stats(pid, season, cache=cache, save=False)
        if verbose and (i+1) % 50 == 0:
            print(f"    {i+1}/{len(needed)} 완료…")
        time.sleep(0.05)   # API 부하 방지

    _save_stats_cache(cache)

    for g in games:
        away_pid = g.get("away_pitcher_id") or 0
        home_pid = g.get("home_pitcher_id") or 0
        a_stats = get_pitcher_stats(away_pid, g["season"], cache=cache, save=False)
        h_stats = get_pitcher_stats(home_pid, g["season"], cache=cache, save=False)
        g["away_starter_era"] = apply_era_correction(a_stats["era"], a_stats["ip"])
        g["home_starter_era"] = apply_era_correction(h_stats["era"], h_stats["ip"])
        g["away_starter_ip"]  = a_stats["ip"]
        g["home_starter_ip"]  = h_stats["ip"]

    return games


def enrich_with_pitcher_wl(games: list, verbose: bool = True) -> list:
    """
    각 경기에 away/home 투수 시즌 W/L/IP 추가.
    get_pitcher_stats() 캐시를 활용해 API 재호출 최소화.
    """
    cache = _load_stats_cache()

    # 미캐시 투수 목록 추출
    needed = set()
    for g in games:
        for pid, season in [(g.get("away_pitcher_id"), g["season"]),
                            (g.get("home_pitcher_id"), g["season"])]:
            if pid and f"{pid}_{season}" not in cache:
                needed.add((pid, season))

    total = len(needed)
    if total and verbose:
        print(f"  투수 W/L/IP 수집 필요: {total}명 (캐시 없음)")

    for i, (pid, season) in enumerate(needed):
        get_pitcher_stats(pid, season, cache=cache, save=False)
        if verbose and (i+1) % 50 == 0:
            print(f"    {i+1}/{total} 완료…")
        time.sleep(0.05)

    if needed:
        _save_stats_cache(cache)

    for g in games:
        away_pid = g.get("away_pitcher_id") or 0
        home_pid = g.get("home_pitcher_id") or 0
        a = get_pitcher_stats(away_pid, g["season"], cache=cache, save=False) if away_pid else {}
        h = get_pitcher_stats(home_pid, g["season"], cache=cache, save=False) if home_pid else {}
        g["away_pitcher_wins"]   = a.get("wins", 0)
        g["away_pitcher_losses"] = a.get("losses", 0)
        g["away_pitcher_ip"]     = a.get("ip", 0.0)
        g["home_pitcher_wins"]   = h.get("wins", 0)
        g["home_pitcher_losses"] = h.get("losses", 0)
        g["home_pitcher_ip"]     = h.get("ip", 0.0)

    return games


if __name__ == "__main__":
    print("=== 과거 데이터 수집 테스트 ===")
    games = fetch_all_seasons([2024])
    print(f"2024: {len(games)}경기")
    print("샘플:", games[0])
    games = enrich_with_era(games[:10], verbose=True)
    print("ERA 보강 샘플:", games[0].get("away_starter_era"), games[0].get("home_starter_era"))
