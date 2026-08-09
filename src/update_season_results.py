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
import requests
from datetime import date, datetime
from pathlib import Path
import pytz

MLB_API = "https://statsapi.mlb.com/api/v1"

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
    """다음 시즌 시작일이 지났으면 리셋 (PST 기준)"""
    try:
        import pytz
        pst_today = datetime.now(pytz.timezone("America/Los_Angeles")).date()
        next_start = date.fromisoformat(NEXT_SEASON_START)
        return pst_today >= next_start and existing.get("season") != str(pst_today.year)
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

    sc = g.get("scorecard", {}) or {}
    sc_away = sc.get("away", {}) or {}
    sc_home = sc.get("home", {}) or {}

    away_sp_detail = sc_away.get("sp_detail", {}) or {}
    home_sp_detail = sc_home.get("sp_detail", {}) or {}
    away_bp_detail = sc_away.get("bp_detail", {}) or {}
    home_bp_detail = sc_home.get("bp_detail", {}) or {}

    return {
        "date":          date_str,
        "away":          g.get("away", ""),
        "home":          g.get("home", ""),
        "pick":          pick,
        "pick_prob":     pick_prob,
        "actual_winner": g.get("actual_winner", ""),
        "correct":       bool(g.get("model_correct")),
        "high_conf":     pick_prob >= 65.0,
        # SP / 불펜 데이터 (Cold SP 패턴 분석용)
        "away_sp_trend":  away_sp_detail.get("trend"),
        "home_sp_trend":  home_sp_detail.get("trend"),
        "away_sp_era":    away_sp_detail.get("era"),
        "home_sp_era":    home_sp_detail.get("era"),
        "away_bp_era":    away_bp_detail.get("bullpen_era"),
        "home_bp_era":    home_bp_detail.get("bullpen_era"),
        "away_sp_score":  sc_away.get("sp_score"),
        "home_sp_score":  sc_home.get("sp_score"),
        "away_bp_score":  sc_away.get("bp_score"),
        "home_bp_score":  sc_home.get("bp_score"),
        "bat_source":     sc.get("bat_source"),
    }


def load_predictions_json(target_date: str = None) -> list:
    """
    predictions.json 에서 경기 데이터 로드.
    우선순위: output/predictions.json (pipeline 직후) → 웹 레포 predictions.json
    target_date 가 지정되면 해당 날짜 게임만 필터링.
    """
    candidates = [
        OUTPUT_DIR / "predictions.json",           # pipeline이 방금 생성한 파일
        WEB_REPO / "public" / "predictions.json",  # 웹 레포 (복원된 파일)
    ]
    for pred_file in candidates:
        if not pred_file.exists():
            continue
        try:
            data = json.loads(pred_file.read_text(encoding="utf-8"))
            if not isinstance(data, list) or not data:
                continue
            if target_date:
                filtered = [g for g in data if g.get("date", "") == target_date]
                if filtered:
                    print(f"  📂 {pred_file.name}에서 {len(filtered)}경기 로드 ({target_date})")
                    return filtered
            else:
                print(f"  📂 {pred_file.name}에서 {len(data)}경기 로드")
                return data
        except Exception as e:
            print(f"⚠️ {pred_file.name} 로드 실패: {e}")
    return []


def fetch_actual_results_from_api(game_date: str) -> dict:
    """
    MLB Stats API에서 특정 날짜의 Final 경기 결과를 가져옴
    반환: { "TeamA|TeamB": actual_winner_name, ... }  (away|home 기준)
    """
    url = f"{MLB_API}/schedule"
    params = {"sportId": 1, "date": game_date}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"⚠️ MLB API 호출 실패: {e}")
        return {}

    results = {}
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            if g.get("gameType") != "R":
                continue
            status = g.get("status", {}).get("abstractGameState", "")
            if status != "Final":
                continue
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_name = away["team"]["name"]
            home_name  = home["team"]["name"]
            if away.get("isWinner"):
                winner = away_name
            elif home.get("isWinner"):
                winner = home_name
            else:
                continue
            key = f"{away_name}|{home_name}"
            results[key] = winner
    print(f"  🌐 MLB API → {len(results)}경기 Final 결과 확인 ({game_date})")
    return results


