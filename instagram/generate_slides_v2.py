#!/usr/bin/env python3
"""
MLB Scorecard Instagram Carousel - Premium Sports Magazine Style
generate_slides_v2.py  — Dynamic version (reads from predictions.json)
"""

from PIL import Image, ImageDraw, ImageFont
import os
import sys
import json
import argparse
from datetime import date, datetime
from pathlib import Path

# ── 날짜 인자 파싱 ─────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser()
_parser.add_argument("--date", default=str(date.today()))
_args, _ = _parser.parse_known_args()
POST_DATE = _args.date

# ── 경로 설정 ──────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent.parent
OUTPUT_DIR = str(BASE_DIR / "slides" / POST_DATE)
PRED_FILE  = BASE_DIR / "output" / "predictions.json"

SIZE = (1080, 1080)

# ── Color palette ──────────────────────────────────────────────────────────
WHITE      = "#FFFFFF"
NAVY       = "#0F172A"
BLUE       = "#2563EB"
GOLD       = "#F59E0B"
LIGHT_GRAY = "#F8FAFC"
MED_GRAY   = "#64748B"
GREEN      = "#10B981"
BORDER     = "#E2E8F0"

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
def color(h): return hex_to_rgb(h)

def load_font(size, bold=False):
    candidates = (
        ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/System/Library/Fonts/Helvetica.ttc",
         "/System/Library/Fonts/Supplemental/Impact.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        ["/System/Library/Fonts/Supplemental/Arial.ttf",
         "/System/Library/Fonts/Helvetica.ttc",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for path in candidates:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size)
            except: continue
    return ImageFont.load_default()

def draw_text_centered(draw, text, y, font, fill, img_width=1080):
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (img_width - (bbox[2]-bbox[0])) // 2
    draw.text((x, y), text, font=font, fill=color(fill))

def draw_slide_indicator(draw, slide_num, total=5):
    font = load_font(22)
    text = f"{slide_num}/{total}"
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((1080-(bbox[2]-bbox[0])-36, 36), text, font=font, fill=color(MED_GRAY))

def fmt_date_label(d: str) -> str:
    """2026-08-03 → AUG 3, 2026"""
    dt = datetime.strptime(d, "%Y-%m-%d")
    return dt.strftime("%b %-d, %Y").upper()

def abbr(team_name: str) -> str:
    """팀 이름 약어 변환"""
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
    """'Trevor Rogers (미정)' → 'T. ROGERS'"""
    name = pitcher_str.split("(")[0].strip()
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0][0]}. {parts[-1].upper()}"
    return name.upper()

def sp_trend(sp_detail: dict) -> str:
    trend = sp_detail.get("trend", "")
    if trend == "hot": return "HOT STREAK"
    if trend == "cold": return "COLD STREAK"
    return "NEUTRAL"

# ── 예측 데이터 로드 & 처리 ────────────────────────────────────────────────
def load_predictions():
    if not PRED_FILE.exists():
        print(f"⚠️  예측 파일 없음: {PRED_FILE}")
        return []
    with open(PRED_FILE, encoding="utf-8") as f:
        return json.load(f)

def get_top_picks(preds: list, n=5) -> list:
    """승률 60% 이상, 확률 높은 순 정렬"""
    picks = []
    for g in preds:
        wp = g.get("win_prob", {})
        if not wp: continue
        away_pct = wp.get("away", 50)
        home_pct = wp.get("home", 50)
        if home_pct >= away_pct and home_pct >= 55:
            picks.append({
                "away": abbr(g.get("away", "???")),
                "home": abbr(g.get("home", "???")),
                "pick": abbr(g.get("home", "???")),
                "opp":  abbr(g.get("away", "???")),
                "pct":  f"{home_pct:.0f}%",
                "pct_val": home_pct,
                "away_sp": sp_name(g.get("away_pitcher","TBD")),
                "home_sp": sp_name(g.get("home_pitcher","TBD")),
                "away_sp_detail": g.get("scorecard",{}).get("away",{}).get("sp_detail",{}),
                "home_sp_detail": g.get("scorecard",{}).get("home",{}).get("sp_detail",{}),
                "away_era": g.get("scorecard",{}).get("away",{}).get("sp_detail",{}).get("era","?"),
                "home_era": g.get("scorecard",{}).get("home",{}).get("sp_detail",{}).get("era","?"),
            })
        elif away_pct > home_pct and away_pct >= 55:
            picks.append({
                "away": abbr(g.get("away","???")),
                "home": abbr(g.get("home","???")),
                "pick": abbr(g.get("away","???")),
                "opp":  abbr(g.get("home","???")),
                "pct":  f"{away_pct:.0f}%",
                "pct_val": away_pct,
                "away_sp": sp_name(g.get("away_pitcher","TBD")),
                "home_sp": sp_name(g.get("home_pitcher","TBD")),
                "away_sp_detail": g.get("scorecard",{}).get("away",{}).get("sp_detail",{}),
                "home_sp_detail": g.get("scorecard",{}).get("home",{}).get("sp_detail",{}),
                "away_era": g.get("scorecard",{}).get("away",{}).get("sp_detail",{}).get("era","?"),
                "home_era": g.get("scorecard",{}).get("home",{}).get("sp_detail",{}).get("era","?"),
            })
    picks.sort(key=lambda x: x["pct_val"], reverse=True)
    return picks[:n]

