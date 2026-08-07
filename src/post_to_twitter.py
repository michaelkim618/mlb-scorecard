#!/usr/bin/env python3
"""
MLB Scorecard — Twitter/X Auto Poster
src/post_to_twitter.py

Usage:
    python3 src/post_to_twitter.py --date 2025-08-04
    python3 src/post_to_twitter.py --type results
"""

import os
import sys
import json
import argparse
import requests
from datetime import date, datetime
from pathlib import Path
from requests_oauthlib import OAuth1

# ── CONFIG ───────────────────────────────────────────────────────────────────
API_KEY      = os.environ.get("TWITTER_API_KEY",      "dJVGGTTN7C6vmG2z7iqG0ruM7")
API_SECRET   = os.environ.get("TWITTER_API_SECRET",   "OtBYpvgLcVG8mNp5p3ZiQqnb3Bv3lBYT8UaNoXlfcseIEQLU7g")
ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN", "2084157502879612928-iKOe2WRJG6fgd2wwNQ8KX6n82j5E4K")
ACCESS_SECRET= os.environ.get("TWITTER_ACCESS_SECRET","CbdHpCOe8GNpRG1u9OMZV8zcGmj0BSGmG0PtsVucxCQ3P")

TWITTER_URL = "https://api.twitter.com/2/tweets"
BASE_DIR    = Path(__file__).parent.parent
DATA_DIR    = BASE_DIR / "output"

def get_auth():
    return OAuth1(API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET)

