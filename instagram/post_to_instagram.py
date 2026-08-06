#!/usr/bin/env python3
"""
MLB Scorecard — Instagram Auto Poster (단일 텍스트 스타일 포스트)
instagram/post_to_instagram.py

Usage:
    python3 instagram/post_to_instagram.py --date 2025-08-04
    python3 instagram/post_to_instagram.py --type results
"""

import os
import sys
import json
import argparse
import requests
from datetime import date, datetime
from pathlib import Path

# ── CONFIG ───────────────────────────────────────────────────────────────────
ZERNIO_API_KEY       = os.environ.get("ZERNIO_API_KEY", "sk_9904830b1af95a69b9a7824939d5c1ffd4040206eab7e954a770b1523a6a582c")
ZERNIO_BASE_URL      = "https://zernio.com/api/v1"
INSTAGRAM_ACCOUNT_ID = os.environ.get("INSTAGRAM_ACCOUNT_ID", "6a7108fedf17280d9336543f")

HEADERS = {
    "Authorization": f"Bearer {ZERNIO_API_KEY}",
    "Content-Type": "application/json",
}

BASE_DIR   = Path(__file__).parent.parent
SLIDES_DIR = BASE_DIR / "slides"
DATA_DIR   = BASE_DIR / "output"

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

def build_prediction_caption(post_date: str, preds: list) -> str:
    date_label = format_date_label(post_date)
    top_picks = get_top_picks(preds, n=5)
    total = len(preds)

    if not top_picks:
        return (
            f"⚾ MLB Picks | {date_label}\n\n"
            f"Analyzing {total} games today.\n"
            "Lineup data still loading — check back closer to first pitch.\n\n"
            "#MLB #MLBPicks #BaseballAnalytics #DataDriven #Baseball"
        )

    lines = []
    for i, p in enumerate(top_picks):
        star = "⭐" if i == 0 else "✅"
        lines.append(f"{star} {p['vs']} → {p['pick']} {p['pct']:.0f}%")

    picks_block = "\n".join(lines)

    caption = (
        f"⚾ MLB Picks | {date_label}\n\n"
        f"{picks_block}\n\n"
        f"Analyzing {total} games today. Every pick tracked publicly — no cherry-picking.\n\n"
        "#MLB #MLBPicks #BaseballAnalytics #DataDriven #Baseball "
        f"#{top_picks[0]['pick']}"
    )
    return caption

def build_results_caption(post_date: str, preds: list) -> str:
    date_label = format_date_label(post_date)
    finished = [g for g in preds if g.get("actual_winner")]

    if not finished:
        return (
            f"📊 MLB Results | {date_label}\n\n"
            "Results being finalized. Check back shortly.\n\n"
            "#MLB #MLBResults #Baseball"
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
        summary_block += f"\n+{len(result_lines)-5} more games"

    caption = (
        f"📊 MLB Results | {date_label}\n\n"
        f"{correct}W — {total-correct}L ({pct:.0f}%)\n\n"
        f"{summary_block}\n\n"
        "All picks tracked from the start. No edits. No excuses.\n\n"
        "#MLB #MLBPicks #BaseballAnalytics #DataDriven #Baseball"
    )
    return caption

def capture_homepage() -> Path:
    """홈페이지 스크린샷 캡처 → Path 반환"""
    output_path = DATA_DIR / "homepage.png"
    try:
        from playwright.sync_api import sync_playwright
        print("📸 홈페이지 스크린샷 캡처 중...")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page(viewport={"width": 1080, "height": 1080})
            page.goto("https://www.mlb-scorecard.com", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)
            page.screenshot(
                path=str(output_path),
                clip={"x": 0, "y": 0, "width": 1080, "height": 1080},
                type="png",
            )
            browser.close()
        print(f"✅ 스크린샷 저장: {output_path}")
        return output_path
    except Exception as e:
        print(f"⚠️  스크린샷 실패: {e}")
        return None

def upload_image(file_path: Path) -> str:
    """이미지 업로드 → 공개 URL 반환"""
    print(f"  📤 업로드 중: {file_path.name}")
    presign_resp = requests.post(
        f"{ZERNIO_BASE_URL}/media/presign",
        headers=HEADERS,
        json={"filename": file_path.name, "contentType": "image/png"},
    )
    presign_resp.raise_for_status()
    data = presign_resp.json()
    with open(file_path, "rb") as f:
        requests.put(data["uploadUrl"], data=f, headers={"Content-Type": "image/png"}).raise_for_status()
    print(f"  ✅ 업로드 완료: {data['publicUrl']}")
    return data["publicUrl"]

def post_single(image_url: str, caption: str) -> dict:
    """단일 이미지 포스트"""
    print("\n📱 Instagram 포스팅 중...")
    payload = {
        "content": caption,
        "mediaItems": [{"type": "image", "url": image_url}],
        "platforms": [{"platform": "instagram", "accountId": INSTAGRAM_ACCOUNT_ID}],
        "publishNow": True,
    }
    resp = requests.post(f"{ZERNIO_BASE_URL}/posts", headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--type", default="prediction", choices=["prediction", "results"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    preds = load_predictions()

    # 캡션 생성
    if args.type == "results":
        caption = build_results_caption(args.date, preds)
    else:
        caption = build_prediction_caption(args.date, preds)

    print(f"📝 Caption preview:\n{'─'*40}\n{caption}\n{'─'*40}")

    if args.dry_run:
        print("🧪 DRY RUN — 실제 포스팅 안 함")
        return

    # 홈페이지 스크린샷 캡처
    cover_path = capture_homepage()

    if not cover_path or not cover_path.exists():
        print("❌ 이미지 없음 — 포스팅 중단")
        sys.exit(1)

    image_url = upload_image(cover_path)
    result = post_single(image_url, caption)

    print("\n🎉 Instagram 포스팅 완료!")
    print(f"   Post ID: {result.get('post', {}).get('_id', 'N/A')}")

if __name__ == "__main__":
    main()
