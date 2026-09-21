"""
당일 라인업 수집 + 미공개 시 fallback
MLB Stats API: /v1/schedule?gamePk={pk}&hydrate=lineups

라인업 미공개 시:
  - 투수: 선발 로테이션 추정 (미정 레이블)
  - 타자: 전날 실제 출장 라인업 사용 (LHP/RHP 스플릿 분석)
"""
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List

BASE = "https://statsapi.mlb.com/api/v1"


# ─── 당일 확정 라인업 ────────────────────────────────────────────────

def get_game_lineup(game_pk: int) -> Optional[Dict[str, List[dict]]]:
    """
    경기 라인업 수집
    Returns:
        {
          "away": [{"id": int, "name": str, "pos": str}, ...],
          "home": [{"id": int, "name": str, "pos": str}, ...],
        }
        or None (라인업 미공개 or 오류)
    """
    try:
        r = requests.get(f"{BASE}/schedule", params={
            "gamePk": game_pk,
            "hydrate": "lineups",
        }, timeout=12)
        r.raise_for_status()
        data = r.json()

        dates = data.get("dates", [])
        if not dates:
            return None
        games = dates[0].get("games", [])
        if not games:
            return None

        lineups = games[0].get("lineups")
        if not lineups:
            return None   # 아직 라인업 미공개

        away_raw = lineups.get("awayPlayers", [])
        home_raw = lineups.get("homePlayers", [])

        if not away_raw or not home_raw:
            return None

        def _parse(players):
            return [
                {
                    "id":   p["id"],
                    "name": p.get("fullName", ""),
                    "pos":  p.get("primaryPosition", {}).get("abbreviation", ""),
                }
                for p in players if "id" in p
            ]

        return {
            "away": _parse(away_raw),
            "home": _parse(home_raw),
        }

    except Exception:
        return None


# ─── 투수 정보 ───────────────────────────────────────────────────────

def get_pitcher_handedness(pitcher_id: int) -> str:
    """
    투수 투구 방향 조회
    Returns: 'L' or 'R' (기본 'R')
    """
    try:
        r = requests.get(f"{BASE}/people/{pitcher_id}", timeout=10)
        r.raise_for_status()
        people = r.json().get("people", [])
        if people:
            return people[0].get("pitchHand", {}).get("code", "R") or "R"
    except Exception:
        pass
    return "R"


# ─── 전날 라인업 ─────────────────────────────────────────────────────

def get_previous_day_lineup(team_id: int, game_date: str) -> Optional[List[dict]]:
    """
    전날 실제 출장 타자 라인업 조회
    game_date: 'YYYY-MM-DD' (오늘 날짜 → 어제 경기 조회)
    Returns: [{"id", "name", "pos"}, ...] or None
    """
    try:
        today_dt = datetime.strptime(game_date, "%Y-%m-%d")
        prev_dt  = today_dt - timedelta(days=1)
        prev_str = prev_dt.strftime("%Y-%m-%d")

        r = requests.get(f"{BASE}/schedule", params={
            "teamId":    team_id,
            "startDate": prev_str,
            "endDate":   prev_str,
            "sportId":   1,
            "hydrate":   "lineups",
        }, timeout=12)
        r.raise_for_status()
        data = r.json()

        dates = data.get("dates", [])
        if not dates:
            return None
        games = dates[0].get("games", [])
        if not games:
            return None

        # 팀 홈/원정 구분해서 해당 팀 라인업 추출
        game = games[0]
        lineups = game.get("lineups")
        if not lineups:
            return None

        away_team_id = game.get("teams", {}).get("away", {}).get("team", {}).get("id")
        home_team_id = game.get("teams", {}).get("home", {}).get("team", {}).get("id")

        if team_id == home_team_id:
            raw = lineups.get("homePlayers", [])
        elif team_id == away_team_id:
            raw = lineups.get("awayPlayers", [])
        else:
            return None

        if not raw:
            return None

        # 투수 제외한 타자만 (P, SP, RP 포지션 제외)
        batters = []
        for p in raw:
            pos = p.get("primaryPosition", {}).get("abbreviation", "")
            if pos not in ("P", "SP", "RP", "CP"):
                batters.append({
                    "id":   p["id"],
                    "name": p.get("fullName", ""),
                    "pos":  pos,
                    "source": "prev_day",
                })
        return batters if batters else None

    except Exception:
        return None


# ─── 선발 로테이션 추정 ──────────────────────────────────────────────

