#!/usr/bin/env python3
"""
MLB Scorecard — TBD 선발투수 이메일 알림
src/notify_tbd_pitchers.py

daily_init 후 실행 → TBD/미확정 선발이 있는 경기를 이메일로 알림

Usage:
    python3 src/notify_tbd_pitchers.py --date 2026-09-11
"""

import os
import json
import argparse
import smtplib
from datetime import date, datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# ── 설정 ─────────────────────────────────────────────────────────────
GMAIL_USER     = os.environ.get("GMAIL_USER",     "mikegood.kim@gmail.com")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")
NOTIFY_TO      = os.environ.get("NOTIFY_TO",      "mikegood.kim@gmail.com")

BASE_DIR   = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"

TBD_KEYWORDS = ("TBD", "미정", "")


def is_tbd(pitcher_name: str, pitcher_id) -> bool:
    if pitcher_id is None:
        return True
    name = (pitcher_name or "").strip()
    return any(kw in name for kw in TBD_KEYWORDS) or not name


def load_predictions(game_date: str) -> list:
    """output/predictions.json 로드"""
    pred_file = OUTPUT_DIR / "predictions.json"
    if not pred_file.exists():
        return []
    data = json.loads(pred_file.read_text(encoding="utf-8"))
    return [g for g in data if g.get("date") == game_date]


def find_tbd_games(games: list) -> list:
    """TBD 선발이 있는 경기 목록 반환"""
    tbd = []
    for g in games:
        away_tbd = is_tbd(g.get("away_pitcher", ""), g.get("away_pitcher_id"))
        home_tbd = is_tbd(g.get("home_pitcher", ""), g.get("home_pitcher_id"))
        if away_tbd or home_tbd:
            tbd.append({
                "away":         g.get("away", ""),
                "home":         g.get("home", ""),
                "game_time":    g.get("game_time", ""),
                "away_pitcher": g.get("away_pitcher", "TBD"),
                "home_pitcher": g.get("home_pitcher", "TBD"),
                "away_tbd":     away_tbd,
                "home_tbd":     home_tbd,
            })
    return tbd


