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

def get_top_picks(preds: list, n=5) -> list:
    picks = []
    for g in preds:
        wp = g.get("win_prob", {})
        if not wp:
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
    return picks[:n]

def format_date_label(post_date: str) -> str:
    dt = datetime.strptime(post_date, "%Y-%m-%d")
    return dt.strftime("%b %-d").upper()

def build_prediction_tweet(post_date: str, preds: list) -> str:
    date_label = format_date_label(post_date)
    top_picks = get_top_picks(preds, n=5)
    total = len(preds)

    if not top_picks:
        return (
            f"⚾ MLB Picks | {date_label}\n\n"
            f"Analyzing {total} games today — lineup data loading.\n"
            "Final picks posted at game time.\n\n"
            "#MLB #MLBPicks #Baseball"
        )

    lines = []
    for i, p in enumerate(top_picks):
        star = "⭐" if i == 0 else "✅"
        lines.append(f"{star} {p['vs']} → {p['pick']} {p['pct']:.0f}%")

    picks_block = "\n".join(lines)

    tweet = (
        f"⚾ MLB Picks | {date_label}\n\n"
        f"{picks_block}\n\n"
        f"{total} games tracked. Every pick public.\n\n"
        f"#MLB #MLBPicks #{top_picks[0]['pick']}"
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

    top5 = result_lines[:5]
    summary_block = "\n".join(top5)
    if len(result_lines) > 5:
        summary_block += f"\n... +{len(result_lines)-5} more"

    tweet = (
        f"📊 MLB Results | {date_label}\n\n"
        f"{correct}W-{total-correct}L ({pct:.0f}%)\n\n"
        f"{summary_block}\n\n"
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
