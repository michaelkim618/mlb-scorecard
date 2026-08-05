#!/usr/bin/env python3
"""
블로그 포스트 자동 생성
사용법: python src/generate_blog_post.py --date 2026-08-05 --type daily
       python src/generate_blog_post.py --date 2026-08-05 --type weekly
"""
import os, json, sys, argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import anthropic

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"

# 6명 페르소나 정의
PERSONAS = {
    "Stan":  {"emoji": "📊", "role": "Data Analyst", "color": "#0EA5E9", "style": "데이터 중심, 숫자로 말함, '이 수치 봐봐'"},
    "Rico":  {"emoji": "🎭", "role": "Drama Critic", "color": "#DC2626", "style": "독설적, 자극적, '솔직히 말할게'"},
    "Hana":  {"emoji": "🌸", "role": "Story Writer", "color": "#EC4899", "style": "따뜻하고 감성적, '근데 있잖아...'"},
    "Ace":   {"emoji": "🎙️", "role": "Former Pitcher", "color": "#D97706", "style": "현장 경험 기반, 단호함, '투수 입장에서 말하면'"},
    "Doc":   {"emoji": "🧠", "role": "Sabermetrician", "color": "#7C3AED", "style": "학술적이지만 읽기 쉽게, WAR/세이버메트릭스"},
    "Max":   {"emoji": "📰", "role": "Senior Reporter", "color": "#16A34A", "style": "노련하고 여유있음, 역사적 맥락, '예전에도 이런 일이'"},
}

# 요일별 작성자 (0=월요일)
DAILY_ROTATION = ["Stan", "Rico", "Hana", "Ace", "Doc", "Max", "Stan"]

def get_daily_author(game_date: str) -> str:
    d = date.fromisoformat(game_date)
    return DAILY_ROTATION[d.weekday()]

def load_predictions(game_date: str) -> list:
    # 해당 날짜 predictions 로드
    js_path = OUTPUT_DIR / f"predictions_{game_date}.js"
    if js_path.exists():
        import re
        content = js_path.read_text(encoding="utf-8")
        m = re.search(r'window\.PREDICTIONS_DATA\s*=\s*(\[.*?\]);', content, re.DOTALL)
        if m:
            return json.loads(m.group(1))
    # fallback: predictions.json
    p = OUTPUT_DIR / "predictions.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        return [g for g in data if g.get("date") == game_date] if isinstance(data, list) else []
    return []