def build_email(game_date: str, tbd_games: list, total_games: int) -> tuple:
    """(subject, html_body, plain_body) 반환"""
    dt = datetime.strptime(game_date, "%Y-%m-%d")
    date_label = dt.strftime("%b %-d, %Y (%a)")

    subject = f"⚾ [{date_label}] MLB 선발투수 TBD {len(tbd_games)}경기 확인 필요"

    # Plain text
    lines = [
        f"MLB Scorecard — 선발투수 TBD 알림",
        f"날짜: {date_label}",
        f"전체 {total_games}경기 중 TBD {len(tbd_games)}경기\n",
    ]
    for g in tbd_games:
        away_tag = " ← TBD" if g["away_tbd"] else ""
        home_tag = " ← TBD" if g["home_tbd"] else ""
        lines.append(
            f"  {g['away']} @ {g['home']}  ({g['game_time']})\n"
            f"    원정: {g['away_pitcher']}{away_tag}\n"
            f"    홈:   {g['home_pitcher']}{home_tag}"
        )
    lines += [
        "",
        "※ PITCHER_OVERRIDES (src/mlb_schedule.py) 에 올바른 선발을 수동 등록하세요.",
        "※ 또는 MLB 공식 사이트 / 팀 SNS에서 확인 후 업데이트 요청하세요.",
    ]
    plain = "\n".join(lines)

    # HTML
    rows = ""
    for g in tbd_games:
        away_style = "color:#EF4444;font-weight:bold;" if g["away_tbd"] else ""
        home_style = "color:#EF4444;font-weight:bold;" if g["home_tbd"] else ""
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #1e3a5f;">
            <b>{g['away']}</b> @ <b>{g['home']}</b>
            <span style="color:#94A3B8;font-size:12px;margin-left:8px;">{g['game_time']}</span>
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #1e3a5f;{away_style}">{g['away_pitcher']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #1e3a5f;{home_style}">{g['home_pitcher']}</td>
        </tr>"""

    html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#0A1628;font-family:'Helvetica Neue',Arial,sans-serif;">
<div style="max-width:600px;margin:32px auto;background:#0d1f3c;border-radius:12px;overflow:hidden;border:1px solid #1e3a5f;">

  <!-- Header -->
  <div style="background:#0EA5E9;padding:20px 24px;">
    <div style="font-size:22px;font-weight:700;color:#fff;">⚾ MLB Scorecard</div>
    <div style="font-size:13px;color:#e0f2fe;margin-top:4px;">선발투수 TBD 알림 — {date_label}</div>
  </div>

  <!-- Summary -->
  <div style="padding:20px 24px;border-bottom:1px solid #1e3a5f;">
    <div style="font-size:15px;color:#94A3B8;">
      전체 <b style="color:#fff;">{total_games}경기</b> 중
      <b style="color:#EF4444;">{len(tbd_games)}경기</b>에서 선발투수가 미확정입니다.
    </div>
  </div>

  <!-- Table -->
  <table width="100%" cellpadding="0" cellspacing="0" style="color:#fff;font-size:14px;">
    <thead>
      <tr style="background:#0f2744;">
        <th style="padding:10px 12px;text-align:left;color:#94A3B8;font-weight:500;">경기</th>
        <th style="padding:10px 12px;text-align:left;color:#94A3B8;font-weight:500;">원정 선발</th>
        <th style="padding:10px 12px;text-align:left;color:#94A3B8;font-weight:500;">홈 선발</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>

  <!-- Footer -->
  <div style="padding:20px 24px;border-top:1px solid #1e3a5f;">
    <div style="font-size:12px;color:#64748B;line-height:1.6;">
      ⚠️ <b style="color:#F59E0B;">조치 방법:</b> <code style="color:#0EA5E9;">src/mlb_schedule.py</code>의
      <code style="color:#0EA5E9;">PITCHER_OVERRIDES</code>에 올바른 선발을 수동 등록하거나,<br>
      MLB 공식 사이트 / 팀 SNS에서 확인 후 Claude에게 업데이트 요청하세요.
    </div>
    <div style="margin-top:12px;font-size:11px;color:#475569;">
      mlb-scorecard.com · Auto-generated by MLB Scorecard Bot
    </div>
  </div>
</div>
</body>
</html>"""

    return subject, html, plain


def send_email(subject: str, html: str, plain: str) -> bool:
    if not GMAIL_APP_PASS:
        print("⚠️  GMAIL_APP_PASS 미설정 — 이메일 전송 스킵")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"MLB Scorecard Bot <{GMAIL_USER}>"
    msg["To"]      = NOTIFY_TO

    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASS)
            server.sendmail(GMAIL_USER, NOTIFY_TO, msg.as_string())
        print(f"✅ 이메일 전송 완료 → {NOTIFY_TO}")
        return True
    except Exception as e:
        print(f"❌ 이메일 전송 실패: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--dry-run", action="store_true", help="이메일 전송 없이 내용만 출력")
    args = parser.parse_args()

    games = load_predictions(args.date)
    if not games:
        print(f"⚠️  {args.date} 예측 데이터 없음 — 알림 스킵")
        return

    tbd_games = find_tbd_games(games)

    if not tbd_games:
        print(f"✅ {args.date} TBD 선발 없음 — 알림 불필요")
        return

    print(f"📧 TBD 경기 {len(tbd_games)}/{len(games)}개 발견:")
    for g in tbd_games:
        print(f"  {g['away']} @ {g['home']} ({g['game_time']})")
        if g["away_tbd"]: print(f"    원정 TBD: {g['away_pitcher']}")
        if g["home_tbd"]: print(f"    홈   TBD: {g['home_pitcher']}")

    subject, html, plain = build_email(args.date, tbd_games, len(games))

    if args.dry_run:
        print(f"\n📝 [DRY RUN] Subject: {subject}")
        print(plain)
        return

    send_email(subject, html, plain)


if __name__ == "__main__":
    main()