def update():
    existing = load_season_results()

    # 시즌 전환 감지
    if should_reset_season(existing):
        _pst_now = datetime.now(pytz.timezone("America/Los_Angeles"))
        print(f"🔄 새 시즌 감지 → season_results 리셋 ({SEASON_YEAR} → {_pst_now.year})")
        existing = {
            "season":     str(_pst_now.year),
            "start_date": NEXT_SEASON_START,
            "games":      [],
        }

    # 기존 게임 키 세트 (중복 방지)
    existing_keys = {game_key(g) for g in existing["games"]}
    new_entries   = []

    # output 폴더에서 시즌 시작일 이후 파일 스캔
    start = date.fromisoformat(existing["start_date"])
    # MLB 경기는 PST 기준 — GitHub Actions(UTC) 자정 이후 날짜 오류 방지
    pst = pytz.timezone("America/Los_Angeles")
    today = datetime.now(pst).date()
    today_str = today.isoformat()
    print(f"  📅 PST 기준 오늘: {today_str} (UTC: {datetime.utcnow().date().isoformat()})")

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

    # ── 누락된 과거 날짜 처리 (yesterday 이전까지) ──
    from datetime import timedelta
    existing_dates = {g["date"] for g in existing["games"]}
    check_date = start
    while check_date < today:
        date_str_check = check_date.isoformat()
        # 이 날짜 게임이 season_results에 하나도 없으면 처리
        if date_str_check not in existing_dates:
            print(f"  🔍 누락 날짜 발견: {date_str_check} — API + predictions 조회 중...")
            past_api = fetch_actual_results_from_api(date_str_check)
            past_preds = load_predictions_json(target_date=date_str_check)
            if past_api and past_preds:
                for g in past_preds:
                    away_name = g.get("away", "")
                    home_name  = g.get("home", "")
                    api_key = f"{away_name}|{home_name}"
                    if api_key not in past_api:
                        continue
                    actual_winner = past_api[api_key]
                    g = dict(g)
                    g["actual_winner"] = actual_winner
                    win_prob = g.get("win_prob", {})
                    away_pct = (win_prob.get("away", 50) or 50) if isinstance(win_prob, dict) else 50
                    home_pct = (win_prob.get("home", 50) or 50) if isinstance(win_prob, dict) else 50
                    pick = g.get("model_winner") or (home_name if home_pct >= away_pct else away_name)
                    g["model_correct"] = (pick == actual_winner)
                    entry = build_game_entry(g, date_str_check)
                    if entry and game_key(entry) not in existing_keys:
                        new_entries.append(entry)
                        existing_keys.add(game_key(entry))
                        print(f"    ✅ {entry['away']} @ {entry['home']} → {entry['pick']} ({'✅' if entry['correct'] else '❌'})")
        check_date += timedelta(days=1)

    # ── 오늘 경기: MLB API로 실제 결과 + predictions.json 병합 ──
    api_results = fetch_actual_results_from_api(today_str)  # { "Away|Home": winner }
    today_preds = load_predictions_json(target_date=today_str)
    if not today_preds:
        today_preds = load_predictions_json()  # 날짜 필터 없이 재시도

    today_from_json = [g for g in today_preds if g.get("date", "") == today_str] or today_preds

    for g in today_from_json:
        away_name = g.get("away", "")
        home_name  = g.get("home", "")
        api_key = f"{away_name}|{home_name}"

        # API 결과를 predictions 데이터에 병합
        if api_key in api_results:
            actual_winner = api_results[api_key]
            g = dict(g)  # 원본 변경 방지
            g["actual_winner"] = actual_winner

            # model_correct 계산
            win_prob = g.get("win_prob", {})
            if isinstance(win_prob, dict):
                away_pct = win_prob.get("away", 50) or 50
                home_pct = win_prob.get("home", 50) or 50
            else:
                away_pct = home_pct = 50
            pick = g.get("model_winner") or (home_name if home_pct >= away_pct else away_name)
            g["model_correct"] = (pick == actual_winner)

        if g.get("model_correct") is None:
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

    # ── predictions.json에도 actual_winner / model_correct 반영 ──
    # SeasonStats UI가 predictions.json의 model_correct를 직접 읽으므로
    # 오늘 경기 결과를 predictions.json에도 업데이트해야 "Today's Results"가 실시간 반영됨
    pred_web = WEB_REPO / "public" / "predictions.json"
    if pred_web.exists() and api_results:
        try:
            pred_data = json.loads(pred_web.read_text(encoding="utf-8"))
            updated_count = 0
            for g in pred_data:
                if g.get("date", "") != today_str:
                    continue
                api_key = f"{g.get('away','')}|{g.get('home','')}"
                if api_key not in api_results:
                    continue
                actual_winner = api_results[api_key]
                if g.get("actual_winner") == actual_winner:
                    continue  # 이미 최신
                g["actual_winner"] = actual_winner
                pick = g.get("model_winner") or ""
                g["model_correct"] = (pick == actual_winner) if pick else None
                # actual_score는 MLB API 점수 조회 없이 winner만 반영
                updated_count += 1
            if updated_count > 0:
                pred_web.write_text(json.dumps(pred_data, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"📝 predictions.json 결과 반영: {updated_count}경기 업데이트")
        except Exception as e:
            print(f"⚠️ predictions.json 업데이트 실패: {e}")


if __name__ == "__main__":
    update()
