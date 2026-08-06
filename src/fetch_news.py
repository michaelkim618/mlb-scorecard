#!/usr/bin/env python3
"""
MLB 주요 뉴스 수집 → public/news.json 저장
소스: MLB.com RSS + ESPN RSS

Usage:
    python3 src/fetch_news.py [web_repo_path]
"""

import json
import sys
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

BASE_DIR = Path(__file__).parent.parent
WEB_REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE_DIR.parent / "mlb-scorecard-web"
OUT_FILE = WEB_REPO / "public" / "news.json"

NEWS_LIMIT = 8  # 카드 8개

# ── 뉴스 소스 ─────────────────────────────────────────────────────────────────
SOURCES = [
    {
        "name":  "MLB.com",
        "url":   "https://www.mlb.com/feeds/news/rss.xml",
        "color": "#002D72",
        "ns":    {"mlb": "https://www.mlb.com/rss/", "dc": "http://purl.org/dc/elements/1.1/"},
    },
    {
        "name":  "ESPN",
        "url":   "https://www.espn.com/espn/rss/mlb/news",
        "color": "#D50A0A",
        "ns":    {"media": "http://search.yahoo.com/mrss/"},
    },
]

# 태그 자동 분류
TAG_RULES = [
    (["injur", "il ", "disabled", "surgery", "strain", "tightness"], "Injury",   "#DC2626"),
    (["trade", "deal", "deadline", "acquire", "sign"],                "Trade",    "#7C3AED"),
    (["homer", "hr", "no-hit", "perfect game", "walk-off"],          "Highlight","#D97706"),
    (["standings", "playoff", "wild card", "division"],               "Standings","#0EA5E9"),
    (["mvp", "cy young", "award", "all-star"],                        "Award",    "#16A34A"),
    (["roster", "lineup", "callup", "option"],                        "Roster",   "#0284C7"),
    (["power rank", "ranking"],                                        "Rankings", "#4F46E5"),
]

def auto_tag(title: str, desc: str) -> tuple[str, str]:
    text = (title + " " + desc).lower()
    for keywords, tag, color in TAG_RULES:
        if any(kw in text for kw in keywords):
            return tag, color
    return "MLB News", "#64748B"


def clean_html(text: str) -> str:
    """HTML 태그 제거"""
    text = re.sub(r'<[^>]+>', '', text or '')
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_pub_date(pub_date_str: str) -> str:
    """RSS pubDate → ISO 8601 변환"""
    try:
        dt = parsedate_to_datetime(pub_date_str)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def time_ago(iso_str: str) -> str:
    """ISO 시간 → "2h ago" 형식"""
    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        diff = int((now - dt).total_seconds())
        if diff < 3600:   return f"{diff // 60}m ago"
        if diff < 86400:  return f"{diff // 3600}h ago"
        return f"{diff // 86400}d ago"
    except Exception:
        return ""


def fetch_og_image(url: str) -> str:
    """기사 URL에서 og:image 메타태그 이미지 추출"""
    try:
        resp = requests.get(url, timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        # og:image 태그 추출 (정규식으로 빠르게)
        match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', resp.text)
        if not match:
            match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', resp.text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


def fetch_mlbcom(source: dict) -> list:
    items = []
    try:
        resp = requests.get(source["url"], timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = source.get("ns", {})
        MLB_NS = "https://www.mlb.com/rss/"

        for item in root.findall(".//item"):
            title   = clean_html(item.findtext("title", ""))
            link    = item.findtext("link", "")
            desc    = clean_html(item.findtext("description", ""))
            pub     = item.findtext("pubDate", "")
            author  = item.findtext(f"{{{ns.get('dc', '')}}}creator", "") if "dc" in ns else ""

            # 이미지 (RSS → 없으면 og:image 크롤)
            img_el = item.find("image")
            image  = img_el.get("href", "") if img_el is not None else ""
            if not image and link:
                image = fetch_og_image(link)

            if not title or not link:
                continue

            tag, tag_color = auto_tag(title, desc)
            pub_iso = parse_pub_date(pub) if pub else datetime.now(timezone.utc).isoformat()

            items.append({
                "title":      title,
                "link":       link,
                "summary":    desc[:200] + ("..." if len(desc) > 200 else ""),
                "image":      image,
                "source":     source["name"],
                "source_color": source["color"],
                "author":     author,
                "tag":        tag,
                "tag_color":  tag_color,
                "pub_date":   pub_iso,
                "time_ago":   time_ago(pub_iso),
            })
    except Exception as e:
        print(f"  [경고] {source['name']} 수집 실패: {e}")
    return items


def fetch_espn(source: dict) -> list:
    items = []
    try:
        resp = requests.get(source["url"], timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        MEDIA_NS = "http://search.yahoo.com/mrss/"

        for item in root.findall(".//item"):
            title  = clean_html(item.findtext("title", ""))
            link   = item.findtext("link", "") or item.findtext("guid", "")
            desc   = clean_html(item.findtext("description", ""))
            pub    = item.findtext("pubDate", "")

            # ESPN 미디어 이미지 (RSS → 없으면 og:image 크롤)
            media_content = item.find(f"{{{MEDIA_NS}}}content")
            image = media_content.get("url", "") if media_content is not None else ""
            if not image and link:
                image = fetch_og_image(link)

            if not title or not link:
                continue

            tag, tag_color = auto_tag(title, desc)
            pub_iso = parse_pub_date(pub) if pub else datetime.now(timezone.utc).isoformat()

            items.append({
                "title":        title,
                "link":         link,
                "summary":      desc[:200] + ("..." if len(desc) > 200 else ""),
                "image":        image,
                "source":       source["name"],
                "source_color": source["color"],
                "author":       "",
                "tag":          tag,
                "tag_color":    tag_color,
                "pub_date":     pub_iso,
                "time_ago":     time_ago(pub_iso),
            })
    except Exception as e:
        print(f"  [경고] {source['name']} 수집 실패: {e}")
    return items


def fetch_all() -> list:
    all_items = []
    for source in SOURCES:
        if "mlb.com" in source["url"]:
            items = fetch_mlbcom(source)
        else:
            items = fetch_espn(source)
        print(f"  {source['name']}: {len(items)}개 수집")
        all_items.extend(items)

    # 최신순 정렬
    all_items.sort(key=lambda x: x["pub_date"], reverse=True)

    # 중복 제목 제거
    seen_titles = set()
    unique = []
    for item in all_items:
        key = item["title"][:40].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(item)

    return unique[:NEWS_LIMIT]


def main():
    print("📰 MLB 뉴스 수집 중...")
    news = fetch_all()
    print(f"✅ 총 {len(news)}개 뉴스 선정")

    for i, n in enumerate(news, 1):
        print(f"  {i}. [{n['tag']}] {n['title'][:55]}... ({n['time_ago']})")

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "news": news,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"💾 저장됨: {OUT_FILE}")


if __name__ == "__main__":
    main()