# ── SLIDE 1 — COVER ────────────────────────────────────────────────────────
def slide_01(date_label: str, total_games: int):
    img = Image.new("RGB", SIZE, color(WHITE))
    draw = ImageDraw.Draw(img)
    navy_h = 270
    draw.rectangle([(0,0),(1080,navy_h)], fill=color(NAVY))
    font_header = load_font(52, bold=True)
    text = "MLB SCORECARD"
    bbox = draw.textbbox((0,0), text, font=font_header)
    tw = bbox[2]-bbox[0]; tx=(1080-tw)//2; ty=90
    draw.text((tx,ty), text, font=font_header, fill=color(WHITE))
    line_y = ty+(bbox[3]-bbox[1])+12
    line_w = tw+40; lx=(1080-line_w)//2
    draw.rectangle([(lx,line_y),(lx+line_w,line_y+5)], fill=color(BLUE))
    font_tag = load_font(24)
    tag = "PREMIUM DATA  ·  DAILY PICKS"
    bbox2 = draw.textbbox((0,0), tag, font=font_tag)
    draw.text(((1080-(bbox2[2]-bbox2[0]))//2, line_y+18), tag, font=font_tag, fill=color(MED_GRAY))
    cx,cy,r = 980,540,280
    draw.ellipse([(cx-r,cy-r),(cx+r,cy+r)], fill=color("#DBEAFE"))
    draw.ellipse([(cx-r+20,cy-r+20),(cx+r-20,cy+r-20)], outline=color(BLUE), width=6)
    font_huge = load_font(130, bold=True)
    draw.text((60, navy_h+30), "TODAY'S", font=font_huge, fill=color(BLUE))
    draw.text((60, navy_h+165), "PICKS", font=font_huge, fill=color(NAVY))
    font_date = load_font(34, bold=True)
    draw.text((62, navy_h+315), date_label, font=font_date, fill=color(GOLD))
    draw.rectangle([(60,navy_h+362),(520,navy_h+367)], fill=color(GOLD))
    font_sub = load_font(26)
    draw.text((62, navy_h+382), f"{total_games} GAMES  ·  DATA-DRIVEN  ·  FREE", font=font_sub, fill=color(MED_GRAY))
    font_handle = load_font(26)
    draw.text((62, 1020), "@MLB_Scorecard", font=font_handle, fill=color(NAVY))
    draw_slide_indicator(draw, 1)
    img.save(os.path.join(OUTPUT_DIR, "slide_01_cover.png"))
    print("✅ slide_01_cover.png")

# ── SLIDE 2 — TOP PICK ────────────────────────────────────────────────────
def slide_02(top_pick: dict, date_label: str):
    img = Image.new("RGB", SIZE, color(WHITE))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0,0),(80,1080)], fill=color(BLUE))
    LEFT = 110
    font_sm_caps = load_font(28, bold=True)
    draw.text((LEFT,60), "HIGH CONFIDENCE", font=font_sm_caps, fill=color(BLUE))
    font_sub = load_font(26)
    draw.text((LEFT,100), "PICK OF THE DAY", font=font_sub, fill=color(MED_GRAY))
    draw.rectangle([(LEFT,142),(1050,144)], fill=color(BORDER))
    font_team = load_font(100, bold=True)
    font_at   = load_font(70)
    y_match = 165
    draw.text((LEFT, y_match), top_pick["away"], font=font_team, fill=color(NAVY))
    draw.text((LEFT+260, y_match+20), "@", font=font_at, fill=color(MED_GRAY))
    draw.text((LEFT+355, y_match), top_pick["home"], font=font_team, fill=color("#94A3B8"))
    rect_y = y_match+115
    draw.rectangle([(LEFT,rect_y),(LEFT+420,rect_y+90)], fill=color(NAVY))
    font_pct = load_font(52, bold=True)
    pick_label = f"{top_pick['pick']}  {top_pick['pct']}"
    draw.text((LEFT+24, rect_y+18), pick_label, font=font_pct, fill=color(WHITE))
    font_label = load_font(22)
    draw.text((LEFT+440, rect_y+34), "WIN PROBABILITY", font=font_label, fill=color(MED_GRAY))
    div_y = rect_y+110
    draw.rectangle([(LEFT,div_y),(1050,div_y+1)], fill=color(BORDER))
    card_y = div_y+20; card_h=175; card_w=430
    # Away SP card
    away_trend = sp_trend(top_pick.get("away_sp_detail",{}))
    away_era   = top_pick.get("away_era","?")
    draw.rectangle([(LEFT,card_y),(LEFT+card_w,card_y+card_h)], fill=color(LIGHT_GRAY))
    draw.rectangle([(LEFT,card_y),(LEFT+8,card_y+card_h)], fill=color(MED_GRAY))
    font_name = load_font(36, bold=True); font_stat = load_font(26); font_hot = load_font(22, bold=True)
    draw.text((LEFT+18, card_y+14), top_pick["away_sp"], font=font_name, fill=color(NAVY))
    draw.text((LEFT+18, card_y+62), f"ERA  {away_era}", font=font_stat, fill=color(BLUE))
    trend_color = GOLD if "HOT" in away_trend else (BLUE if "COLD" in away_trend else MED_GRAY)
    draw.text((LEFT+18, card_y+100), away_trend, font=font_hot, fill=color(trend_color))
    draw.text((LEFT+18, card_y+136), top_pick["away"], font=font_hot, fill=color(MED_GRAY))
    # Home SP card
    home_trend = sp_trend(top_pick.get("home_sp_detail",{}))
    home_era   = top_pick.get("home_era","?")
    rx = LEFT+card_w+30
    draw.rectangle([(rx,card_y),(rx+card_w,card_y+card_h)], fill=color(LIGHT_GRAY))
    draw.rectangle([(rx,card_y),(rx+8,card_y+card_h)], fill=color(BLUE))
    draw.text((rx+18, card_y+14), top_pick["home_sp"], font=font_name, fill=color(NAVY))
    draw.text((rx+18, card_y+62), f"ERA  {home_era}", font=font_stat, fill=color(BLUE))
    h_trend_color = GOLD if "HOT" in home_trend else (BLUE if "COLD" in home_trend else MED_GRAY)
    draw.text((rx+18, card_y+100), home_trend, font=font_hot, fill=color(h_trend_color))
    draw.text((rx+18, card_y+136), top_pick["home"], font=font_hot, fill=color(MED_GRAY))
    bottom_div = card_y+card_h+22
    draw.rectangle([(LEFT,bottom_div),(1050,bottom_div+1)], fill=color(BORDER))
    model_y = bottom_div+14
    font_model = load_font(22)
    draw.text((LEFT, model_y), "SCORECARD MODEL:  SP 30%  ·  BP 20%  ·  BAT 35%  ·  SIT 15%", font=font_model, fill=color(MED_GRAY))
    font_handle = load_font(24)
    bbox = draw.textbbox((0,0), "@MLB_Scorecard", font=font_handle)
    draw.text((1050-(bbox[2]-bbox[0]), 1040), "@MLB_Scorecard", font=font_handle, fill=color(NAVY))
    draw_slide_indicator(draw, 2)
    img.save(os.path.join(OUTPUT_DIR, "slide_02_top_pick.png"))
    print("✅ slide_02_top_pick.png")

