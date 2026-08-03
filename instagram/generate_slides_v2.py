#!/usr/bin/env python3
"""
MLB Scorecard Instagram Carousel - Premium Sports Magazine Style
generate_slides_v2.py
"""

from PIL import Image, ImageDraw, ImageFont
import os

OUTPUT_DIR = "/Users/michaelkim/Desktop/MLB_Scorecard_Instagram"
SIZE = (1080, 1080)

# Color palette
WHITE      = "#FFFFFF"
NAVY       = "#0F172A"
BLUE       = "#2563EB"
GOLD       = "#F59E0B"
LIGHT_GRAY = "#F8FAFC"
MED_GRAY   = "#64748B"
GREEN      = "#10B981"
BORDER     = "#E2E8F0"
BLACK      = "#000000"

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def color(h):
    return hex_to_rgb(h)

def load_font(size, bold=False):
    """Try system fonts in order of preference."""
    font_candidates = []
    if bold:
        font_candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Impact.ttf",
            "/System/Library/Fonts/SFNSDisplay-Bold.otf",
            "/System/Library/Fonts/SFNSText-Bold.otf",
        ]
    else:
        font_candidates = [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/SFNSDisplay.otf",
            "/System/Library/Fonts/SFNSText.otf",
        ]
    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()

def draw_text_centered(draw, text, y, font, fill, img_width=1080):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    x = (img_width - w) // 2
    draw.text((x, y), text, font=font, fill=color(fill))

def draw_slide_indicator(draw, slide_num, total=5):
    """Small subtle slide indicator top-right."""
    font = load_font(22)
    text = f"{slide_num}/{total}"
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((1080 - w - 36, 36), text, font=font, fill=color(MED_GRAY))

def add_watermark(draw):
    font = load_font(24)
    draw.text((540 - 80, 1040), "@MLB_Scorecard", font=font, fill=color(MED_GRAY))