def generate_daily_post(game_date: str, web_repo: Path):
    """매일 결과 리뷰 + 내일 Top Pick 프리뷰"""
    author = get_daily_author(game_date)
    persona = PERSONAS[author]

    # 오늘 결과
    today_games = load_predictions(game_date)
    done_games = [g for g in today_games if g.get("model_correct") is not None]
    wins = sum(1 for g in done_games if g.get("model_correct") == True)
    losses = sum(1 for g in done_games if g.get("model_correct") == False)
    pct = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0

    # 내일 Top Pick
    tomorrow = (date.fromisoformat(game_date) + timedelta(days=1)).isoformat()
    tomorrow_games = load_predictions(tomorrow)
    top_pick = next((g for g in tomorrow_games if g.get("consensus") or g.get("value_bet")),
                    tomorrow_games[0] if tomorrow_games else None)

    # 게임 결과 요약
    games_summary = []
    for g in done_games[:10]:
        win_prob = g.get("win_prob", {})
        away_pct = win_prob.get("away", 50) if isinstance(win_prob, dict) else 50
        home_pct = win_prob.get("home", 50) if isinstance(win_prob, dict) else 50
        pick = g.get("model_winner", "")
        pick_prob = max(away_pct, home_pct)
        correct = "✓ Hit" if g.get("model_correct") else "✗ Miss"
        games_summary.append(f"- {g.get('away','').split()[-1]} @ {g.get('home','').split()[-1]}: Pick {pick.split()[-1]} {pick_prob:.0f}% → {correct}")

    top_pick_summary = ""
    if top_pick:
        wp = top_pick.get("win_prob", {})
        away_p = wp.get("away", 50) if isinstance(wp, dict) else 50
        home_p = wp.get("home", 50) if isinstance(wp, dict) else 50
        pick_team = top_pick.get("model_winner", "")
        pick_p = max(away_p, home_p)
        top_pick_summary = f"""내일 Top Pick: {top_pick.get('away','')} @ {top_pick.get('home','')}
Pick: {pick_team} ({pick_p:.0f}%)
Away SP: {top_pick.get('away_pitcher', 'TBD')}
Home SP: {top_pick.get('home_pitcher', 'TBD')}"""

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    prompt = f"""당신은 MLB Scorecard의 블로그 팀 멤버 {author}입니다.

페르소나: {persona['role']}
문체: {persona['style']}

오늘({game_date}) MLB 예측 결과:
- 총 결과: {wins}W-{losses}L ({pct}%)
- 게임별 결과:
{chr(10).join(games_summary)}

{f"내일 Top Pick 데이터:{chr(10)}{top_pick_summary}" if top_pick_summary else ""}

{author}의 스타일로 블로그 포스트를 JSON 형식으로 작성해주세요.

반드시 아래 JSON 구조를 지켜주세요. content 배열에는 다양한 타입을 섞어서 시각적으로 풍부하게 만드세요:

{{
  "title": "매력적인 제목 (한국어 또는 영어)",
  "summary": "한줄 요약 (100자 이내)",
  "read_time": "3 min",
  "content": [
    {{"type": "text", "value": "## 섹션 제목\\n\\n본문 내용. 마크다운 지원."}},
    {{"type": "stat_row", "stats": [{{"label": "Today W-L", "value": "{wins}-{losses}"}}, {{"label": "Accuracy", "value": "{pct}%"}}]}},
    {{"type": "chart", "chart_type": "bar", "title": "오늘의 예측 결과",
      "data": [{{"name": "팀약자", "probability": 숫자, "result": 1}}],
      "x_key": "name",
      "bars": [{{"key": "probability", "color": "#0EA5E9", "label": "예측 확률(%)"}}, {{"key": "result", "color": "#16A34A", "label": "적중(1=O)"}}]}},
    {{"type": "table", "title": "오늘의 전체 결과", "headers": ["경기", "픽", "확률", "결과"], "rows": [["팀A @ 팀B", "팀A", "63%", "✓"]]}},
    {{"type": "text", "value": "## 내일 Top Pick 프리뷰\\n\\n분석 내용..."}},
    {{"type": "highlight_box", "color": "#F59E0B", "title": "⭐ Tomorrow's Top Pick", "value": "팀명", "sub": "픽 확률 · 투수명"}}
  ]
}}

중요: 순수 JSON만 반환하세요. 마크다운 코드블록 없이."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    # JSON 추출
    if "```" in raw:
        import re
        m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
        if m: raw = m.group(1)

    post_data = json.loads(raw)

    # 메타데이터 추가
    slug = f"{game_date}-daily-{author.lower()}"
    post_data.update({
        "slug": slug,
        "author": author,
        "author_emoji": persona["emoji"],
        "author_role": persona["role"],
        "author_color": persona["color"],
        "date": game_date,
        "type": "daily",
        "tag": "Daily Review",
        "tag_color": persona["color"],
    })

    # 저장
    blog_dir = web_repo / "public" / "blog" / "posts"
    blog_dir.mkdir(parents=True, exist_ok=True)
    out_path = blog_dir / f"{slug}.json"
    out_path.write_text(json.dumps(post_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ Daily post 생성: {out_path.name}")

    return post_data


def generate_weekly_post(game_date: str, web_repo: Path):
    """주간 포스트: 6명 토픽 제안 → 편집장 픽 → 글 작성"""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # 이번 주 뉴스 로드
    news_file = web_repo / "public" / "news.json"
    news_summary = ""
    if news_file.exists():
        news_data = json.loads(news_file.read_text())
        news_items = news_data.get("news", [])[:8]
        news_summary = "\n".join([f"- {n['title']}" for n in news_items])

    # Step 1: 6명 토픽 제안
    print("  📋 6명 토픽 제안 중...")
    proposals = {}
    for author, persona in PERSONAS.items():
        prop_prompt = f"""당신은 MLB Scorecard 블로그의 {author} ({persona['role']})입니다.

이번 주 MLB 뉴스:
{news_summary}

당신의 관점({persona['style']})에서 이번 주 가장 흥미로운 토픽 1개를 제안하세요.

JSON 형식으로:
{{"topic": "토픽 제목", "reason": "왜 이게 이번 주 핫이슈인지 1-2문장"}}

순수 JSON만 반환."""

        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prop_prompt}]
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            import re
            m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
            if m: raw = m.group(1)
        try:
            proposals[author] = json.loads(raw)
        except:
            proposals[author] = {"topic": f"{author}'s pick", "reason": "흥미로운 이슈"}
        print(f"    {persona['emoji']} {author}: {proposals[author].get('topic', '')[:50]}")

    # Step 2: 편집장 픽
    print("  🎯 편집장 토픽 선정 중...")
    proposals_text = "\n".join([f"- {a} ({PERSONAS[a]['role']}): {p['topic']} — {p['reason']}"
                                 for a, p in proposals.items()])

    editor_prompt = f"""당신은 MLB Scorecard의 편집장입니다.

블로그 팀 6명의 주간 토픽 제안:
{proposals_text}

가장 독자들이 흥미롭게 읽을 토픽 1개를 선택하세요.

JSON:
{{"selected_author": "이름", "reason": "선택 이유 한줄"}}

