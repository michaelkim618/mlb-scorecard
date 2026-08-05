#!/usr/bin/env python3
"""
Blog post auto-generation for MLB Scorecard
Usage: python src/generate_blog_post.py --date 2026-08-05 --type daily
       python src/generate_blog_post.py --date 2026-08-05 --type weekly
"""
import os, json, sys, argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import anthropic

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"

# 6 Persona definitions
PERSONAS = {
    "Stan":  {"emoji": "📊", "role": "Data Analyst", "color": "#0EA5E9",
              "style": "Data-driven, speaks in numbers, 'look at this stat', analytical and precise"},
    "Rico":  {"emoji": "🎭", "role": "Drama Critic", "color": "#DC2626",
              "style": "Blunt, provocative, 'let me be honest', hot takes and controversial opinions"},
    "Hana":  {"emoji": "🌸", "role": "Story Writer", "color": "#EC4899",
              "style": "Warm, storytelling style, 'here's the thing...', human angles and narratives"},
    "Ace":   {"emoji": "🎙️", "role": "Former Pitcher", "color": "#D97706",
              "style": "Field experience, direct, 'from a pitcher's perspective', technical baseball insight"},
    "Doc":   {"emoji": "🧠", "role": "Sabermetrician", "color": "#7C3AED",
              "style": "Academic but accessible, WAR/sabermetrics, deep statistical analysis"},
    "Max":   {"emoji": "📰", "role": "Senior Reporter", "color": "#16A34A",
              "style": "Seasoned, relaxed, historical context, 'I've seen this before...'"},
}

# Daily author rotation (0=Monday)
DAILY_ROTATION = ["Stan", "Rico", "Hana", "Ace", "Doc", "Max", "Stan"]

def get_daily_author(game_date: str) -> str:
    d = date.fromisoformat(game_date)
    return DAILY_ROTATION[d.weekday()]

def load_predictions(game_date: str) -> list:
    js_path = OUTPUT_DIR / f"predictions_{game_date}.js"
    if js_path.exists():
        import re
        content = js_path.read_text(encoding="utf-8")
        m = re.search(r'window\.PREDICTIONS_DATA\s*=\s*(\[.*?\]);', content, re.DOTALL)
        if m:
            return json.loads(m.group(1))
    p = OUTPUT_DIR / "predictions.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        return [g for g in data if g.get("date") == game_date] if isinstance(data, list) else []
    return []

