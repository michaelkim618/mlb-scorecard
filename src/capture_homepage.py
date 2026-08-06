#!/usr/bin/env python3
"""
MLB Scorecard — 홈페이지 스크린샷 캡처
src/capture_homepage.py

Usage:
    python3 src/capture_homepage.py
    python3 src/capture_homepage.py --output output/homepage.png
"""

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

HOMEPAGE_URL = "https://www.mlb-scorecard.com"

def capture(output_path: Path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ playwright 미설치. pip install playwright && playwright install chromium")
        sys.exit(1)

    print(f"📸 홈페이지 스크린샷 캡처 중: {HOMEPAGE_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page(
            viewport={"width": 1080, "height": 1080},  # 인스타그램 정사각형
        )

        page.goto(HOMEPAGE_URL, wait_until="networkidle", timeout=30000)

        # 페이지 완전 로드 대기 (데이터 fetch 포함)
        page.wait_for_timeout(3000)

        # 상단 Hero 섹션만 캡처 (랜딩페이지 느낌)
        page.screenshot(
            path=str(output_path),
            clip={"x": 0, "y": 0, "width": 1080, "height": 1080},
            type="png",
        )

        browser.close()

    print(f"✅ 스크린샷 저장: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(OUTPUT_DIR / "homepage.png"))
    args = parser.parse_args()

    output_path = Path(args.output)
    capture(output_path)


if __name__ == "__main__":
    main()
