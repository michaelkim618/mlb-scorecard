"""
Kalshi 공개 API 클라이언트
KXMLBGAME 시리즈에서 MLB 경기 승패 마켓을 찾아 implied probability 반환
"""
import requests
from typing import Optional, Dict, Tuple

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# MLB 팀명 → Kalshi ticker 코드 매핑
TEAM_TO_KALSHI: Dict[str, str] = {
    "Arizona Diamondbacks":     "AZ",
    "Atlanta Braves":           "ATL",
    "Baltimore Orioles":        "BAL",
    "Boston Red Sox":           "BOS",
    "Chicago Cubs":             "CHC",
    "Chicago White Sox":        "CWS",
    "Cincinnati Reds":          "CIN",
    "Cleveland Guardians":      "CLE",
    "Colorado Rockies":         "COL",
    "Detroit Tigers":           "DET",
    "Houston Astros":           "HOU",
    "Kansas City Royals":       "KC",
    "Los Angeles Angels":       "LAA",
    "Los Angeles Dodgers":      "LAD",
    "Miami Marlins":            "MIA",
    "Milwaukee Brewers":        "MIL",
    "Minnesota Twins":          "MIN",
    "New York Mets":            "NYM",
    "New York Yankees":         "NYY",
    "Philadelphia Phillies":    "PHI",
    "Pittsburgh Pirates":       "PIT",
    "San Diego Padres":         "SD",
    "San Francisco Giants":     "SF",
    "Seattle Mariners":         "SEA",
    "St. Louis Cardinals":      "STL",
    "Tampa Bay Rays":           "TB",
    "Texas Rangers":            "TEX",
    "Toronto Blue Jays":        "TOR",
    "Washington Nationals":     "WSH",
    "Athletics":                "ATH",
    "Oakland Athletics":        "ATH",
}


def _fetch_events_for_date(game_date: str, limit: int = 100) -> list:
    """
    game_date: YYYY-MM-DD
    Kalshi ticker에 날짜가 포함되어 있으므로 해당 날짜 이벤트만 필터링
    """
    # Kalshi ticker 날짜 형식: 26JUN23 (연도2자리+월영문3자리+일2자리)
    from datetime import datetime
    dt = datetime.strptime(game_date, "%Y-%m-%d")
    months = ["JAN","FEB","MAR","APR","MAY","JUN",
              "JUL","AUG","SEP","OCT","NOV","DEC"]
    date_code = f"{str(dt.year)[-2:]}{months[dt.month-1]}{dt.day:02d}"

    url = f"{KALSHI_BASE}/events"
    params = {"limit": limit, "series_ticker": "KXMLBGAME"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        all_events = resp.json().get("events", [])
    except Exception:
        return []

    return [e for e in all_events if date_code in e.get("event_ticker", "")]


def _get_market_price(event_ticker: str, home_team_code: str) -> Optional[float]:
    """
    event_ticker에서 홈팀 마켓의 yes_bid (implied probability) 반환.
    ticker 패턴: KXMLBGAME-26JUN262215ATLSF-ATL  (마지막 부분이 승리팀)
    """
    url = f"{KALSHI_BASE}/markets"
    params = {"event_ticker": event_ticker}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        markets = resp.json().get("markets", [])
    except Exception:
        return None

    # 홈팀 YES 마켓 찾기
    for m in markets:
        ticker = m.get("ticker", "")
        yes_sub = m.get("yes_sub_title", "")
        # ticker 마지막 세그먼트가 팀 코드
        if ticker.endswith(f"-{home_team_code}"):
            bid = m.get("yes_bid_dollars")
            if bid and float(bid) > 0:
                return round(float(bid) * 100, 1)  # 0~100%

    # 마켓 두 개 중 팀코드로 못 찾으면 첫 번째 마켓 반환
    if markets:
        bid = markets[0].get("yes_bid_dollars")
        if bid and float(bid) > 0:
            return round(float(bid) * 100, 1)

    return None


def get_kalshi_prob(
    game_date: str,
    away_name: str,
    home_name: str,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Returns (home_implied_prob, away_implied_prob) as 0~100 floats,
    or (None, None) if no market found.
    """
    away_code = TEAM_TO_KALSHI.get(away_name)
    home_code = TEAM_TO_KALSHI.get(home_name)

    if not away_code or not home_code:
        return None, None

    events = _fetch_events_for_date(game_date)
    if not events:
        return None, None

    # ticker 안에 두 팀 코드가 모두 포함된 이벤트 찾기
    matched = None
    for e in events:
        t = e.get("event_ticker", "")
        if away_code in t and home_code in t:
            matched = e
            break

    if not matched:
        # 순서가 다를 수도 있으므로 sub_title로도 시도
        for e in events:
            sub = e.get("sub_title", "")
            if away_code in sub and home_code in sub:
                matched = e
                break

    if not matched:
        return None, None

    home_prob = _get_market_price(matched["event_ticker"], home_code)
    if home_prob is None:
        return None, None

    away_prob = round(100 - home_prob, 1)
    return home_prob, away_prob


if __name__ == "__main__":
    print("=== Kalshi MLB 마켓 테스트 ===")
    # 내일 경기 (현재 오픈된 마켓)
    home_p, away_p = get_kalshi_prob("2026-06-26", "Atlanta Braves", "San Francisco Giants")
    print(f"ATL @ SF:  홈(SF) = {home_p}%  어웨이(ATL) = {away_p}%")

    home_p2, away_p2 = get_kalshi_prob("2026-06-26", "New York Yankees", "Boston Red Sox")
    print(f"NYY @ BOS: 홈(BOS) = {home_p2}%  어웨이(NYY) = {away_p2}%")

    home_p3, away_p3 = get_kalshi_prob("2026-06-23", "Houston Astros", "Toronto Blue Jays")
    print(f"HOU @ TOR (어제): 홈(TOR) = {home_p3}%  어웨이(HOU) = {away_p3}%")
