#!/usr/bin/env python3
"""
MLB Scorecard — Instagram Card Generator (White Style)
instagram/generate_card.py

Generates a 1080×1080 white-background analytics card for Instagram posts.
Works both locally (uses canvas-design plugin fonts) and on GitHub Actions
(downloads fonts from Google Fonts as fallback).

Usage:
    from instagram.generate_card import generate_prediction_card, generate_results_card
    path = generate_prediction_card(post_date, predictions, output_dir)
    path = generate_results_card(post_date, predictions, output_dir)
"""

import os
import sys
import json
import urllib.request
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("⚠️  Pillow not installed. Run: pip install Pillow")
    sys.exit(1)

# ── Font resolution ────────────────────────────────────────────────────────────
# Priority: 1) canvas-design plugin fonts, 2) repo-bundled fonts, 3) Google Fonts download

_CANVAS_FONTS = Path(
    "/Users/michaelkim/.claude/plugins/cache/anthropic-agent-skills"
    "/document-skills/3b3fad96af16/skills/canvas-design/canvas-fonts"
)
_REPO_FONTS = Path(__file__).parent / "fonts"

# Google Fonts direct download URLs
_FONT_URLS = {
    "BigShoulders-Bold.ttf": (
        "https://fonts.gstatic.com/s/bigshoulderstext/v24/"
        "55xEezRtP9rzL4KLxkgau10-xn_ZRS-D0wN9q1Ywekk.ttf"
    ),
    "GeistMono-Bold.ttf": (
        "https://github.com/vercel/geist-font/raw/main/packages/next/dist/fonts/"
        "geist-mono/GeistMono-Bold.ttf"
    ),
    "InstrumentSans-Bold.ttf": (
        "https://fonts.gstatic.com/s/instrumentsans/v1/"
        "pximypc9vsFDm051Uf6KVwgkfoSxQ0GsQv8ToedPibnr-yp2JGEJOH.ttf"
    ),
    "InstrumentSans-Regular.ttf": (
        "https://fonts.gstatic.com/s/instrumentsans/v1/"
        "pximypc9vsFDm051Uf6KVwgkfoSxQ0GsQv8ToedPibnr-yp2JGEJOH.ttf"
    ),
    "IBMPlexMono-Regular.ttf": (
        "https://fonts.gstatic.com/s/ibmplexmono/v19/"
        "-F63fjptAgt5VM-kVkqdyU8n3oQIwl1FgsAXvvnI.ttf"
    ),
}

# Fallback: system fonts that are reliably present on most Linux runners
_SYSTEM_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNSDisplay.ttf",
]


def _ensure_fonts() -> Path:
    """Return a directory that contains the required font files."""
    # 1. canvas-design plugin (local dev)
    if _CANVAS_FONTS.exists() and (_CANVAS_FONTS / "BigShoulders-Bold.ttf").exists():
        return _CANVAS_FONTS

    # 2. repo-bundled fonts
    _REPO_FONTS.mkdir(parents=True, exist_ok=True)
    all_present = all((_REPO_FONTS / n).exists() for n in _FONT_URLS)
    if all_present:
        return _REPO_FONTS

    # 3. Download from Google Fonts
    print("📥 Downloading fonts for card generator...")
    for fname, url in _FONT_URLS.items():
        dest = _REPO_FONTS / fname
        if dest.exists():
            continue
        try:
            print(f"   ↓ {fname}")
            urllib.request.urlretrieve(url, str(dest))
        except Exception as e:
            print(f"   ⚠️  Failed to download {fname}: {e}")

    if any((_REPO_FONTS / n).exists() for n in _FONT_URLS):
        return _REPO_FONTS

    return None  # will use PIL default