# ── SLIDE 1 — COVER ─────────────────────────────────────────────────────────
def slide_01():
    img = Image.new("RGB", SIZE, color(WHITE))
    draw = ImageDraw.Draw(img)

    # Top navy block (top 1/4)
    navy_h = 270
    draw.rectangle([(0, 0), (1080, navy_h)], fill=color(NAVY))

    # "MLB SCORECARD" in navy block
    font_header = load_font(52, bold=True)
    text = "MLB SCORECARD"
    bbox = draw.textbbox((0, 0), text, font=font_header)
    tw = bbox[2] - bbox[0]
    tx = (1080 - tw) // 2
    ty = 90
    draw.text((tx, ty), text, font=font_header, fill=color(WHITE))

    # Electric blue underline
    line_y = ty + (bbox[3] - bbox[1]) + 12
    line_w = tw + 40
    lx = (1080 - line_w) // 2
    draw.rectangle([(lx, line_y), (lx + line_w, line_y + 5)], fill=color(BLUE))

    # Small tag under underline
    font_tag = load_font(24)
    tag = "PREMIUM DATA  ·  DAILY PICKS"
    bbox2 = draw.textbbox((0, 0), tag, font=font_tag)
    draw.text(((1080 - (bbox2[2]-bbox2[0])) // 2, line_y + 18), tag, font=font_tag, fill=color(MED_GRAY))

    # Large baseball circle in light blue, partially cut off right edge
    # Draw as decorative circle
    cx, cy, r = 980, 540, 280
    draw.ellipse([(cx-r, cy-r), (cx+r, cy+r)], fill=color("#DBEAFE"))
    draw.ellipse([(cx-r+20, cy-r+20), (cx+r-20, cy+r-20)], outline=color(BLUE), width=6)
    # Baseball stitching lines
    for dx in [-30, 30]:
        draw.arc([(cx+dx-50, cy-80), (cx+dx+50, cy+80)], start=-60, end=60, fill=color(BLUE), width=4)

    # Baseball emoji text
    font_ball = load_font(160, bold=True)
    draw.text((870, 400), "⚾", font=font_ball, fill=color(BLUE))

    # "TODAY'S" in electric blue, huge
    font_huge = load_font(130, bold=True)
    text1 = "TODAY'S"
    bbox1 = draw.textbbox((0, 0), text1, font=font_huge)
    draw.text((60, navy_h + 30), text1, font=font_huge, fill=color(BLUE))

    # "PICKS" in navy, huge
    text2 = "PICKS"
    bbox2b = draw.textbbox((0, 0), text2, font=font_huge)
    draw.text((60, navy_h + 165), text2, font=font_huge, fill=color(NAVY))

    # "AUG 4, 2025" gold
    font_date = load_font(34, bold=True)
    draw.text((62, navy_h + 315), "AUG 4, 2025", font=font_date, fill=color(GOLD))

    # Thin gold line
    draw.rectangle([(60, navy_h + 362), (520, navy_h + 367)], fill=color(GOLD))

    # Sub-tagline
    font_sub = load_font(26)
    draw.text((62, navy_h + 382), "15 GAMES  ·  DATA-DRIVEN  ·  FREE", font=font_sub, fill=color(MED_GRAY))

    # Bottom "@MLB_Scorecard"
    font_handle = load_font(26)
    draw.text((62, 1020), "@MLB_Scorecard", font=font_handle, fill=color(NAVY))

    draw_slide_indicator(draw, 1)

    img.save(os.path.join(OUTPUT_DIR, "slide_01_cover.png"))
    print("Saved slide_01_cover.png")


# ── SLIDE 2 — TOP PICK ───────────────────────────────────────────────────────
def slide_02():
    img = Image.new("RGB", SIZE, color(WHITE))
    draw = ImageDraw.Draw(img)

    # Left blue vertical strip
    draw.rectangle([(0, 0), (80, 1080)], fill=color(BLUE))

    LEFT = 110

    # "HIGH CONFIDENCE" small caps blue
    font_sm_caps = load_font(28, bold=True)
    draw.text((LEFT, 60), "HIGH CONFIDENCE", font=font_sm_caps, fill=color(BLUE))

    # "PICK OF THE DAY" gray
    font_sub = load_font(26)
    draw.text((LEFT, 100), "PICK OF THE DAY", font=font_sub, fill=color(MED_GRAY))

    # Thin divider
    draw.rectangle([(LEFT, 142), (1050, 144)], fill=color(BORDER))

    # Giant matchup: LAD  @  CHC
    font_team = load_font(100, bold=True)
    font_at = load_font(70)

    y_match = 165
    draw.text((LEFT, y_match), "LAD", font=font_team, fill=color(NAVY))
    draw.text((LEFT + 250, y_match + 20), "@", font=font_at, fill=color(MED_GRAY))
    draw.text((LEFT + 345, y_match), "CHC", font=font_team, fill=color("#94A3B8"))

    # Navy rectangle with "LAD 63%"
    rect_y = y_match + 115
    draw.rectangle([(LEFT, rect_y), (LEFT + 400, rect_y + 90)], fill=color(NAVY))
    font_pct = load_font(52, bold=True)
    draw.text((LEFT + 24, rect_y + 18), "LAD  63%", font=font_pct, fill=color(WHITE))

    # Small label
    font_label = load_font(22)
    draw.text((LEFT + 420, rect_y + 34), "WIN PROBABILITY", font=font_label, fill=color(MED_GRAY))

    # Thin divider
    div_y = rect_y + 110
    draw.rectangle([(LEFT, div_y), (1050, div_y + 1)], fill=color(BORDER))

    # Pitcher cards
    card_y = div_y + 20
    card_h = 175
    card_w = 430

    # Left card (Skubal)
    draw.rectangle([(LEFT, card_y), (LEFT + card_w, card_y + card_h)], fill=color(LIGHT_GRAY))
    draw.rectangle([(LEFT, card_y), (LEFT + 8, card_y + card_h)], fill=color(BLUE))
    font_name = load_font(40, bold=True)
    font_stat = load_font(28)
    font_hot  = load_font(24, bold=True)
    draw.text((LEFT + 22, card_y + 14), "SKUBAL", font=font_name, fill=color(NAVY))
    draw.text((LEFT + 22, card_y + 68), "ERA  2.83", font=font_stat, fill=color(BLUE))
    draw.text((LEFT + 22, card_y + 108), "WHIP  0.97", font=font_stat, fill=color(MED_GRAY))
    draw.text((LEFT + 22, card_y + 140), "🔥 HOT STREAK", font=font_hot, fill=color(GOLD))

    # Right card (Assad)
    rx = LEFT + card_w + 30
    draw.rectangle([(rx, card_y), (rx + card_w, card_y + card_h)], fill=color(LIGHT_GRAY))
    draw.rectangle([(rx, card_y), (rx + 8, card_y + card_h)], fill=color(MED_GRAY))
    draw.text((rx + 22, card_y + 14), "ASSAD", font=font_name, fill=color(NAVY))
    draw.text((rx + 22, card_y + 68), "ERA  2.47", font=font_stat, fill=color(BLUE))
    draw.text((rx + 22, card_y + 108), "WHIP  1.07", font=font_stat, fill=color(MED_GRAY))
    draw.text((rx + 22, card_y + 140), "🔥 HOT STREAK", font=font_hot, fill=color(GOLD))

    # Divider
    bottom_div = card_y + card_h + 22
    draw.rectangle([(LEFT, bottom_div), (1050, bottom_div + 1)], fill=color(BORDER))

    # Model breakdown
    model_y = bottom_div + 14
    font_model = load_font(22)
    draw.text((LEFT, model_y), "SCORECARD MODEL:  SP 30%  ·  BP 20%  ·  BAT 35%  ·  SIT 15%", font=font_model, fill=color(MED_GRAY))

    # "@MLB_Scorecard" bottom right
    font_handle = load_font(24)
    bbox = draw.textbbox((0,0), "@MLB_Scorecard", font=font_handle)
    draw.text((1050 - (bbox[2]-bbox[0]), 1040), "@MLB_Scorecard", font=font_handle, fill=color(NAVY))

    draw_slide_indicator(draw, 2)
    img.save(os.path.join(OUTPUT_DIR, "slide_02_top_pick.png"))
    print("Saved slide_02_top_pick.png")


# ── SLIDE 3 — FULL CARD ──────────────────────────────────────────────────────
def slide_03():
    img = Image.new("RGB", SIZE, color(WHITE))
    draw = ImageDraw.Draw(img)

    # Top blue header block
    draw.rectangle([(0, 0), (1080, 130)], fill=color(BLUE))
    font_hdr = load_font(58, bold=True)
    draw_text_centered(draw, "AUG 4 PICKS", 32, font_hdr, WHITE)

    # Column headers
    y = 155
    font_col = load_font(24, bold=True)
    draw.text((60, y), "MATCHUP", font=font_col, fill=color(MED_GRAY))
    draw.text((590, y), "PICK", font=font_col, fill=color(MED_GRAY))
    draw.text((820, y), "CONFIDENCE", font=font_col, fill=color(MED_GRAY))

    draw.rectangle([(40, y + 36), (1040, y + 37)], fill=color(BORDER))

    # Rows
    rows = [
        ("CIN", "vs", "OAK",  "CIN", "63%", False),
        ("BOS", "vs", "CHW",  "BOS", "67%", True),   # ⭐ star
        ("PHI", "vs", "WSH",  "PHI", "63%", False),
        ("LAD", "vs", "CHC",  "LAD", "63%", False),
        ("HOU", "vs", "TOR",  "HOU", "62%", False),
    ]

    font_team_r = load_font(42, bold=True)
    font_vs     = load_font(30)
    font_pick   = load_font(48, bold=True)
    font_pct    = load_font(46, bold=True)
    font_star   = load_font(28)

    row_h = 110
    row_start = y + 50

    for i, (t1, vs, t2, pick, pct, star) in enumerate(rows):
        ry = row_start + i * row_h

        # Subtle alternating
        if i % 2 == 1:
            draw.rectangle([(40, ry), (1040, ry + row_h - 4)], fill=color(LIGHT_GRAY))

        # Teams
        draw.text((60, ry + 28), t1, font=font_team_r, fill=color(NAVY))
        draw.text((178, ry + 38), vs, font=font_vs, fill=color(MED_GRAY))
        draw.text((238, ry + 28), t2, font=font_team_r, fill=color(MED_GRAY))

        # Pick
        draw.text((590, ry + 22), pick, font=font_pick, fill=color(BLUE))

        # Star
        if star:
            draw.text((690, ry + 22), " ⭐", font=font_star, fill=color(GOLD))

        # Percentage
        draw.text((820, ry + 22), pct, font=font_pct, fill=color(GOLD))

        # Bottom thin divider
        draw.rectangle([(40, ry + row_h - 4), (1040, ry + row_h - 3)], fill=color(BORDER))

    # Bottom gray stats bar
    bar_y = row_start + len(rows) * row_h + 20
    draw.rectangle([(0, bar_y), (1080, bar_y + 70)], fill=color(LIGHT_GRAY))
    font_bar = load_font(26)
    draw_text_centered(draw, "5 PICKS TODAY  ·  AVG CONFIDENCE: 63.6%", bar_y + 22, font_bar, MED_GRAY)

    # "@MLB_Scorecard"
    font_handle = load_font(24)
    bbox = draw.textbbox((0,0), "@MLB_Scorecard", font=font_handle)
    draw.text(((1080-(bbox[2]-bbox[0]))//2, 1040), "@MLB_Scorecard", font=font_handle, fill=color(MED_GRAY))

    draw_slide_indicator(draw, 3)
    img.save(os.path.join(OUTPUT_DIR, "slide_03_full_card.png"))
    print("Saved slide_03_full_card.png")


# ── SLIDE 4 — HOW WE SCORE ───────────────────────────────────────────────────
def slide_04():
    img = Image.new("RGB", SIZE, color(WHITE))
    draw = ImageDraw.Draw(img)

    LEFT = 60

    # "HOW WE" small gray
    font_sm = load_font(34)
    draw.text((LEFT, 70), "HOW WE", font=font_sm, fill=color(MED_GRAY))

    # "SCORE" giant navy
    font_giant = load_font(148, bold=True)
    draw.text((LEFT, 105), "SCORE", font=font_giant, fill=color(NAVY))

    # Gold underline accent
    draw.rectangle([(LEFT, 255), (LEFT + 520, 262)], fill=color(GOLD))
    draw.rectangle([(LEFT, 268), (LEFT + 200, 272)], fill=color(BLUE))

    # Four factor rows
    factors = [
        ("30%", BLUE,  "SP SCORE",    "Starter ERA, WHIP, recent form & rest days"),
        ("20%", GREEN, "BULLPEN",     "Pen ERA, usage load, save situations"),
        ("35%", GOLD,  "BATTING",     "OPS, recent 10G trend, lineup depth"),
        ("15%", MED_GRAY, "SITUATIONAL", "Home/away, streak, weather, park factor"),
    ]

    font_pct_big = load_font(64, bold=True)
    font_factor  = load_font(36, bold=True)
    font_desc    = load_font(26)

    row_y = 300
    for pct, clr, name, desc in factors:
        draw.rectangle([(LEFT - 10, row_y), (1060, row_y + 1)], fill=color(BORDER))
        row_y += 14

        # Big percentage left
        draw.text((LEFT, row_y), pct, font=font_pct_big, fill=color(clr))

        # Factor name + description
        draw.text((LEFT + 155, row_y + 4), name, font=font_factor, fill=color(NAVY))
        draw.text((LEFT + 155, row_y + 50), desc, font=font_desc, fill=color(MED_GRAY))

        row_y += 115

    draw.rectangle([(LEFT - 10, row_y), (1060, row_y + 1)], fill=color(BORDER))

    # Italic tagline
    font_italic = load_font(28)
    draw.text((LEFT, row_y + 22), "Transparent. Data-driven. No hype.", font=font_italic, fill=color(MED_GRAY))

    # "@MLB_Scorecard"
    font_handle = load_font(24)
    draw.text((LEFT, 1040), "@MLB_Scorecard", font=font_handle, fill=color(NAVY))

    draw_slide_indicator(draw, 4)
    img.save(os.path.join(OUTPUT_DIR, "slide_04_how_we_score.png"))
    print("Saved slide_04_how_we_score.png")


# ── SLIDE 5 — RESULTS ────────────────────────────────────────────────────────
def slide_05():
    img = Image.new("RGB", SIZE, color(WHITE))
    draw = ImageDraw.Draw(img)

    # Top half navy
    navy_h = 520
    draw.rectangle([(0, 0), (1080, navy_h)], fill=color(NAVY))

    # "SEASON" small white
    font_sm = load_font(30)
    draw.text((60, 60), "SEASON", font=font_sm, fill=color("#94A3B8"))

    # "ACCURACY" giant white
    font_giant = load_font(110, bold=True)
    draw.text((60, 95), "ACCURACY", font=font_giant, fill=color(WHITE))

    # Huge "58.3%" in electric blue — right side of navy
    font_huge_pct = load_font(160, bold=True)
    pct_text = "58.3%"
    bbox = draw.textbbox((0,0), pct_text, font=font_huge_pct)
    pw = bbox[2] - bbox[0]
    # center it in right portion
    draw.text(((1080 - pw) // 2, 270), pct_text, font=font_huge_pct, fill=color(BLUE))

    # Bottom white half
    # "21W  15L" record
    font_record = load_font(90, bold=True)
    draw_text_centered(draw, "21W   15L", navy_h + 50, font_record, NAVY)

    # Progress bar
    bar_y = navy_h + 160
    bar_w = 900
    bx = (1080 - bar_w) // 2
    draw.rectangle([(bx, bar_y), (bx + bar_w, bar_y + 22)], fill=color(BORDER))
    filled = int(bar_w * 0.583)
    draw.rectangle([(bx, bar_y), (bx + filled, bar_y + 22)], fill=color(BLUE))
    # Marker dot
    draw.ellipse([(bx + filled - 14, bar_y - 8), (bx + filled + 14, bar_y + 30)], fill=color(BLUE))

    # Thin navy labels on bar
    font_bar_lbl = load_font(20)
    draw.text((bx, bar_y + 30), "0%", font=font_bar_lbl, fill=color(MED_GRAY))
    draw.text((bx + bar_w - 30, bar_y + 30), "100%", font=font_bar_lbl, fill=color(MED_GRAY))

    # Italic disclaimer
    font_italic = load_font(26)
    draw_text_centered(draw, "Every pick tracked publicly. No cherry-picking.", bar_y + 70, font_italic, MED_GRAY)

    # Gold button-style box "FOLLOW @MLB_Scorecard →"
    btn_y = bar_y + 120
    btn_text = "FOLLOW @MLB_Scorecard  →"
    font_btn = load_font(30, bold=True)
    bbox_btn = draw.textbbox((0,0), btn_text, font=font_btn)
    bw = bbox_btn[2] - bbox_btn[0]
    bh = bbox_btn[3] - bbox_btn[1]
    pad = 20
    btn_x = (1080 - bw - pad*2) // 2
    draw.rectangle([(btn_x, btn_y), (btn_x + bw + pad*2, btn_y + bh + pad*2)], fill=color(GOLD))
    draw.text((btn_x + pad, btn_y + pad), btn_text, font=font_btn, fill=color(NAVY))

    # Small: "Free picks. Every game day."
    font_free = load_font(24)
    draw_text_centered(draw, "Free picks. Every game day.", btn_y + bh + pad*2 + 20, font_free, MED_GRAY)

    draw_slide_indicator(draw, 5)
    img.save(os.path.join(OUTPUT_DIR, "slide_05_results.png"))
    print("Saved slide_05_results.png")


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    slide_01()
    slide_02()
    slide_03()
    slide_04()
    slide_05()
    print("\nAll 5 slides generated successfully!")
