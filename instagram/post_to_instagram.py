#!/usr/bin/env python3
"""
MLB Scorecard — Zernio Instagram Auto Poster
post_to_instagram.py

Usage:
    python3 post_to_instagram.py --date 2025-08-04
    python3 post_to_instagram.py  # 오늘 날짜 자동
"""

import os
import sys
import json
import argparse
import requests
from datetime import date
from pathlib import Path

# ── CONFIG ───────────────────────────────────────────────────────────────────
# 환경변수 우선, 없으면 직접 입력값 사용 (로컬 실행용)
ZERNIO_API_KEY       = os.environ.get("ZERNIO_API_KEY", "sk_9904830b1af95a69b9a7824939d5c1ffd4040206eab7e954a770b1523a6a582c")
ZERNIO_BASE_URL      = "https://zernio.com/api/v1"
INSTAGRAM_ACCOUNT_ID = os.environ.get("INSTAGRAM_ACCOUNT_ID", "6a7108fedf17280d9336543f")

HEADERS = {
    "Authorization": f"Bearer {ZERNIO_API_KEY}",
    "Content-Type": "application/json",
}

BASE_DIR = Path(__file__).parent.parent / "slides"

SLIDE_FILES = [
    "slide_01_cover.png",
    "slide_02_top_pick.png",
    "slide_03_full_card.png",
    "slide_04_how_we_score.png",
    "slide_05_results.png",
]

# ── STEP 1: 이미지 업로드 (presigned URL 방식) ────────────────────────────────
def upload_image(file_path: Path) -> str:
    """로컬 PNG → Zernio 서버에 업로드 → 공개 URL 반환"""
    print(f"  📤 업로드 중: {file_path.name}")

    # 1a. Presigned URL 요청
    presign_resp = requests.post(
        f"{ZERNIO_BASE_URL}/media/presign",
        headers=HEADERS,
        json={
            "filename": file_path.name,
            "contentType": "image/png",
        }
    )
    presign_resp.raise_for_status()
    presign_data = presign_resp.json()

    upload_url  = presign_data["uploadUrl"]
    public_url  = presign_data["publicUrl"]

    # 1b. 이미지 파일 PUT 업로드
    with open(file_path, "rb") as f:
        put_resp = requests.put(
            upload_url,
            data=f,
            headers={"Content-Type": "image/png"},
        )
    put_resp.raise_for_status()

    print(f"  ✅ 업로드 완료: {public_url}")
    return public_url


# ── STEP 2: Instagram 카루셀 포스팅 ──────────────────────────────────────────
def post_carousel(image_urls: list[str], caption: str) -> dict:
    """Zernio API로 Instagram 카루셀 포스팅"""
    print("\n📱 Instagram 카루셀 포스팅 중...")

    media_items = [{"type": "image", "url": url} for url in image_urls]

    payload = {
        "content": caption,
        "mediaItems": media_items,
        "platforms": [
            {
                "platform": "instagram",
                "accountId": INSTAGRAM_ACCOUNT_ID,
            }
        ],
        "publishNow": True,
    }

    resp = requests.post(
        f"{ZERNIO_BASE_URL}/posts",
        headers=HEADERS,
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()


# ── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="MLB Scorecard Instagram Auto Poster")
    parser.add_argument("--date", default=str(date.today()), help="날짜 (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="실제 포스팅 없이 테스트")
    args = parser.parse_args()

    post_date = args.date  # e.g. "2025-08-04"
    date_label = post_date  # "AUG 4, 2025" 형식으로도 변환 가능

    # 슬라이드 폴더 확인
    slide_dir = BASE_DIR / post_date
    if not slide_dir.exists():
        print(f"❌ 슬라이드 폴더 없음: {slide_dir}")
        print(f"   먼저 generate_slides_v2.py 실행해서 슬라이드 생성하세요.")
        sys.exit(1)

    # 슬라이드 파일 확인
    slide_paths = []
    for fname in SLIDE_FILES:
        fpath = slide_dir / fname
        if not fpath.exists():
            print(f"❌ 슬라이드 파일 없음: {fpath}")
            sys.exit(1)
        slide_paths.append(fpath)

    print(f"✅ 슬라이드 {len(slide_paths)}장 확인: {slide_dir}")

    if INSTAGRAM_ACCOUNT_ID == "YOUR_INSTAGRAM_ACCOUNT_ID":
        print("\n⚠️  INSTAGRAM_ACCOUNT_ID 를 설정해야 해요!")
        print("   Zernio 대시보드 → Connected Accounts → Instagram 계정 ID 복사")
        sys.exit(1)

    if args.dry_run:
        print("\n🧪 DRY RUN 모드 — 실제 포스팅 안 함")
        for p in slide_paths:
            print(f"   Would upload: {p.name}")
        return

    # 이미지 업로드
    print(f"\n📤 이미지 업로드 시작 ({len(slide_paths)}장)...")
    image_urls = []
    for fpath in slide_paths:
        url = upload_image(fpath)
        image_urls.append(url)

    # 캡션
    caption = (
        f"⚾ MLB SCORECARD | {post_date}\n\n"
        "📊 Today's data-driven picks — all tracked publicly.\n"
        "SP · Bullpen · Batting · Situational\n\n"
        "Swipe for full card & analysis 👉\n\n"
        "#MLB #BaseballAnalytics #MLBPicks #DataDriven #Baseball"
    )

    # 포스팅
    result = post_carousel(image_urls, caption)

    print("\n🎉 Instagram 포스팅 완료!")
    print(f"   Post ID: {result.get('post', {}).get('_id', 'N/A')}")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