# ── SLIDE 3 — FULL CARD ───────────────────────────────────────────────────
def slide_03(top_picks: list, date_label: str):
    img = Image.new("RGB", SIZE, color(WHITE))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0,0),(1080,130)], fill=color(BLUE))
    font_hdr = load_font(54, bold=True)
    draw_text_centered(draw, f"{date_label} PICKS", 32, font_hdr, WHITE)
    y = 155
    font_col = load_font(22, bold=True)
    draw.text((60,y), "MATCHUP", font=font_col, fill=color(MED_GRAY))
    draw.text((560,y), "PICK", font=font_col, fill=color(MED_GRAY))
    draw.text((780,y), "CONFIDENCE", font=font_col, fill=color(MED_GRAY))
    draw.rectangle([(40,y+34),(1040,y+35)], fill=color(BORDER))
    font_team_r = load_font(40, bold=True)
    font_vs     = load_font(28)
    font_pick   = load_font(44, bold=True)
    font_pct_r  = load_font(42, bold=True)
    font_star   = load_font(24)
    row_h = 108; row_start = y+46
    for i, p in enumerate(top_picks):
        ry = row_start + i*row_h
        if i%2==1: draw.rectangle([(40,ry),(1040,ry+row_h-4)], fill=color(LIGHT_GRAY))
        draw.text((60, ry+24), p["away"], font=font_team_r, fill=color(NAVY))
        draw.text((170,ry+34), "vs", font=font_vs, fill=color(MED_GRAY))
        draw.text((220,ry+24), p["home"], font=font_team_r, fill=color(MED_GRAY))
        draw.text((560,ry+18), p["pick"], font=font_pick, fill=color(BLUE))
        if i==0: draw.text((660,ry+18), "BEST BET", font=font_star, fill=color(GOLD))
        draw.text((780,ry+18), p["pct"], font=font_pct_r, fill=color(GOLD))
        draw.rectangle([(40,ry+row_h-4),(1040,ry+row_h-3)], fill=color(BORDER))
    bar_y = row_start+len(top_picks)*row_h+16
    draw.rectangle([(0,bar_y),(1080,bar_y+68)], fill=color(LIGHT_GRAY))
    font_bar = load_font(24)
    avg_conf = sum(p["pct_val"] for p in top_picks)/len(top_picks) if top_picks else 0
    draw_text_centered(draw, f"{len(top_picks)} PICKS TODAY  ·  AVG CONFIDENCE: {avg_conf:.1f}%", bar_y+22, font_bar, MED_GRAY)
    font_handle = load_font(24)
    bbox = draw.textbbox((0,0), "@MLB_Scorecard", font=font_handle)
    draw.text(((1080-(bbox[2]-bbox[0]))//2, 1040), "@MLB_Scorecard", font=font_handle, fill=color(MED_GRAY))
    draw_slide_indicator(draw, 3)
    img.save(os.path.join(OUTPUT_DIR, "slide_03_full_card.png"))
    print("✅ slide_03_full_card.png")

# ── SLIDE 4 — HOW WE SCORE ────────────────────────────────────────────────
def slide_04():
    img = Image.new("RGB", SIZE, color(WHITE))
    draw = ImageDraw.Draw(img)
    LEFT = 60
    font_sm = load_font(34)
    draw.text((LEFT,70), "HOW WE", font=font_sm, fill=color(MED_GRAY))
    font_giant = load_font(148, bold=True)
    draw.text((LEFT,105), "SCORE", font=font_giant, fill=color(NAVY))
    draw.rectangle([(LEFT,255),(LEFT+520,262)], fill=color(GOLD))
    draw.rectangle([(LEFT,268),(LEFT+200,272)], fill=color(BLUE))
    factors = [
        ("30%",BLUE,"SP SCORE","Starter ERA, WHIP, recent form & rest days"),
        ("20%",GREEN,"BULLPEN","Pen ERA, usage load, save situations"),
        ("35%",GOLD,"BATTING","OPS, recent 10G trend, lineup depth"),
        ("15%",MED_GRAY,"SITUATIONAL","Home/away, streak, weather, park factor"),
    ]
    font_pct_big = load_font(64, bold=True); font_factor = load_font(36, bold=True); font_desc = load_font(26)
    row_y = 300
    for pct,clr,name,desc in factors:
        draw.rectangle([(LEFT-10,row_y),(1060,row_y+1)], fill=color(BORDER))
        row_y += 14
        draw.text((LEFT,row_y), pct, font=font_pct_big, fill=color(clr))
        draw.text((LEFT+155,row_y+4), name, font=font_factor, fill=color(NAVY))
        draw.text((LEFT+155,row_y+50), desc, font=font_desc, fill=color(MED_GRAY))
        row_y += 115
    draw.rectangle([(LEFT-10,row_y),(1060,row_y+1)], fill=color(BORDER))
    font_italic = load_font(28)
    draw.text((LEFT,row_y+22), "Transparent. Data-driven. No hype.", font=font_italic, fill=color(MED_GRAY))
    font_handle = load_font(24)
    draw.text((LEFT,1040), "@MLB_Scorecard", font=font_handle, fill=color(NAVY))
    draw_slide_indicator(draw, 4)
    img.save(os.path.join(OUTPUT_DIR, "slide_04_how_we_score.png"))
    print("✅ slide_04_how_we_score.png")

# ── SLIDE 5 — RESULTS ────────────────────────────────────────────────────
def slide_05(win_count: int, loss_count: int):
    total = win_count + loss_count
    acc = (win_count/total*100) if total > 0 else 0
    img = Image.new("RGB", SIZE, color(WHITE))
    draw = ImageDraw.Draw(img)
    navy_h = 520
    draw.rectangle([(0,0),(1080,navy_h)], fill=color(NAVY))
    font_sm = load_font(30)
    draw.text((60,60), "SEASON", font=font_sm, fill=color("#94A3B8"))
    font_giant = load_font(110, bold=True)
    draw.text((60,95), "ACCURACY", font=font_giant, fill=color(WHITE))
    font_huge_pct = load_font(160, bold=True)
    pct_text = f"{acc:.1f}%"
    bbox = draw.textbbox((0,0), pct_text, font=font_huge_pct)
    draw.text(((1080-(bbox[2]-bbox[0]))//2, 270), pct_text, font=font_huge_pct, fill=color(BLUE))
    font_record = load_font(90, bold=True)
    draw_text_centered(draw, f"{win_count}W   {loss_count}L", navy_h+50, font_record, NAVY)
    bar_y = navy_h+160; bar_w=900; bx=(1080-bar_w)//2
    draw.rectangle([(bx,bar_y),(bx+bar_w,bar_y+22)], fill=color(BORDER))
    filled = int(bar_w*(acc/100))
    draw.rectangle([(bx,bar_y),(bx+filled,bar_y+22)], fill=color(BLUE))
    draw.ellipse([(bx+filled-14,bar_y-8),(bx+filled+14,bar_y+30)], fill=color(BLUE))
    font_bar_lbl = load_font(20)
    draw.text((bx,bar_y+30), "0%", font=font_bar_lbl, fill=color(MED_GRAY))
    draw.text((bx+bar_w-30,bar_y+30), "100%", font=font_bar_lbl, fill=color(MED_GRAY))
    font_italic = load_font(26)
    draw_text_centered(draw, "Every pick tracked publicly. No cherry-picking.", bar_y+70, font_italic, MED_GRAY)
    btn_y = bar_y+120
    btn_text = "FOLLOW @MLB_Scorecard"
    font_btn = load_font(30, bold=True)
    bbox_btn = draw.textbbox((0,0), btn_text, font=font_btn)
    bw=bbox_btn[2]-bbox_btn[0]; bh=bbox_btn[3]-bbox_btn[1]; pad=20
    btn_x = (1080-bw-pad*2)//2
    draw.rectangle([(btn_x,btn_y),(btn_x+bw+pad*2,btn_y+bh+pad*2)], fill=color(GOLD))
    draw.text((btn_x+pad,btn_y+pad), btn_text, font=font_btn, fill=color(NAVY))
    font_free = load_font(24)
    draw_text_centered(draw, "Free picks. Every game day.", btn_y+bh+pad*2+20, font_free, MED_GRAY)
    draw_slide_indicator(draw, 5)
    img.save(os.path.join(OUTPUT_DIR, "slide_05_results.png"))
    print("✅ slide_05_results.png")

# ── 시즌 기록 계산 ─────────────────────────────────────────────────────────
def calc_season_record(preds: list) -> tuple[int, int]:
    wins = sum(1 for g in preds if g.get("model_correct") is True)
    losses = sum(1 for g in preds if g.get("model_correct") is False)
    return wins, losses

# ── MAIN ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    preds = load_predictions()
    date_label  = fmt_date_label(POST_DATE)
    total_games = len(preds)
    top_picks   = get_top_picks(preds, n=5)
    wins, losses = calc_season_record(preds)

    print(f"\n📅 날짜: {date_label}  |  경기: {total_games}  |  TOP픽: {len(top_picks)}개")
    print(f"📊 시즌 기록: {wins}W {losses}L")

    if not top_picks:
        print("⚠️  TOP 픽이 없어요. 기본 슬라이드로 생성합니다.")
        top_picks = [{"away":"TBD","home":"TBD","pick":"TBD","opp":"TBD",
                      "pct":"??%","pct_val":0,"away_sp":"TBD","home_sp":"TBD",
                      "away_sp_detail":{},"home_sp_detail":{},"away_era":"?","home_era":"?"}]

    slide_01(date_label, total_games)
    slide_02(top_picks[0], date_label)
    slide_03(top_picks, date_label)
    slide_04()
    slide_05(wins, losses)

    print(f"\n✅ 5장 슬라이드 저장 완료: {OUTPUT_DIR}")