def post_tweet(text: str) -> dict:
    resp = requests.post(
        TWITTER_URL,
        auth=get_auth(),
        json={"text": text},
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()

def abbr(team_name: str) -> str:
    MAP = {
        "Los Angeles Dodgers":"LAD","Chicago Cubs":"CHC","Boston Red Sox":"BOS",
        "Chicago White Sox":"CHW","Philadelphia Phillies":"PHI","Washington Nationals":"WSH",
        "Cincinnati Reds":"CIN","Oakland Athletics":"OAK","Houston Astros":"HOU",
        "Toronto Blue Jays":"TOR","New York Yankees":"NYY","New York Mets":"NYM",
        "Atlanta Braves":"ATL","Miami Marlins":"MIA","St. Louis Cardinals":"STL",
        "Milwaukee Brewers":"MIL","Minnesota Twins":"MIN","Kansas City Royals":"KC",
        "Cleveland Guardians":"CLE","Detroit Tigers":"DET","Tampa Bay Rays":"TB",
        "Baltimore Orioles":"BAL","Los Angeles Angels":"LAA","Seattle Mariners":"SEA",
        "Texas Rangers":"TEX","Colorado Rockies":"COL","Arizona Diamondbacks":"ARI",
        "San Diego Padres":"SD","San Francisco Giants":"SF","Pittsburgh Pirates":"PIT",
    }
    for full, short in MAP.items():
        if full.lower() in team_name.lower():
            return short
    return team_name[:3].upper()

def load_predictions() -> list:
    pred_file = DATA_DIR / "predictions.json"
    if pred_file.exists():
        with open(pred_file, encoding="utf-8") as f:
            return json.load(f)
    return []


def load_yesterday_record(today: str):
    """season_results.json에서 전날 W-L 성적 계산"""
    # 웹 레포 위치 탐색
    web_candidates = [
        BASE_DIR / "mlb-scorecard-web",           # GitHub Actions
        BASE_DIR.parent / "mlb-scorecard-web",    # 로컬 (같은 레벨)
        Path.home() / "Desktop" / "mlb-scorecard-web",  # 로컬 Desktop
    ]
    results_file = None
    for w in web_candidates:
        f = w / "public" / "season_results.json"
        if f.exists():
            results_file = f
            break

    if not results_file:
        return None

    try:
        data = json.loads(results_file.read_text(encoding="utf-8"))
        games = data.get("games", [])
    except Exception:
        return None

    from datetime import datetime, timedelta
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    day_games = [g for g in games if g.get("date") == yesterday and "actual_winner" in g]

    if not day_games:
        return None

    wins = sum(1 for g in day_games if g.get("correct"))
    total = len(day_games)
    pct = wins / total * 100
    return {"wins": wins, "losses": total - wins, "total": total, "pct": pct, "date": yesterday}


def yesterday_comment(rec: dict) -> str:
    """성적에 따른 코멘트 (트위터용 — 짧고 임팩트 있게)"""
    pct = rec["pct"]
    w, l = rec["wins"], rec["losses"]
    if pct >= 70:
        return f"🔥 Yesterday: {w} Won - {l} Lost ({pct:.0f}% Accuracy) — AWESOME night!"
    elif pct >= 60:
        return f"✅ Yesterday: {w} Won - {l} Lost ({pct:.0f}% Accuracy) — solid call!"
    elif pct >= 50:
        return f"📈 Yesterday: {w} Won - {l} Lost ({pct:.0f}% Accuracy) — we'll be back stronger."
    else:
        return f"😤 Yesterday: {w} Won - {l} Lost ({pct:.0f}% Accuracy) — rough one. Bounce back time."

def get_top_picks(preds: list, n=5) -> list:
    picks = []
    skipped_tbd = 0
    for g in preds:
        wp = g.get("win_prob", {})
        if not wp:
            continue

        # SP TBD 경기는 Top Pick에서 제외 (신뢰도 낮음)
        sp_tbd = g.get("sp_tbd", {})
        if sp_tbd.get("any", False):
            skipped_tbd += 1
            continue

        # bat_source=team_stats 경기는 Top Pick에서 제외 (라인업 없어 신뢰도 40%)
        bat_source = g.get("scorecard", {}).get("bat_source", "")
        if bat_source == "team_stats":
            skipped_tbd += 1
            continue

        away_pct = wp.get("away", 50)
        home_pct = wp.get("home", 50)
        if home_pct >= away_pct and home_pct >= 55:
            picks.append({
                "pick": abbr(g.get("home", "???")),
                "opp":  abbr(g.get("away", "???")),
                "pct":  home_pct,
                "vs":   f"{abbr(g.get('away','???'))} @ {abbr(g.get('home','???'))}",
            })
        elif away_pct > home_pct and away_pct >= 55:
            picks.append({
                "pick": abbr(g.get("away", "???")),
                "opp":  abbr(g.get("home", "???")),
                "pct":  away_pct,
                "vs":   f"{abbr(g.get('away','???'))} @ {abbr(g.get('home','???'))}",
            })
    picks.sort(key=lambda x: x["pct"], reverse=True)
    result = picks[:n]
    # skipped_tbd 정보를 첫 번째 pick에 메타데이터로 붙여서 전달
    if result and skipped_tbd > 0:
        result[0]["skipped_tbd"] = skipped_tbd
    elif skipped_tbd > 0:
        # 픽이 없는데 TBD만 있는 경우
        return [{"skipped_tbd": skipped_tbd}]
    return result

def format_date_label(post_date: str) -> str:
    dt = datetime.strptime(post_date, "%Y-%m-%d")
    return dt.strftime("%b %-d").upper()

def build_prediction_tweet(post_date: str, preds: list) -> str:
    date_label = format_date_label(post_date)
    top_picks = get_top_picks(preds, n=5)
    total = len(preds)
    rec = load_yesterday_record(post_date)

    if not top_picks:
        yday = f"\n{yesterday_comment(rec)}\n" if rec else ""
        return (
            f"⚾ MLB Picks | {date_label}\n"
            f"{yday}\n"
            f"Analyzing {total} games — lineup data loading.\n"
            "Final picks dropped at game time.\n\n"
            "#MLB #MLBPicks #Baseball"
        )

    skipped_tbd = top_picks[0].get("skipped_tbd", 0) if top_picks else 0
    real_picks = [p for p in top_picks if "vs" in p]

    lines = []
    for i, p in enumerate(real_picks):
        star = "⭐" if i == 0 else "✅"
        lines.append(f"{star} {p['vs']} → {p['pick']} {p['pct']:.0f}%")
    picks_block = "\n".join(lines)

    tbd_note = f"⚠️ {skipped_tbd} games excluded (SP not yet announced)\n" if skipped_tbd > 0 else ""
    yday_line = f"\n{yesterday_comment(rec)}\n" if rec else ""
    top_tag = f"#{real_picks[0]['pick'].replace(' ', '')}" if real_picks else "#Baseball"

    tweet = (
        f"⚾ MLB Picks | {date_label}\n"
        f"{yday_line}\n"
        f"Today's Top Picks:\n"
        f"{picks_block}\n\n"
        f"{tbd_note}"
        f"{total} games tracked. No cherry-picking, ever.\n\n"
        f"📊 Full analysis & all picks → mlb-scorecard.com\n\n"
        f"#MLB #MLBPicks {top_tag}"
    )
    return tweet

def build_results_tweet(post_date: str, preds: list) -> str:
    date_label = format_date_label(post_date)

    finished = [g for g in preds if g.get("actual_winner")]
    if not finished:
        return (
            f"📊 MLB Results | {date_label}\n\n"
            "Results being finalized — check back shortly.\n\n"
            "#MLB #MLBResults"
        )

    correct = 0
    result_lines = []
    for g in finished:
        wp = g.get("win_prob", {})
        away_pct = wp.get("away", 50)
        home_pct = wp.get("home", 50)
        pick = abbr(g["home"]) if home_pct >= away_pct else abbr(g["away"])
        actual = abbr(g["actual_winner"])
        hit = pick == actual
        if hit:
            correct += 1
        mark = "✅" if hit else "❌"
        result_lines.append(f"{mark} {abbr(g['away'])} @ {abbr(g['home'])} → {pick} ({actual})")

    total = len(finished)
    pct = correct / total * 100

    # 성적 코멘트
    if pct >= 70:
        verdict = f"🔥 {correct} Won - {total-correct} Lost ({pct:.0f}% Accuracy) — AWESOME night! The data delivered."
    elif pct >= 60:
        verdict = f"✅ {correct} Won - {total-correct} Lost ({pct:.0f}% Accuracy) — solid night. We'll take it."
    elif pct >= 50:
        verdict = f"📈 {correct} Won - {total-correct} Lost ({pct:.0f}% Accuracy) — close, but we can do better."
    else:
        verdict = f"😤 {correct} Won - {total-correct} Lost ({pct:.0f}% Accuracy) — rough one. Back tomorrow."

    top5 = result_lines[:5]
    summary_block = "\n".join(top5)
    if len(result_lines) > 5:
        summary_block += f"\n... +{len(result_lines)-5} more"

    tweet = (
        f"📊 MLB Results | {date_label}\n\n"
        f"{verdict}\n\n"
        f"{summary_block}\n\n"
        f"All picks tracked from day 1. No edits. No excuses.\n\n"
        f"📊 Full season record → mlb-scorecard.com\n\n"
        "#MLB #MLBPicks #Baseball"
    )
    return tweet

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--type", default="prediction", choices=["prediction", "results"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    preds = load_predictions()

    if args.type == "results":
        tweet = build_results_tweet(args.date, preds)
    else:
        tweet = build_prediction_tweet(args.date, preds)

    print(f"📝 Tweet preview:\n{'─'*40}\n{tweet}\n{'─'*40}")
    print(f"   Length: {len(tweet)} chars")

    if args.dry_run:
        print("🧪 DRY RUN — 실제 포스팅 안 함")
        return

    try:
        result = post_tweet(tweet)
        tweet_id = result.get("data", {}).get("id", "N/A")
        print(f"✅ Tweet 완료 | ID: {tweet_id}")
        print(f"   https://twitter.com/MLB_Scorecard/status/{tweet_id}")
    except Exception as e:
        print(f"❌ Tweet 실패: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
