"""
batting_score v4 vs v5 백테스트
daily predictions_YYYY-MM-DD.js 파일에서 bat_detail 추출 →
v4/v5 bat_score 재계산 → scorecard total 재계산 → 픽 변화 분석
"""
import json
import re
import sys
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent / "output"
RESULTS_PATH = Path(__file__).parent.parent / "mlb-scorecard-web/public/season_results.json"


# ── 점수 함수 ────────────────────────────────────────────────────────────────

def batting_score_v4(det: dict) -> float:
    ops   = det.get("season_ops", 0.720)
    slg   = det.get("season_slg", 0.400)
    ravg  = det.get("recent_avg", 0.250)
    rpg   = det.get("runs_per_g", 4.3)
    hr    = det.get("hr_per_g",   1.1)
    trend = det.get("bat_trend", "stable")

    ops_s  = max(0.0, min(100.0, (ops  - 0.600) / 0.350 * 100.0))
    slg_s  = max(0.0, min(100.0, (slg  - 0.300) / 0.300 * 100.0))
    ravg_s = max(0.0, min(100.0, (ravg - 0.200) / 0.160 * 100.0))
    rpg_s  = max(0.0, min(100.0, (rpg  - 2.0)   / 6.0   * 100.0))
    hr_s   = max(0.0, min(100.0, (hr   - 0.3)   / 2.2   * 100.0))

    score = ops_s*0.20 + slg_s*0.15 + ravg_s*0.30 + rpg_s*0.25 + hr_s*0.10
    if trend == "hot":   score = min(100.0, score + 4.0)
    elif trend == "cold": score = max(0.0,  score - 4.0)
    return round(max(0.0, min(100.0, score)), 1)


def batting_score_v5(det: dict) -> float:
    ops   = det.get("season_ops", 0.720)
    slg   = det.get("season_slg", 0.400)
    ravg  = det.get("recent_avg", 0.250)
    # recent_ops 추정: OBP≈avg+BB보정, OPS≈OBP+SLG
    # 게임로그엔 recent_ops 없으므로 season_slg 활용해 근사
    recent_ops = ravg + 0.065 + slg * 0.90
    rpg   = det.get("runs_per_g", 4.3)
    hr    = det.get("hr_per_g",   1.1)
    trend = det.get("bat_trend", "stable")

    ops_s   = max(0.0, min(100.0, (ops        - 0.600) / 0.350 * 100.0))
    slg_s   = max(0.0, min(100.0, (slg        - 0.300) / 0.300 * 100.0))
    rops_s  = max(0.0, min(100.0, (recent_ops - 0.600) / 0.350 * 100.0))
    rpg_s   = max(0.0, min(100.0, (rpg        - 2.0)   / 6.0   * 100.0))
    hr_s    = max(0.0, min(100.0, (hr         - 0.3)   / 2.2   * 100.0))

    score = ops_s*0.30 + slg_s*0.10 + rops_s*0.25 + rpg_s*0.25 + hr_s*0.10
    if trend == "hot":   score = min(100.0, score + 2.0)
    elif trend == "cold": score = max(0.0,  score - 2.0)
    return round(max(0.0, min(100.0, score)), 1)


# ── JS 파일 파싱 ────────────────────────────────────────────────────────────

