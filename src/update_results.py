"""
과거 예측 파일에 실제 경기 결과를 업데이트
output/predictions_YYYY-MM-DD.js 파일들을 스캔하여
actual_winner 가 없는 날짜의 결과를 MLB API에서 가져와 갱신
"""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OUTPUT_DIR = Path(__file__).parent.parent / "output"


def _load_js(path: Path):
    content = path.read_text(encoding="utf-8")
    m = re.search(r'window\.PREDICTIONS_DATA\s*=\s*(\[.*?\]);', content, re.DOTALL)
    if not m:
        return None
    return json.loads(m.group(1))


def _save_js(path: Path, data: list, game_date: str):
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(
        f"// Auto-generated (scorecard) — {game_date}\nwindow.PREDICTIONS_DATA = {payload};\n",
        encoding="utf-8"
    )


def update_date(game_date: str, verbose: bool = True) -> bool:
    """특정 날짜 결과 업데이트. 변경이 있으면 True 반환"""
    js_path = OUTPUT_DIR / f"predictions_{game_date}.js"
    if not js_path.exists():
        return False

    data = _load_js(js_path)
    if not data:
        return False

    # 결과 없는 게임이 있는지 확인 (actual_winner가 None인 경우만 — 0:0 스코어와 구분)
    missing = [g for g in data if g.get("actual_winner") is None]
    if not missing:
        return False



    # MLB API에서 실제 결과 가져오기
    from mlb_schedule import get_games
    try:
        games = get_games(game_date)
    except Exception as e:
        if verbose:
            print(f"  [{game_date}] API 오류: {e}")
        return False

    # game_pk 기준으로 매핑
    result_map = {g["gamePk"]: g for g in games}

    updated = 0
    for pred in data:
        if pred.get("actual_winner") is not None:
            continue

        game_pk = pred.get("game_pk")
        if not game_pk or game_pk not in result_map:
            # game_pk 없는 경우 팀 이름으로 매칭
            away_name = pred.get("away", "")
            home_name = pred.get("home", "")
            matched = next(
                (g for g in games
                 if g["away_name"] == away_name and g["home_name"] == home_name),
                None
            )
        else:
            matched = result_map[game_pk]

        if not matched:
            continue

        actual_away   = matched.get("actual_away")
        actual_home   = matched.get("actual_home")
        actual_winner = matched.get("actual_winner")

        if actual_winner is None:
            continue  # 아직 경기 안 끝남

        # actual_score 구조체 업데이트 (JS 템플릿 호환)
        pred["actual_score"] = {"away": actual_away, "home": actual_home}
        pred["actual_winner"] = actual_winner

        # model_correct 재계산 (win_prob 구조체 우선, 없으면 flat 필드 fallback)
        win_prob = pred.get("win_prob", {})
        home_pct = win_prob.get("home") if win_prob else pred.get("home_win_pct", 50)
        away_pct = win_prob.get("away") if win_prob else pred.get("away_win_pct", 50)
        if home_pct is None: home_pct = 50
        if away_pct is None: away_pct = 50
        model_winner = pred["home"] if home_pct >= away_pct else pred["away"]
        pred["model_correct"] = (model_winner == actual_winner)
        updated += 1

    if updated > 0:
        _save_js(js_path, data, game_date)
        if verbose:
            total    = len(data)
            complete = sum(1 for g in data if g.get("actual_winner") is not None)
            hits     = sum(1 for g in data if g.get("model_correct") is True)
            pct      = round(hits / complete * 100, 1) if complete else 0
            print(f"  [{game_date}] 업데이트 {updated}경기 → 완료 {complete}/{total} · 적중률 {pct}%")
        return True
    return False


def update_all_pending(days_back: int = 30, verbose: bool = True) -> int:
    """
    오늘부터 days_back일 전까지 결과가 없는 날짜 파일을 모두 업데이트
    반환값: 업데이트된 파일 수
    """
    today = date.today()
    count = 0
    for js_path in sorted(OUTPUT_DIR.glob("predictions_????-*.js")):
        m = re.search(r'predictions_(\d{4}-\d{2}-\d{2})\.js', js_path.name)
        if not m:
            continue
        d_str = m.group(1)
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        # 오늘 경기는 진행 중일 수 있으니 어제까지만
        if d >= today:
            continue
        if (today - d).days > days_back:
            continue
        if update_date(d_str, verbose=verbose):
            count += 1
    return count


if __name__ == "__main__":
    print("📊 과거 경기 결과 업데이트 중...")
    n = update_all_pending(days_back=60, verbose=True)
    if n == 0:
        print("✅ 업데이트할 결과 없음 (이미 최신)")
    else:
        print(f"\n✅ 총 {n}개 날짜 업데이트 완료")