순수 JSON만 반환."""

    editor_resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{"role": "user", "content": editor_prompt}]
    )
    raw = editor_resp.content[0].text.strip()
    if "```" in raw:
        import re
        m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
        if m: raw = m.group(1)

    try:
        editor_pick = json.loads(raw)
    except:
        editor_pick = {"selected_author": "Stan", "reason": "가장 흥미로운 데이터 이슈"}

    selected_author = editor_pick.get("selected_author", "Stan")
    if selected_author not in PERSONAS:
        selected_author = "Stan"

    selected_persona = PERSONAS[selected_author]
    selected_topic = proposals[selected_author]["topic"]
    print(f"  ✅ 편집장 픽: {selected_persona['emoji']} {selected_author} — {selected_topic}")

    # Step 3: 풀 글 작성
    print(f"  ✍️ {selected_author} 글 작성 중...")

    write_prompt = f"""당신은 MLB Scorecard의 {selected_author} ({selected_persona['role']})입니다.
문체: {selected_persona['style']}
스타일: B급 감성 + 오타쿠적 열정 + 진짜 MLB 팬만 아는 디테일

이번 주 핫이슈로 선정된 토픽: {selected_topic}

이번 주 MLB 뉴스 컨텍스트:
{news_summary}

당신의 개성 넘치는 스타일로 주간 블로그 포스트를 JSON으로 작성해주세요.
재미있고 읽기 쉽게, 하지만 전문성도 갖추어서.

{{
  "title": "개성 있는 제목",
  "summary": "한줄 요약",
  "read_time": "5 min",
  "editor_note": "편집장의 선정 이유: {editor_pick.get('reason', '')}",
  "content": [
    {{"type": "text", "value": "## 섹션\\n\\n내용..."}},
    // 차트, 표, stat_row, highlight_box 등 시각적 요소 2개 이상 포함
    // 재미있는 비유나 오타쿠 감성 표현 적극 활용
  ]
}}

순수 JSON만 반환."""

    write_resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=5000,
        messages=[{"role": "user", "content": write_prompt}]
    )

    raw = write_resp.content[0].text.strip()
    if "```" in raw:
        import re
        m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
        if m: raw = m.group(1)

    post_data = json.loads(raw)

    # 주차 계산
    d = date.fromisoformat(game_date)
    week_num = d.isocalendar()[1]
    slug = f"{game_date}-weekly-{selected_author.lower()}"

    post_data.update({
        "slug": slug,
        "author": selected_author,
        "author_emoji": selected_persona["emoji"],
        "author_role": selected_persona["role"],
        "author_color": selected_persona["color"],
        "date": game_date,
        "type": "weekly",
        "tag": "Weekly Analysis",
        "tag_color": selected_persona["color"],
        "proposals": proposals,
        "editor_pick": editor_pick,
    })

    blog_dir = web_repo / "public" / "blog" / "posts"
    blog_dir.mkdir(parents=True, exist_ok=True)
    out_path = blog_dir / f"{slug}.json"
    out_path.write_text(json.dumps(post_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ Weekly post 생성: {out_path.name}")

    return post_data


def update_index(web_repo: Path):
    """blog/index.json 업데이트"""
    posts_dir = web_repo / "public" / "blog" / "posts"
    posts_dir.mkdir(parents=True, exist_ok=True)

    index = []
    for post_file in sorted(posts_dir.glob("*.json"), reverse=True):
        try:
            post = json.loads(post_file.read_text(encoding="utf-8"))
            index.append({
                "slug":         post["slug"],
                "title":        post["title"],
                "author":       post["author"],
                "author_emoji": post["author_emoji"],
                "author_color": post["author_color"],
                "author_role":  post.get("author_role", ""),
                "date":         post["date"],
                "type":         post.get("type", "daily"),
                "tag":          post.get("tag", ""),
                "tag_color":    post.get("tag_color", "#64748B"),
                "read_time":    post.get("read_time", "3 min"),
                "summary":      post.get("summary", ""),
            })
        except Exception as e:
            print(f"  [경고] {post_file.name} 파싱 오류: {e}")

    index_path = web_repo / "public" / "blog" / "index.json"
    index_path.write_text(json.dumps({"posts": index}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ blog/index.json 업데이트: {len(index)}개 포스트")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--type", choices=["daily", "weekly", "both"], default="daily")
    parser.add_argument("--web-repo", default=None)
    args = parser.parse_args()

    web_repo = Path(args.web_repo) if args.web_repo else BASE_DIR.parent / "mlb-scorecard-web"

    if args.type in ("daily", "both"):
        generate_daily_post(args.date, web_repo)
    if args.type in ("weekly", "both"):
        generate_weekly_post(args.date, web_repo)

    update_index(web_repo)


if __name__ == "__main__":
    main()