def generate_daily_post(game_date: str, web_repo: Path):
    """Daily results review + tomorrow's Top Pick preview"""
    author = get_daily_author(game_date)
    persona = PERSONAS[author]

    # Today's results
    today_games = load_predictions(game_date)
    done_games = [g for g in today_games if g.get("model_correct") is not None]

    wins = sum(1 for g in done_games if g.get("model_correct") == True)
    losses = sum(1 for g in done_games if g.get("model_correct") == False)
    pct = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0

    # Tomorrow's Top Pick
    tomorrow = (date.fromisoformat(game_date) + timedelta(days=1)).isoformat()
    tomorrow_games = load_predictions(tomorrow)
    top_pick = next((g for g in tomorrow_games if g.get("consensus") or g.get("value_bet")),
                    tomorrow_games[0] if tomorrow_games else None)

    # Game results summary
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
        top_pick_summary = f"""Tomorrow's Top Pick: {top_pick.get('away','')} @ {top_pick.get('home','')}
Pick: {pick_team} ({pick_p:.0f}%)
Away SP: {top_pick.get('away_pitcher', 'TBD')}
Home SP: {top_pick.get('home_pitcher', 'TBD')}"""

    # Date labels for title context
    from datetime import date as _date, timedelta as _td
    _d = _date.fromisoformat(game_date)
    results_label = _d.strftime("%b %-d")                  # e.g. "Aug 4"
    preview_label = (_d + _td(days=1)).strftime("%b %-d")  # e.g. "Aug 5"
    has_results   = len(done_games) > 0
    has_preview   = top_pick is not None

    # Case: no games today AND no games tomorrow → skip entirely
    if not has_results and not has_preview:
        print(f"⏭  {game_date}: No games today or tomorrow — skipping daily post.")
        return None

    # Title and context vary by case
    if not has_results and has_preview:
        # Off day today, games tomorrow → preview-only post
        title_format   = f"{preview_label} Preview: [subtitle in {author}'s voice]"
        title_examples = f'- "{preview_label} Preview: Here\'s What the Data Says"\n- "{preview_label} Preview: The Slate I\'ve Been Waiting For"'
        context_note   = f"- No MLB games were played today ({results_label}) — it's an off day\n- Focus entirely on previewing {preview_label}'s upcoming slate"
    elif has_results and has_preview:
        # Normal: results + preview
        title_format   = f"{results_label} Results + {preview_label} Preview: [subtitle in {author}'s voice]"
        title_examples = f'- "{results_label} Results + {preview_label} Preview: The Model Was On Fire"\n- "{results_label} Results + {preview_label} Preview: One Miss, Zero Regrets"'
        context_note   = f"- It reviews {results_label} results\n- It previews {preview_label} upcoming picks (tomorrow's slate)"
    else:
        # Results only: no games tomorrow
        title_format   = f"{results_label} Results: [subtitle in {author}'s voice]"
        title_examples = f'- "{results_label} Results: The Numbers Don\'t Lie"\n- "{results_label} Results: Breaking Down Every Pick"'
        context_note   = f"- It reviews {results_label} results\n- No games scheduled tomorrow — focus entirely on today's analysis"

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    prompt = f"""You are {author}, a blog team member at MLB Scorecard.

Persona: {persona['role']}
Writing style: {persona['style']}

IMPORTANT: Write the ENTIRE blog post in ENGLISH. All text, headings, summaries, labels, and content must be in English only.

Context: {context_note}

{f'''{game_date} MLB prediction results:
- Overall: {wins}W-{losses}L ({pct}%)
- Game-by-game results:
{chr(10).join(games_summary)}''' if has_results else f"No games were played on {results_label} (off day)."}

{f"{preview_label} Top Pick data:{chr(10)}{top_pick_summary}" if top_pick_summary else "No games scheduled for tomorrow."}

Write a blog post in {author}'s style as a JSON object. Make the content array visually rich with a variety of content types.

TITLE FORMAT: The title MUST clearly indicate the date context.
Format to use: "{title_format}"
Examples:
{title_examples}

Return ONLY this JSON structure (no markdown code blocks):

{{
  "title": "{title_format}",
  "summary": "One-line English summary mentioning {results_label} results (under 100 characters)",
  "read_time": "3 min",
  "content": [
    {{"type": "text", "value": "## Section Heading\\n\\nBody text in English. Markdown supported."}},
    {{"type": "stat_row", "stats": [{{"label": "Today W-L", "value": "{wins}-{losses}"}}, {{"label": "Accuracy", "value": "{pct}%"}}]}},
    {{"type": "chart", "chart_type": "bar", "title": "Today's Prediction Results",
      "data": [{{"name": "TEAM", "probability": 63, "result": 1}}],
      "x_key": "name",
      "bars": [{{"key": "probability", "color": "#0EA5E9", "label": "Win Probability (%)"}}, {{"key": "result", "color": "#16A34A", "label": "Correct (1=Yes)"}}]}},
    {{"type": "table", "title": "Full Results", "headers": ["Game", "Pick", "Prob", "Result"], "rows": [["TEAM @ TEAM", "TEAM", "63%", "✓"]]}},
    {{"type": "text", "value": "## Tomorrow's Top Pick Preview\\n\\nAnalysis in English..."}},
    {{"type": "highlight_box", "color": "#F59E0B", "title": "⭐ Tomorrow's Top Pick", "value": "TEAM NAME", "sub": "Win probability · Pitcher matchup"}}
  ]
}}

All text values must be in English. Return pure JSON only."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    if "```" in raw:
        import re
        m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
        if m: raw = m.group(1)

    post_data = json.loads(raw)

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

    blog_dir = web_repo / "public" / "blog" / "posts"
    blog_dir.mkdir(parents=True, exist_ok=True)
    out_path = blog_dir / f"{slug}.json"
    out_path.write_text(json.dumps(post_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ Daily post created: {out_path.name}")

    return post_data


def generate_weekly_post(game_date: str, web_repo: Path):
    """Weekly post: 6 members pitch topics → editor picks → full post written"""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # Load this week's news
    news_file = web_repo / "public" / "news.json"
    news_summary = ""
    if news_file.exists():
        news_data = json.loads(news_file.read_text())
        news_items = news_data.get("news", [])[:8]
        news_summary = "\n".join([f"- {n['title']}" for n in news_items])

    # Step 1: 6 members pitch topics
    print("  📋 Getting topic pitches from all 6 writers...")
    proposals = {}
    for author, persona in PERSONAS.items():
        prop_prompt = f"""You are {author}, a blog writer at MLB Scorecard ({persona['role']}).

