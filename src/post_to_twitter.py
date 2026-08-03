#!/usr/bin/env python3
"""
MLB Scorecard — Twitter/X Auto Poster
src/post_to_twitter.py

Usage:
    python3 src/post_to_twitter.py --date 2025-08-04
    python3 src/post_to_twitter.py  # 오늘 날짜 자동
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
API_KEY         = os.environ.get("TWITTER_API_KEY",      "dJVGGTTN7C6vmG2z7iqG0ruM7")
API_SECRET      = os.environ.get("TWITTER_API_SECRET",   "OtBYpvgLcVG8mNp5p3ZiQqnb3Bv3lBYT8UaNoXlfcseIEQLU7g")
ACCESS_TOKEN    = os.environ.get("TWITTER_ACCESS_TOKEN", "2084157502879612928-iKOe2WRJG6fgd2wwNQ8KX6n82j5E4K")
ACCESS_SECRET   = os.environ.get("TWITTER_ACCESS_SECRET","CbdHpCOe8GNpRG1u9OMZV8zcGmj0BSGmG0PtsVucxCQ3P")

TWITTER_URL = "https://api.twitter.com/2/tweets"

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "output"

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

def sp_name(pitcher_str: str) -> str:
    name = pitcher_str.split("(")[0].strip()
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0][0]}. {parts[-1]}"
    return name

def load_predictions() -> list:
    """output/predictions.json 읽기 (scorecard_pipeline이 생성)"""
    pred_file = DATA_DIR / "predictions.json"
    if pred_file.exists():
        with open(pred_file, encoding="utf-8") as f:
            return json.load(f)
    return []

def get_top_picks(preds: list, n=5) -> list:
    picks = []
    for g in preds:
        wp = g.get("win_prob", {})
        if not wp: continue
        away_pct = wp.get("away", 50)
        home_pct = wp.get("home", 50)
        if home_pct >= away_pct and home_pct >= 55:
            picks.append({"pick": abbr(g.get("home","???")), "opp": abbr(g.get("away","???")),
                          "away": abbr(g.get("away","???")), "home": abbr(g.get("home","???")),
                          "pct": f"{home_pct:.0f}%", "pct_val": home_pct,
                          "away_sp": sp_name(g.get("away_pitcher","TBD")),
                          "home_sp": sp_name(g.get("home_pitcher","TBD"))})
        elif away_pct > home_pct and away_pct >= 55:
            picks.append({"pick": abbr(g.get("away","???")), "opp": abbr(g.get("home","???")),
                          "away": abbr(g.get("away","???")), "home": abbr(g.get("home","???")),
                          "pct": f"{away_pct:.0f}%", "pct_val": away_pct,
                          "away_sp": sp_name(g.get("away_pitcher","TBD")),
                          "home_sp": sp_name(g.get("home_pitcher","TBD"))})
    picks.sort(key=lambda x: x["pct_val"], reverse=True)
    return picks[:n]

def format_date_label(post_date: str) -> str:
    """2025-08-04 → AUG 4, 2025"""
    dt = datetime.strptime(post_date, "%Y-%m-%d")
    return dt.strftime("%b %-d, %Y").upper()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    post_date = args.date
    date_label = format_date_label(post_date)

    # 예측 데이터 로드
    preds = load_predictions()
    top_picks = get_top_picks(preds, n=5)
    total_games = len(preds)

    # ── Tweet 1: 커버 ─────────────────────────────────────────────────────────
    tweet1 = (
        f"⚾ MLB SCORECARD | {date_label}\n\n"
        f"TODAY'S PICKS — {total_games} GAMES\n\n"
        "Data-driven predictions. Every pick tracked.\n"
        "No hype. Just numbers.\n\n"
        "Full card 👇\n\n@MLB_Scorecard"
    )

    # ── Tweet 2: TOP PICK ────────────────────────────────────────────────────
    if top_picks:
        t = top_picks[0]
        tweet2 = (
            f"⭐ TOP PICK | {date_label}\n\n"
            f"{t['away']} @ {t['home']} → 🔵 {t['pick']} {t['pct']}\n\n"
            f"SP: {t['away_sp']} vs {t['home_sp']}\n\n"
            "Scorecard: SP(30%) + BP(20%) + BAT(35%) + SIT(15%)\n\n"
            f"#{t['pick']} #MLBPicks #Baseball"
        )
    else:
        tweet2 = (
            f"⭐ TOP PICK | {date_label}\n\n"
            "Analysis in progress — lineup data loading.\n"
            "Check back at game time for confirmed picks.\n\n"
            "#MLB #MLBPicks #BaseballAnalytics"
        )

    # ── Tweet 3: FULL CARD ───────────────────────────────────────────────────
    if top_picks:
        picks_lines = "\n".join([
            f"{'⭐' if i==0 else '✅'} {p['pick']} {p['pct']} vs {p['opp']}"
            for i, p in enumerate(top_picks)
        ])
        tweet3 = (
            f"📋 {date_label} FULL CARD\n\n"
            f"{picks_lines}\n\n"
            "All picks tracked. No cherry-picking.\n"
            "#MLB #BaseballAnalytics #MLBPicks"
        )
    else:
        tweet3 = (
            f"📋 {date_label} — PICKS LOADING\n\n"
            "Lineup confirmations in progress.\n"
            "Final card posted at game time.\n\n"
            "#MLB #MLBPicks"
        )

    # ── Tweet 4: HOW WE SCORE ────────────────────────────────────────────────
    tweet4 = (
        "📊 HOW WE BUILD THE SCORECARD\n\n"
        "⚾ SP Score — 30%\n"
        "🔥 Bullpen — 20%\n"
        "🏏 Batting — 35%\n"
        "📍 Situational — 15%\n\n"
        "Transparent. Data-driven. No hype.\n"
        "Follow → @MLB_Scorecard"
    )

    tweets = [tweet1, tweet2, tweet3, tweet4]

    if args.dry_run:
        print("🧪 DRY RUN — 실제 포스팅 안 함\n")
        for i, t in enumerate(tweets, 1):
            print(f"[Tweet {i}]\n{t}\n{'─'*40}")
        return

    print(f"🐦 Twitter 포스팅 시작 | {date_label}\n")
    for i, text in enumerate(tweets, 1):
        try:
            result = post_tweet(text)
            tweet_id = result.get("data", {}).get("id", "N/A")
            print(f"✅ Tweet {i} 완료 | ID: {tweet_id}")
            print(f"   https://twitter.com/MLB_Scorecard/status/{tweet_id}\n")
        except Exception as e:
            print(f"❌ Tweet {i} 실패: {e}\n")

    print("🎉 Twitter 포스팅 완료!")

if __name__ == "__main__":
    main()