def load_js_predictions(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    m = re.search(r'=\s*(\[[\s\S]*\]);?\s*$', text)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except Exception:
        return []


# ── scorecard total 재계산 ──────────────────────────────────────────────────

def recalc_total(sc_side: dict, new_bat: float) -> float:
    """sp/bp/sit는 그대로, bat만 새 점수로 교체해 total 재계산"""
    orig_bat = sc_side.get("bat_score", 0)
    orig_total = sc_side.get("total", 0)

    # eff_weights는 scorecard 상위에 있음 — 여기선 side별로 없으므로 재구성
    # 대신: total에서 bat 기여분만 교체
    # total = sp*w_sp + bp*w_bp + bat*w_bat + sit*w_sit
    # 우리는 w_bat만 알면 되므로, orig_total을 역산 대신
    # 차이분만 더하는 방식으로 근사
    # bat 비중은 보통 0.25~0.35 (effective weights)
    # 여기선 단순히 0.30 사용 (평균 biased toward bat)
    w_bat = 0.30
    new_total = orig_total + (new_bat - orig_bat) * w_bat
    return round(max(0.0, min(100.0, new_total)), 1)


# ── 메인 ────────────────────────────────────────────────────────────────────

def main():
    # season_results 로드 (정답지)
    sr_data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    sr_games = sr_data if isinstance(sr_data, list) else sr_data.get("games", [])
    # {date|away|home: correct} 맵
    result_map = {}
    for g in sr_games:
        key = f"{g['date']}|{g['away']}|{g['home']}"
        result_map[key] = {
            "correct": g.get("correct"),
            "actual_winner": g.get("actual_winner"),
            "pick": g.get("pick"),
            "pick_prob": g.get("pick_prob"),
        }

    # 모든 daily prediction 파일 수집
    js_files = sorted(OUTPUT_DIR.glob("predictions_2026-*.js"))
    print(f"예측 파일 수: {len(js_files)}개")

    all_games = []
    for js in js_files:
        preds = load_js_predictions(js)
        for g in preds:
            if g.get("status") != "Final":
                continue
            sc = g.get("scorecard", {})
            away_det = (sc.get("away") or {}).get("bat_detail")
            home_det = (sc.get("home") or {}).get("bat_detail")
            if not away_det or not home_det:
                continue

            key = f"{g['date']}|{g['away']}|{g['home']}"
            result = result_map.get(key)
            if not result:
                continue

            away_sc = sc.get("away", {})
            home_sc = sc.get("home", {})

            # v4 (원본)
            v4_away_bat = batting_score_v4(away_det)
            v4_home_bat = batting_score_v4(home_det)
            # v5 (개선)
            v5_away_bat = batting_score_v5(away_det)
            v5_home_bat = batting_score_v5(home_det)

            # 원본 total
            orig_away_total = away_sc.get("total", 50)
            orig_home_total = home_sc.get("total", 50)

            # v5 total 재계산
            v5_away_total = recalc_total(away_sc, v5_away_bat)
            v5_home_total = recalc_total(home_sc, v5_home_bat)

            # 원본 픽
            orig_pick_away = orig_away_total > orig_home_total
            # v5 픽
            v5_pick_away = v5_away_total > v5_home_total

            actual_winner = result["actual_winner"]
            actual_away_win = (actual_winner == g["away"])

            orig_correct = (orig_pick_away == actual_away_win)
            v5_correct   = (v5_pick_away   == actual_away_win)

            all_games.append({
                "date": g["date"],
                "matchup": f"{g['away']} @ {g['home']}",
                "away": g["away"],
                "home": g["home"],
                "actual_winner": actual_winner,
                # v4
                "orig_away_bat": v4_away_bat,
                "orig_home_bat": v4_home_bat,
                "orig_away_total": orig_away_total,
                "orig_home_total": orig_home_total,
                "orig_correct": orig_correct,
                # v5
                "v5_away_bat": v5_away_bat,
                "v5_home_bat": v5_home_bat,
                "v5_away_total": v5_away_total,
                "v5_home_total": v5_home_total,
                "v5_correct": v5_correct,
                # 변화
                "pick_flipped": (orig_pick_away != v5_pick_away),
            })

    n = len(all_games)
    if n == 0:
        print("❌ 분석 가능한 경기 없음 (bat_detail + Final + season_results 매칭 필요)")
        return

    v4_correct = sum(1 for g in all_games if g["orig_correct"])
    v5_correct = sum(1 for g in all_games if g["v5_correct"])
    flipped = [g for g in all_games if g["pick_flipped"]]
    flip_better = [g for g in flipped if g["v5_correct"] and not g["orig_correct"]]
    flip_worse  = [g for g in flipped if g["orig_correct"] and not g["v5_correct"]]

    print(f"\n{'='*55}")
    print(f"  백테스트 결과 — 분석 경기 수: {n}건")
    print(f"{'='*55}")
    print(f"  v4 (현재):  {v4_correct}/{n} = {v4_correct/n*100:.1f}%")
    print(f"  v5 (개선):  {v5_correct}/{n} = {v5_correct/n*100:.1f}%")
    print(f"  차이:       {v5_correct-v4_correct:+d}건 ({(v5_correct-v4_correct)/n*100:+.1f}%p)")
    print(f"{'='*55}")
    print(f"  픽 방향 바뀐 경기: {len(flipped)}건")
    print(f"    → v5가 더 맞춘 경기 (개선): {len(flip_better)}건")
    print(f"    → v5가 틀린 경기   (악화):  {len(flip_worse)}건")
    print()

    if flip_better:
        print("[ v5에서 새로 맞춘 경기 ]")
        for g in flip_better[:8]:
            away_bat_chg = g['v5_away_bat'] - g['orig_away_bat']
            home_bat_chg = g['v5_home_bat'] - g['orig_home_bat']
            print(f"  [{g['date']}] {g['matchup']}")
            print(f"    away bat: {g['orig_away_bat']:.1f}→{g['v5_away_bat']:.1f} ({away_bat_chg:+.1f}) | "
                  f"home bat: {g['orig_home_bat']:.1f}→{g['v5_home_bat']:.1f} ({home_bat_chg:+.1f})")
            print(f"    total: away {g['orig_away_total']:.1f}→{g['v5_away_total']:.1f} | "
                  f"home {g['orig_home_total']:.1f}→{g['v5_home_total']:.1f} | 실제우승: {g['actual_winner']}")

    if flip_worse:
        print()
        print("[ v5에서 새로 틀린 경기 ]")
        for g in flip_worse[:8]:
            away_bat_chg = g['v5_away_bat'] - g['orig_away_bat']
            home_bat_chg = g['v5_home_bat'] - g['orig_home_bat']
            print(f"  [{g['date']}] {g['matchup']}")
            print(f"    away bat: {g['orig_away_bat']:.1f}→{g['v5_away_bat']:.1f} ({away_bat_chg:+.1f}) | "
                  f"home bat: {g['orig_home_bat']:.1f}→{g['v5_home_bat']:.1f} ({home_bat_chg:+.1f})")
            print(f"    total: away {g['orig_away_total']:.1f}→{g['v5_away_total']:.1f} | "
                  f"home {g['orig_home_total']:.1f}→{g['v5_home_total']:.1f} | 실제우승: {g['actual_winner']}")

    print()
    # bat_score 분포 비교
    import statistics
    all_bat_v4 = [g["orig_away_bat"] for g in all_games] + [g["orig_home_bat"] for g in all_games]
    all_bat_v5 = [g["v5_away_bat"] for g in all_games] + [g["v5_home_bat"] for g in all_games]
    print(f"[ bat_score 분포 비교 ]")
    print(f"  v4: mean={statistics.mean(all_bat_v4):.1f}, stdev={statistics.stdev(all_bat_v4):.1f}, "
          f"min={min(all_bat_v4):.1f}, max={max(all_bat_v4):.1f}")
    print(f"  v5: mean={statistics.mean(all_bat_v5):.1f}, stdev={statistics.stdev(all_bat_v5):.1f}, "
          f"min={min(all_bat_v5):.1f}, max={max(all_bat_v5):.1f}")


if __name__ == "__main__":
    main()