def estimate_rotation_pitcher(team_id: int, game_date: str) -> Optional[dict]:
    """
    선발 로테이션 기반 당일 예상 선발투수 추정
    - 최근 30일 경기에서 probablePitcher 수집
    - 각 투수의 마지막 선발 날짜 기준 ~5일 주기로 다음 선발 추정

    Returns: {
        "id": int, "name": str, "handedness": str,
        "last_start": str, "estimated": True
    } or None
    """
    try:
        today_dt   = datetime.strptime(game_date, "%Y-%m-%d")
        start_dt   = today_dt - timedelta(days=35)
        start_str  = start_dt.strftime("%Y-%m-%d")
        today_str  = today_dt.strftime("%Y-%m-%d")

        r = requests.get(f"{BASE}/schedule", params={
            "teamId":    team_id,
            "startDate": start_str,
            "endDate":   today_str,
            "sportId":   1,
            "hydrate":   "probablePitcher",
        }, timeout=15)
        r.raise_for_status()
        data = r.json()

        # 날짜별 선발투수 수집
        pitcher_last_start: Dict[int, dict] = {}
        for date_entry in data.get("dates", []):
            d_str = date_entry.get("date", "")
            for game in date_entry.get("games", []):
                teams = game.get("teams", {})
                # 해당 팀이 원정/홈 중 어디인지 확인
                for side in ("away", "home"):
                    t = teams.get(side, {})
                    if t.get("team", {}).get("id") == team_id:
                        pp = t.get("probablePitcher")
                        if pp and pp.get("id"):
                            pid  = pp["id"]
                            name = pp.get("fullName", "")
                            # 가장 최신 선발 날짜만 저장
                            if pid not in pitcher_last_start or d_str > pitcher_last_start[pid]["last_start"]:
                                pitcher_last_start[pid] = {
                                    "id":         pid,
                                    "name":       name,
                                    "last_start": d_str,
                                }

        if not pitcher_last_start:
            return None

        # 다음 선발 추정: 마지막 선발 + 실제 평균 주기(최근 2경기 간격) 기반
        # 고정 5일 가정은 야마모토처럼 7일 주기 투수를 오추정하는 원인이었음
        # 개선:
        #   1. 투수별 최근 2개 선발 날짜로 실제 주기 계산
        #   2. 주기 범위 4~8일로 클램프 (너무 긴 부상 복귀는 제외)
        #   3. 추정 다음 선발이 오늘 ±2일 범위 내에 없으면 해당 투수 제외
        #      → 범위 밖이면 오늘 선발이 아니라고 판단 (Yamamoto 9/15→+7=9/22, 오늘 9/20: 차이 2일 → 이전엔 통과됐지만 이제 ±1일로 더 좁힘)

        # 투수별 마지막 2경기 날짜를 모두 수집 (주기 계산용)
        pitcher_starts: Dict[int, list] = {}
        for date_entry in data.get("dates", []):
            d_str = date_entry.get("date", "")
            for game in date_entry.get("games", []):
                teams = game.get("teams", {})
                for side in ("away", "home"):
                    t = teams.get(side, {})
                    if t.get("team", {}).get("id") == team_id:
                        pp = t.get("probablePitcher")
                        if pp and pp.get("id"):
                            pid = pp["id"]
                            if pid not in pitcher_starts:
                                pitcher_starts[pid] = []
                            if d_str not in pitcher_starts[pid]:
                                pitcher_starts[pid].append(d_str)

        today_ord = today_dt.toordinal()
        best_pitcher = None
        best_diff    = 9999
        TOLERANCE    = 1   # 오늘 ±1일 이내만 허용 (기존 무제한 → 좁힘)

        for pid, info in pitcher_last_start.items():
            last_dt = datetime.strptime(info["last_start"], "%Y-%m-%d")

            # 실제 투구 간격 계산 (가능하면)
            starts_sorted = sorted(pitcher_starts.get(pid, []))
            if len(starts_sorted) >= 2:
                # 마지막 두 경기 간격
                d1 = datetime.strptime(starts_sorted[-2], "%Y-%m-%d")
                d2 = datetime.strptime(starts_sorted[-1], "%Y-%m-%d")
                actual_interval = (d2 - d1).days
                # 비정상 범위는 기본값 5일로 클램프
                interval = max(4, min(8, actual_interval))
            else:
                interval = 5  # 데이터 부족 시 기본값

            next_start = last_dt + timedelta(days=interval)
            diff       = abs(next_start.toordinal() - today_ord)

            # 허용 오차 ±TOLERANCE일 밖이면 오늘 선발 아님 → 제외
            if diff > TOLERANCE:
                continue

            if diff < best_diff:
                best_diff    = diff
                best_pitcher = info

        if not best_pitcher:
            return None

        # 투구 방향 조회
        handedness = get_pitcher_handedness(best_pitcher["id"])

        return {
            "id":          best_pitcher["id"],
            "name":        best_pitcher["name"],
            "handedness":  handedness,
            "last_start":  best_pitcher["last_start"],
            "estimated":   True,
        }

    except Exception:
        return None


# ─── 통합 fallback 라인업 ────────────────────────────────────────────

def get_fallback_lineup(team_id: int, game_date: str, opp_team_id: int) -> dict:
    """
    라인업 미공개 시 fallback 데이터 수집

    Returns:
    {
        "batters":     [{"id","name","pos","source"}, ...] or None,
        "est_pitcher": {"id","name","handedness","estimated":True} or None,
        "bat_source":  "prev_day" | "team_stats",
    }
    """
    # 전날 라인업
    prev_batters = get_previous_day_lineup(team_id, game_date)

    return {
        "batters":    prev_batters,
        "bat_source": "prev_day" if prev_batters else "team_stats",
    }
