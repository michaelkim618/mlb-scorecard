"""
season_results.json 업데이트 스크립트
- output/predictions_YYYY-MM-DD.js 파일들을 스캔
- model_correct 가 확정된 경기들을 season_results.json 에 누적
- 시즌 전환 감지 (MLB 시즌: 3월 말 ~ 10월 말)
- 사용법: python src/update_season_results.py [web_repo_path]
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

# ─── 경로 설정 ──────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent
OUTPUT_DIR  = SCRIPT_DIR.parent / "output"
WEB_REPO    = Path(sys.argv[1]) if len(sys.argv) > 1 else SCRIPT_DIR.parent.parent / "mlb-scorecard-web"
OUT_FILE    = WEB_REPO / "public" / "season_results.json"

# ─── 시즌 설정 ──────────────────────────────────────────────────
SEASON_YEAR  = "2026"
SEASON_START = "2026-08-04"   # 우리 추적 시작일

# 다음 시즌 시작일 (대략 3월 말 → 갱신 시 수정)
NEXT_SEASON_START = "2027-03-25"


def parse_js(path: Path):
    content = path.read_text(encoding="utf-8")
    m = re.search(r'window\.PREDICTIONS_DATA\s*=\s*(\[.*?\]);', content, re.DOTALL)
    if not m:
        return []
    return json.loads(m.group(1))


def load_season_results() -> dict:
    if OUT_FILE.exists():
        return json.loads(OUT_FILE.read_text(encoding="utf-8"))
    return {
        "season": SEASON_YEAR,
        "start_date": SEASON_START,
        "games": [],
    }


def should_reset_season(existing: dict) -> bool:
    """다음 시즌 시작일이 지났으면 리셋"""
    try:
        next_start = date.fromisoformat(NEXT_SEASON_START)
        return date.today() >= next_start and existing.get("season") != str(date.today().year)
    except Exception:
        return False


def game_key(g: dict) -> str:
    return f"{g['date']}|{g['away']}|{g['home']}"


def build_game_entry(g, date_str):
    """predictions JS 게임 → season_results 엔트리 변환"""
    if g.get("model_correct") is None:
        return None  # 아직 결과 없음

    win_prob = g.get("win_prob", {})
    if isinstance(win_prob, dict):
        away_pct = win_prob.get("away", 50) or 50
        home_pct = win_prob.get("home", 50) or 50
    else:
        away_pct = home_pct = 50

    pick      = g.get("model_winner") or (g["home"] if home_pct >= away_pct else g["away"])
    pick_prob = round(max(away_pct, home_pct), 1)

    return {
        "date":          date_str,
        "away":          g.get("away", ""),
        "home":          g.get("home", ""),
        "pick":          pick,
        "pick_prob":     pick_prob,
        "actual_winner": g.get("actual_winner", ""),
        "correct":       bool(g.get("model_correct")),
        "high_conf":     pick_prob >= 65.0,   # Premium Pick 기준: 65%+
    }


def load_predictions_json() -> list:
    """웹 레포의 predictions.json 에서 오늘 경기 데이터 로드 (GitHub Actions용)"""
    pred_file = WEB_REPO / "public" / "predictions.json"
    if not pred_file.exists():
        return []
    try:
        data = json.loads(pred_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception as e:
        print(f"⚠️ predictions.json 로드 실패: {e}")
    return []


def update():
    existing = load_season_results()

    # 시즌 전환 감지
    if should_reset_season(existing):
        print(f"🔄 새 시즌 감지 → season_results 리셋 ({SEASON_YEAR} → {date.today().year})")
        existing = {
            "season":     str(date.today().year),
            "start_date": NEXT_SEASON_START,
            "games":      [],
        }

    # 기존 게임 키 세트 (중복 방지)
    existing_keys = {game_key(g) for g in existing["games"]}
    new_entries   = []

    # output 폴더에서 시즌 시작일 이후 파일 스캔
    start = date.fromisoformat(existing["start_date"])
    today = date.today()
    today_str = today.isoformat()

    for js_path in sorted(OUTPUT_DIR.glob("predictions_????-??-??.js")):
        m = re.search(r'predictions_(\d{4}-\d{2}-\d{2})\.js', js_path.name)
        if not m:
            continue
        date_str = m.group(1)
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            continue

        if d < start or d > today:   # 시즌 전 제외 (오늘 포함)
            continue

        games = parse_js(js_path)
        for g in games:
            entry = build_game_entry(g, date_str)
            if entry is None:
                continue
            key = game_key(entry)
            if key not in existing_keys:
                new_entries.append(entry)
                existing_keys.add(key)

    # ── 오늘 경기: predictions.json 에서 결과 확인 (GitHub Actions용) ──
    today_games = load_predictions_json()
    today_from_json = [g for g in today_games if g.get("gameDate", "") == today_str or g.get("date", "") == today_str]
    if not today_from_json and today_games:
        # gameDate 필드가 없는 경우 전체를 오늘로 처리 (당일 predictions.json 이므로)
        today_from_json = today_games

    for g in today_from_json:
        if g.get("model_correct") is None and g.get("actual_winner") is None:
            continue  # 아직 경기 전 또는 진행 중
        entry = build_game_entry(g, today_str)
        if entry is None:
            continue
        key = game_key(entry)
        if key not in existing_keys:
            new_entries.append(entry)
            existing_keys.add(key)
            print(f"  📍 오늘 경기 추가: {entry['away']} @ {entry['home']} → {entry['pick']} ({'✅' if entry['correct'] else '❌'})")
        else:
            # 기존 엔트리 업데이트 (결과가 바뀐 경우)
            for i, eg in enumerate(existing["games"]):
                if game_key(eg) == key:
                    if eg.get("actual_winner") != entry.get("actual_winner") and entry.get("actual_winner"):
                        existing["games"][i] = entry
                        print(f"  🔄 결과 업데이트: {entry['away']} @ {entry['home']} → {entry['pick']} ({'✅' if entry['correct'] else '❌'})")
                    break

    if not new_entries:
        print("✅ 추가할 새 결과 없음 (오늘 진행 중 경기는 위에서 개별 업데이트)")
    else:
        existing["games"].extend(new_entries)
        existing["games"].sort(key=lambda g: g["date"])
        print(f"✅ {len(new_entries)}경기 추가됨")

    # 누적 통계 출력
    all_games = existing["games"]
    W = sum(1 for g in all_games if g["correct"])
    L = sum(1 for g in all_games if not g["correct"])
    pct = round(W / (W + L) * 100, 1) if (W + L) else 0
    print(f"📊 시즌 누적: {W}W-{L}L ({pct}%) | 총 {len(all_games)}경기")

    # 저장
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"💾 저장됨: {OUT_FILE}")


if __name__ == "__main__":
    update()