This week's MLB news headlines:
{news_summary}

From your perspective ({persona['style']}), pitch the ONE most interesting topic for this week's featured article.

Return ONLY this JSON (no markdown):
{{"topic": "Topic title in English", "reason": "Why this is the hot topic this week — 1-2 sentences in English"}}"""

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
            proposals[author] = {"topic": f"{author}'s pick", "reason": "Interesting issue this week"}
        print(f"    {persona['emoji']} {author}: {proposals[author].get('topic', '')[:60]}")

    # Step 2: Editor picks
    print("  🎯 Editor selecting the winning topic...")
    proposals_text = "\n".join([f"- {a} ({PERSONAS[a]['role']}): {p['topic']} — {p['reason']}"
                                 for a, p in proposals.items()])

    editor_prompt = f"""You are the Editor-in-Chief at MLB Scorecard.

Your 6 blog writers have pitched these weekly topics:
{proposals_text}

Select the ONE topic that will most engage readers this week.

Return ONLY this JSON (no markdown):
{{"selected_author": "AuthorName", "reason": "One sentence explanation for selecting this topic"}}"""

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
        editor_pick = {"selected_author": "Stan", "reason": "Most compelling data story this week"}

    selected_author = editor_pick.get("selected_author", "Stan")
    if selected_author not in PERSONAS:
        selected_author = "Stan"

    selected_persona = PERSONAS[selected_author]
    selected_topic = proposals[selected_author]["topic"]
    print(f"  ✅ Editor's pick: {selected_persona['emoji']} {selected_author} — {selected_topic}")

    # Step 3: Write the full post
    print(f"  ✍️ Writing full post as {selected_author}...")

    write_prompt = f"""You are {selected_author} ({selected_persona['role']}) at MLB Scorecard.
Writing style: {selected_persona['style']}
Tone: Passionate MLB fan energy + genuine expertise + personality-driven storytelling

This week's editor-selected topic: {selected_topic}

This week's MLB news context:
{news_summary}

Write a full weekly blog post in {selected_author}'s distinctive voice.

IMPORTANT: Write the ENTIRE post in ENGLISH. Every word, heading, label, and sentence must be in English.

Return ONLY this JSON (no markdown code blocks):
{{
  "title": "A catchy, personality-driven English title",
  "summary": "One-line English summary",
  "read_time": "5 min",
  "editor_note": "Editor's note: {editor_pick.get('reason', '')}",
  "content": [
    {{"type": "text", "value": "## Section Heading\\n\\nContent in English with {selected_author}'s voice..."}},
    // Include at least 2 visual elements: chart, table, stat_row, or highlight_box
    // Bring personality, hot takes, and deep MLB knowledge
    // Make it fun to read but backed by real analysis
  ]
}}

All content must be in English. Return pure JSON only."""

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
    print(f"✅ Weekly post created: {out_path.name}")

    return post_data


def update_index(web_repo: Path):
    """Update blog/index.json"""
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
            print(f"  [warning] Failed to parse {post_file.name}: {e}")

    index_path = web_repo / "public" / "blog" / "index.json"
    index_path.write_text(json.dumps({"posts": index}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ blog/index.json updated: {len(index)} posts")


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