def _load_font(font_dir: Path | None, name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a font with graceful fallback."""
    if font_dir:
        path = font_dir / name
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                pass
    # system font fallback
    for sf in _SYSTEM_FONT_CANDIDATES:
        if Path(sf).exists():
            try:
                return ImageFont.truetype(sf, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ── Color palette ──────────────────────────────────────────────────────────────
BG        = (255, 255, 255)
CARD_BLUE = (235, 245, 255)
CARD_RED  = (255, 240, 240)
CARD_GRAY = (247, 249, 252)
CARD_GRN  = (235, 252, 242)
NAVY      = ( 10,  22,  40)
BLUE      = ( 14, 165, 233)
BLUE_DARK = (  7,  89, 133)
AMBER     = (194, 120,   0)
AMBER_BG  = (255, 245, 220)
RED       = (200,  45,  45)
RED_DARK  = (160,  30,  30)
GREEN     = ( 22, 140,  72)
GREEN_DRK = ( 15,  90,  50)
SLATE     = ( 71,  85, 105)
MUTED     = (148, 163, 184)
DIVIDER   = (220, 228, 240)
BORDER_B  = ( 14, 165, 233)
BORDER_R  = (200,  45,  45)
BORDER_G  = ( 22, 140,  72)

W = H = 1080
M = 54  # margin


# ── Drawing helpers ────────────────────────────────────────────────────────────
def _tw(draw, text, fnt):
    bb = draw.textbbox((0, 0), text, font=fnt)
    return bb[2] - bb[0], bb[3] - bb[1]


def _cx(draw, text, fnt, y, color):
    w_, _ = _tw(draw, text, fnt)
    draw.text(((W - w_) // 2, y), text, font=fnt, fill=color)


def _rr(draw, x0, y0, x1, y1, fill, rad=0, outline=None, ow=2):
    if rad:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=rad, fill=fill, outline=outline, width=ow)
    else:
        draw.rectangle([x0, y0, x1, y1], fill=fill, outline=outline)


def _bar(draw, x, y, w, h, frac, filled_color, rad=2):
    """Draw a progress bar (frac 0–1)."""
    _rr(draw, x, y, x + w, y + h, DIVIDER, rad=rad)
    if frac > 0:
        _rr(draw, x, y, x + int(w * min(frac, 1.0)), y + h, filled_color, rad=rad)


# ── Prediction card ────────────────────────────────────────────────────────────
def generate_prediction_card(
    post_date: str,
    predictions: list,
    output_dir: Path,
    abbr_fn=None,
) -> Path | None:
    """
    Generate a white-style 1080×1080 prediction card.

    Args:
        post_date:   "YYYY-MM-DD"
        predictions: list of game dicts (from predictions.json)
        output_dir:  directory to save the PNG
        abbr_fn:     optional team abbreviation function

    Returns:
        Path to the saved PNG, or None on failure.
    """
    try:
        font_dir = _ensure_fonts()
        return _draw_prediction_card(post_date, predictions, output_dir, font_dir, abbr_fn)
    except Exception as e:
        print(f"⚠️  Card generation failed: {e}")
        import traceback; traceback.print_exc()
        return None


def generate_results_card(
    post_date: str,
    predictions: list,
    output_dir: Path,
    abbr_fn=None,
) -> Path | None:
    """Generate a white-style 1080×1080 results card."""
    try:
        font_dir = _ensure_fonts()
        return _draw_results_card(post_date, predictions, output_dir, font_dir, abbr_fn)
    except Exception as e:
        print(f"⚠️  Results card generation failed: {e}")
        import traceback; traceback.print_exc()
        return None


# ── Prediction card internals ──────────────────────────────────────────────────
def _draw_prediction_card(post_date, preds, output_dir, font_dir, abbr_fn):
    if abbr_fn is None:
        abbr_fn = _abbr_default

    dt_label = datetime.strptime(post_date, "%Y-%m-%d").strftime("%b %-d, %Y").upper()

    img = Image.new("RGB", (W, H), BG)
    d   = ImageDraw.Draw(img)

    # fonts
    fHook  = _load_font(font_dir, "BigShoulders-Bold.ttf",    90)
    fNumLg = _load_font(font_dir, "GeistMono-Bold.ttf",       48)
    fNumMd = _load_font(font_dir, "GeistMono-Bold.ttf",       30)
    fLbB   = _load_font(font_dir, "InstrumentSans-Bold.ttf",  16)
    fLb    = _load_font(font_dir, "InstrumentSans-Regular.ttf", 14)
    fTiny  = _load_font(font_dir, "InstrumentSans-Regular.ttf", 11)
    fMono  = _load_font(font_dir, "IBMPlexMono-Regular.ttf",  12)

    # ── Top accent bar
    _rr(d, 0, 0, W, 6, BLUE)

    # ── Header
    d.text((M, 22), "MLB SCORECARD  ·  DAILY PICKS", font=fLb, fill=MUTED)
    dw, _ = _tw(d, dt_label, fLb)
    d.text((W - M - dw, 22), dt_label, font=fLb, fill=MUTED)
    d.line([(M, 50), (W - M, 50)], fill=DIVIDER, width=1)

    # ── Title / hook
    _cx(d, "TODAY'S", fHook, 62, NAVY)
    _cx(d, "MLB PICKS", fHook, 158, BLUE)
    uw, _ = _tw(d, "MLB PICKS", fHook)
    ux = (W - uw) // 2
    d.line([(ux, 258), (ux + uw, 258)], fill=BLUE, width=3)

    d.line([(M, 274), (W - M, 274)], fill=DIVIDER, width=1)

    # ── Build picks list
    top_picks = _get_picks(preds, abbr_fn, n=5)
    clean     = [p for p in top_picks if "vs" in p and not p.get("market_avoid")]
    avoid     = [p for p in top_picks if "vs" in p and p.get("market_avoid")]
    total_games = len(preds)

    # Section header
    SY = 288
    _rr(d, M, SY, W - M, SY + 22, (240, 245, 252))
    d.text((M + 12, SY + 4), "TOP PICKS", font=fTiny, fill=BLUE_DARK)
    d.text((W - M - 100, SY + 4), f"{total_games} GAMES TODAY", font=fTiny, fill=SLATE)

    ROW_H = 80
    y = SY + 26

    if clean:
        for i, p in enumerate(clean[:5]):
            bg   = CARD_BLUE if i == 0 else (CARD_GRAY if i % 2 == 0 else BG)
            bord = BORDER_B if i == 0 else DIVIDER
            _rr(d, M, y, W - M, y + ROW_H, bg)
            d.line([(M, y + ROW_H), (W - M, y + ROW_H)], fill=DIVIDER, width=1)
            _rr(d, M, y, M + 4, y + ROW_H, bord)

            # Star badge for top pick
            badge_x = M + 16
            if i == 0:
                _rr(d, badge_x, y + 12, badge_x + 46, y + 34, BLUE, rad=4)
                d.text((badge_x + 4, y + 14), "TOP", font=fTiny, fill=BG)

            # vs label
            d.text((M + 16, y + 38), p["vs"], font=fLbB, fill=SLATE)

            # pick + pct
            pick_txt = f"{p['pick']}  {p['pct']:.0f}%"
            d.text((M + 430, y + 10), pick_txt, font=fNumMd, fill=NAVY)

            # mini bar
            BAR_W = 200
            _bar(d, M + 430, y + 52, BAR_W, 5, p["pct"] / 100, BLUE)

            # edge note
            if abs(p.get("edge", 0)) > 0:
                edge_color = RED if p["edge"] < 0 else GREEN
                edge_txt = f"edge {p['edge']:+.1f}%"
                d.text((M + 430, y + 62), edge_txt, font=fTiny, fill=edge_color)

            y += ROW_H
    else:
        # No confirmed picks yet
        d.text((M + 16, y + 20), "Analyzing lineups — picks loading soon", font=fLbB, fill=SLATE)
        y += ROW_H

    # ── Market avoid section
    if avoid:
        y += 8
        _rr(d, M, y, W - M, y + 20, (255, 248, 220))
        d.text((M + 12, y + 3), "MARKET DISAGREEMENT — Lower Confidence", font=fTiny, fill=AMBER)
        y += 24
        for p in avoid[:2]:
            _rr(d, M, y, W - M, y + ROW_H, (255, 252, 240))
            d.line([(M, y + ROW_H), (W - M, y + ROW_H)], fill=DIVIDER, width=1)
            _rr(d, M, y, M + 4, y + ROW_H, AMBER)
            d.text((M + 16, y + 12), p["vs"], font=fLbB, fill=SLATE)
            d.text((M + 430, y + 12), f"{p['pick']}  {p['pct']:.0f}%", font=fNumMd, fill=NAVY)
            d.text((M + 430, y + 46), f"market disagrees  edge {p['edge']:+.1f}%", font=fTiny, fill=AMBER)
            y += ROW_H

    # ── Footer area
    FY = max(y + 20, H - 130)
    d.line([(M, FY), (W - M, FY)], fill=DIVIDER, width=1)

    # Sub-note
    note = "Predictions update as lineups confirm. Check mlb-scorecard.com before first pitch."
    nw, _ = _tw(d, note, fTiny)
    d.text(((W - nw) // 2, FY + 10), note, font=fTiny, fill=MUTED)

    # Branding row
    d.text((M, FY + 30), "@MLB_Scorecard", font=fLbB, fill=BLUE)
    sw, _ = _tw(d, "mlb-scorecard.com", fLb)
    d.text((W - M - sw, FY + 30), "mlb-scorecard.com", font=fLb, fill=MUTED)

    tag = "Every pick tracked. No edits. No excuses."
    tw2, _ = _tw(d, tag, fTiny)
    d.text(((W - tw2) // 2, FY + 56), tag, font=fTiny, fill=MUTED)

    disc = f"Data-driven MLB predictions  |  {total_games} games analyzed"
    dw2, _ = _tw(d, disc, fTiny)
    d.text(((W - dw2) // 2, FY + 76), disc, font=fTiny, fill=(190, 200, 215))

    # Bottom bar
    _rr(d, 0, H - 6, W, H, BLUE)

    # ── Save
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"card_prediction_{post_date}.png"
    img.save(str(out_path), dpi=(300, 300))
    print(f"✅ Prediction card saved: {out_path}")
    return out_path


# ── Results card internals ─────────────────────────────────────────────────────
def _draw_results_card(post_date, preds, output_dir, font_dir, abbr_fn):
    if abbr_fn is None:
        abbr_fn = _abbr_default

    dt_label = datetime.strptime(post_date, "%Y-%m-%d").strftime("%b %-d, %Y").upper()
    finished = [g for g in preds if g.get("actual_winner")]

    if not finished:
        return None

    # Compute results
    correct = 0
    result_rows = []
    for g in finished:
        wp       = g.get("win_prob", {})
        away_pct = wp.get("away", 50)
        home_pct = wp.get("home", 50)
        pick     = abbr_fn(g["home"]) if home_pct >= away_pct else abbr_fn(g["away"])
        actual   = abbr_fn(g["actual_winner"])
        hit      = pick == actual
        if hit:
            correct += 1
        result_rows.append({
            "label": f"{abbr_fn(g.get('away','?'))} @ {abbr_fn(g.get('home','?'))}",
            "pick":  pick,
            "actual": actual,
            "hit":   hit,
        })
    total = len(finished)
    pct   = correct / total * 100 if total else 0

    img = Image.new("RGB", (W, H), BG)
    d   = ImageDraw.Draw(img)

    fHook  = _load_font(font_dir, "BigShoulders-Bold.ttf",     90)
    fNumLg = _load_font(font_dir, "GeistMono-Bold.ttf",        64)
    fNumMd = _load_font(font_dir, "GeistMono-Bold.ttf",        28)
    fLbB   = _load_font(font_dir, "InstrumentSans-Bold.ttf",   16)
    fLb    = _load_font(font_dir, "InstrumentSans-Regular.ttf", 14)
    fTiny  = _load_font(font_dir, "InstrumentSans-Regular.ttf", 11)
    fMono  = _load_font(font_dir, "IBMPlexMono-Regular.ttf",   12)

    # ── Top accent bar
    score_color = GREEN if pct >= 60 else (AMBER if pct >= 50 else RED)
    _rr(d, 0, 0, W, 6, score_color)

    # ── Header
    d.text((M, 22), "MLB SCORECARD  ·  RESULTS", font=fLb, fill=MUTED)
    dw, _ = _tw(d, dt_label, fLb)
    d.text((W - M - dw, 22), dt_label, font=fLb, fill=MUTED)
    d.line([(M, 50), (W - M, 50)], fill=DIVIDER, width=1)

    # ── Big score display
    score_txt = f"{correct}-{total - correct}"
    record_bg = CARD_GRN if pct >= 60 else (CARD_RED if pct < 50 else CARD_GRAY)
    _rr(d, M, 62, W - M, 230, record_bg, rad=12)
    _cx(d, score_txt, fNumLg, 80, NAVY)
    _cx(d, f"{pct:.0f}% ACCURACY", fHook, 148, score_color)

    if pct >= 70:
        verdict = "The model was firing on all cylinders."
    elif pct >= 60:
        verdict = "Solid night. Consistent, data-driven picks."
    elif pct >= 50:
        verdict = "Not our best. Every result is logged, no exceptions."
    else:
        verdict = "Baseball humbles everyone. We own it. Back tomorrow."
    vw, _ = _tw(d, verdict, fLb)
    d.text(((W - vw) // 2, 230 + 8), verdict, font=fLb, fill=SLATE)

    d.line([(M, 270), (W - M, 270)], fill=DIVIDER, width=1)

    # ── Results table
    SY = 280
    _rr(d, M, SY, W - M, SY + 22, (240, 245, 252))
    d.text((M + 12, SY + 4), "GAME-BY-GAME RESULTS", font=fTiny, fill=BLUE_DARK)

    ROW_H = 58
    y = SY + 26
    max_rows = min(len(result_rows), 8)

    for i, row in enumerate(result_rows[:max_rows]):
        bg   = CARD_GRAY if i % 2 == 0 else BG
        bord = BORDER_G if row["hit"] else BORDER_R
        _rr(d, M, y, W - M, y + ROW_H, bg)
        d.line([(M, y + ROW_H), (W - M, y + ROW_H)], fill=DIVIDER, width=1)
        _rr(d, M, y, M + 4, y + ROW_H, bord)

        # Match label
        d.text((M + 16, y + 8), row["label"], font=fLbB, fill=SLATE)

        # Pick → actual
        arrow_txt = f"{row['pick']}  →  {row['actual']}"
        d.text((M + 16, y + 32), arrow_txt, font=fMono, fill=NAVY)

        # Hit/miss badge
        badge_c  = GREEN if row["hit"] else RED
        badge_bg = CARD_GRN if row["hit"] else CARD_RED
        badge_t  = "HIT" if row["hit"] else "MISS"
        _rr(d, W - M - 62, y + 14, W - M - 8, y + 38, badge_bg, rad=4)
        bw, _ = _tw(d, badge_t, fLbB)
        d.text((W - M - 8 - bw - (54 - bw) // 2, y + 17), badge_t, font=fLbB, fill=badge_c)

        y += ROW_H

    if len(result_rows) > max_rows:
        d.text((M + 16, y + 8), f"+{len(result_rows) - max_rows} more games", font=fTiny, fill=MUTED)
        y += 28

    # ── Footer
    FY = max(y + 16, H - 130)
    d.line([(M, FY), (W - M, FY)], fill=DIVIDER, width=1)

    d.text((M, FY + 30), "@MLB_Scorecard", font=fLbB, fill=BLUE)
    sw, _ = _tw(d, "mlb-scorecard.com", fLb)
    d.text((W - M - sw, FY + 30), "mlb-scorecard.com", font=fLb, fill=MUTED)

    tag = "All picks posted before first pitch. All results tracked. No edits."
    tw2, _ = _tw(d, tag, fTiny)
    d.text(((W - tw2) // 2, FY + 56), tag, font=fTiny, fill=MUTED)

    _rr(d, 0, H - 6, W, H, score_color)

    # ── Save
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"card_results_{post_date}.png"
    img.save(str(out_path), dpi=(300, 300))
    print(f"✅ Results card saved: {out_path}")
    return out_path


# ── Team abbreviation helper ───────────────────────────────────────────────────
def _abbr_default(team_name: str) -> str:
    MAP = {
        "Los Angeles Dodgers": "LAD", "Chicago Cubs": "CHC",
        "Boston Red Sox": "BOS",      "Chicago White Sox": "CHW",
        "Philadelphia Phillies": "PHI","Washington Nationals": "WSH",
        "Cincinnati Reds": "CIN",      "Oakland Athletics": "OAK",
        "Houston Astros": "HOU",       "Toronto Blue Jays": "TOR",
        "New York Yankees": "NYY",     "New York Mets": "NYM",
        "Atlanta Braves": "ATL",       "Miami Marlins": "MIA",
        "St. Louis Cardinals": "STL",  "Milwaukee Brewers": "MIL",
        "Minnesota Twins": "MIN",      "Kansas City Royals": "KC",
        "Cleveland Guardians": "CLE",  "Detroit Tigers": "DET",
        "Tampa Bay Rays": "TB",        "Baltimore Orioles": "BAL",
        "Los Angeles Angels": "LAA",   "Seattle Mariners": "SEA",
        "Texas Rangers": "TEX",        "Colorado Rockies": "COL",
        "Arizona Diamondbacks": "ARI", "San Diego Padres": "SD",
        "San Francisco Giants": "SF",  "Pittsburgh Pirates": "PIT",
    }
    for full, short in MAP.items():
        if full.lower() in team_name.lower():
            return short
    return team_name[:3].upper()


def _get_picks(preds, abbr_fn, n=5):
    picks = []
    for g in preds:
        wp = g.get("win_prob", {})
        if not wp:
            continue
        sp_tbd = g.get("sp_tbd", {})
        if sp_tbd.get("any", False):
            continue
        bat_source = g.get("scorecard", {}).get("bat_source", "")
        if bat_source == "team_stats":
            continue

        away_pct     = wp.get("away", 50)
        home_pct     = wp.get("home", 50)
        edge         = g.get("edge", 0) or 0
        market_avoid = edge <= -10

        if home_pct >= away_pct and home_pct >= 55:
            picks.append({
                "pick": abbr_fn(g.get("home", "???")),
                "opp":  abbr_fn(g.get("away", "???")),
                "pct":  home_pct,
                "vs":   f"{abbr_fn(g.get('away','???'))} @ {abbr_fn(g.get('home','???'))}",
                "edge": edge,
                "market_avoid": market_avoid,
            })
        elif away_pct > home_pct and away_pct >= 55:
            picks.append({
                "pick": abbr_fn(g.get("away", "???")),
                "opp":  abbr_fn(g.get("home", "???")),
                "pct":  away_pct,
                "vs":   f"{abbr_fn(g.get('away','???'))} @ {abbr_fn(g.get('home','???'))}",
                "edge": edge,
                "market_avoid": market_avoid,
            })

    picks.sort(key=lambda x: (x.get("market_avoid", False), -x["pct"]))
    return picks[:n]


# ── CLI entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate MLB Scorecard Instagram card")
    parser.add_argument("--date", default=str(__import__("datetime").date.today()))
    parser.add_argument("--type", default="prediction", choices=["prediction", "results"])
    parser.add_argument("--predictions", default=None, help="Path to predictions.json")
    parser.add_argument("--output-dir", default="/tmp", help="Output directory")
    args = parser.parse_args()

    if args.predictions:
        with open(args.predictions, encoding="utf-8") as f:
            preds = json.load(f)
    else:
        preds = []

    if args.type == "results":
        path = generate_results_card(args.date, preds, Path(args.output_dir))
    else:
        path = generate_prediction_card(args.date, preds, Path(args.output_dir))

    if path:
        print(f"Card saved to: {path}")
    else:
        print("Card generation failed")
        sys.exit(1)
