"""
백테스트: 직전 1경기 ERA vs 최근 5경기 ERA 블렌딩 비율 최적화
- last_start_era * α + last5_era * (1-α) 조합
- α = 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3 비교
- 기준: v6 (α=1.0) 대비 정확도 변화
"""
import json, sys, re
from glob import glob
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
OUTPUT = BASE.parent / "output"

from pitcher_recent_score import analyze_pitcher_recent, pitcher_score as _orig_pitcher_score


def adapt_gamelog(gl_entries: list) -> list:
    adapted = []
    for e in gl_entries:
        adapted.append({
            "inningsPitched": str(e.get("ip", "0")),
            "earnedRuns":     e.get("er", 0),
            "hits":           e.get("h", 0),
            "baseOnBalls":    e.get("bb", 0),
            "strikeOuts":     e.get("so", 0),
            "_game_date":     e.get("date", ""),
        })
    adapted.sort(key=lambda x: x["_game_date"])
    return adapted


def graduated_correction(era: float) -> float:
    """v6 graduated correction 테이블"""
    if era is None:
        return 0.0
    if era == 0.0:
        return +4.0
    elif era <= 1.50:
        return +3.0
    elif era <= 3.00:
        return +1.5
    elif era <= 4.50:
        return 0.0
    elif era <= 6.00:
        return -2.0
    elif era <= 9.00:
        return -7.0
    else:
        return -10.0


def pitcher_score_blended(stats: dict, alpha: float,
                           season_era=None, season_wins=None, season_losses=None) -> float:
    """
    alpha: 직전 1경기 ERA 가중치 (0~1)
    1-alpha: 최근 5경기 ERA 가중치
    """
    # stats에서 v6 correction을 제거한 베이스 점수 먼저 계산
    # → recent_avg_era를 None으로 세팅해서 graduated_correction 무력화
    stats_no_correction = dict(stats)
    stats_no_correction["recent_avg_era"] = None
    stats_no_correction["last_start_era"] = None

    base_score = _orig_pitcher_score(stats_no_correction,
                                      season_era=season_era,
                                      season_wins=season_wins,
                                      season_losses=season_losses)

    # 블렌딩된 ERA로 graduated correction 적용
    last1 = stats.get("last_start_era")
    last5 = stats.get("last3_era")  # last3_era 필드가 실제로 last5 ERA를 담음

    if last1 is None:
        blended_era = last5
    elif last5 is None:
        blended_era = last1
    else:
        blended_era = round(alpha * last1 + (1 - alpha) * last5, 2)

    correction = graduated_correction(blended_era)
    score = base_score + correction
    score = max(30.0, min(72.0, score))
    return round(score, 1)


def win_prob_from_delta(v6_away_total, v6_home_total,
                        delta_away_sp, delta_home_sp, sp_w):
    """SP 점수 변동분을 총점에 반영 → 승률 재계산"""
    new_away = v6_away_total + delta_away_sp * sp_w
    new_home = v6_home_total + delta_home_sp * sp_w
    diff = new_away - new_home
    raw = 50.0 + diff * 0.5
    raw = max(30.0, min(70.0, raw))
    return round(raw, 1), round(100 - raw, 1)


def load_js(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    data = json.loads(raw.split("=", 1)[1].strip().rstrip(";"))
    return data if isinstance(data, list) else data.get("games", [])


# ── 파일 로드 ─────────────────────────────────────────────────────
files = sorted(glob(str(OUTPUT / "predictions_2026-08-*.js")))
files = [f for f in files if "2026-08-25" not in f]

print(f"백테스트 대상: {len(files)}일 ({Path(files[0]).stem[-10:]} ~ {Path(files[-1]).stem[-10:]})\n")

# 테스트할 alpha 값들
alphas = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]
results_by_alpha = {a: {"correct": 0, "total": 0, "changed": 0, "improved": 0, "worsened": 0} for a in alphas}

# 게임별 결과 누적
game_results = []

for fpath in files:
    games = load_js(fpath)
    date_str = re.search(r"(\d{4}-\d{2}-\d{2})", fpath).group(1)

    for g in games:
        actual = g.get("actual_winner")
        if not actual:
            continue

        away = g.get("away", "?")
        home = g.get("home", "?")
        sc = g.get("scorecard", {})
        ew = sc.get("eff_weights", {"sp": 0.30})
        sp_w = ew.get("sp", 0.30)

        away_sc = sc.get("away", {})
        home_sc = sc.get("home", {})
        v6_away_sp = away_sc.get("sp_score", 45.0)
        v6_home_sp = home_sc.get("sp_score", 45.0)
        v6_away_total = away_sc.get("total", 0) or 0
        v6_home_total = home_sc.get("total", 0) or 0
        v6_winner = g.get("model_winner", "?")
        v6_wp = g.get("win_prob", {})
        v6_away_pct = v6_wp.get("away", 50)

        # 투수 gamelog → stats 계산
        away_gl = g.get("away_pitcher_gamelog", [])
        home_gl = g.get("home_pitcher_gamelog", [])
        away_ps = g.get("away_pitcher_stats", {})
        home_ps = g.get("home_pitcher_stats", {})

        away_season_era = float(away_ps.get("era", 4.5) or 4.5)
        away_season_wins = away_ps.get("wins")
        away_season_losses = away_ps.get("losses")
        home_season_era = float(home_ps.get("era", 4.5) or 4.5)
        home_season_wins = home_ps.get("wins")
        home_season_losses = home_ps.get("losses")

        if away_gl:
            away_stats = analyze_pitcher_recent(adapt_gamelog(away_gl), n=10, today_str=date_str)
        else:
            away_stats = {}

        if home_gl:
            home_stats = analyze_pitcher_recent(adapt_gamelog(home_gl), n=10, today_str=date_str)
        else:
            home_stats = {}

        # v6 correct 여부
        v6_correct = (v6_winner == actual)

        # alpha별 점수 계산
        alpha_data = {}
        for alpha in alphas:
            if away_stats:
                new_away_sp = pitcher_score_blended(away_stats, alpha,
                                                    away_season_era, away_season_wins, away_season_losses)
            else:
                new_away_sp = v6_away_sp

            if home_stats:
                new_home_sp = pitcher_score_blended(home_stats, alpha,
                                                    home_season_era, home_season_wins, home_season_losses)
            else:
                new_home_sp = v6_home_sp

            delta_away = new_away_sp - v6_away_sp
            delta_home = new_home_sp - v6_home_sp

            new_away_pct, new_home_pct = win_prob_from_delta(
                v6_away_total, v6_home_total, delta_away, delta_home, sp_w)

            new_winner = away if new_away_pct > new_home_pct else home
            new_correct = (new_winner == actual)
            pick_changed = (new_winner != v6_winner)

            r = results_by_alpha[alpha]
            r["total"] += 1
            if new_correct:
                r["correct"] += 1
            if pick_changed:
                r["changed"] += 1
                if new_correct and not v6_correct:
                    r["improved"] += 1
                elif not new_correct and v6_correct:
                    r["worsened"] += 1

            alpha_data[alpha] = {"winner": new_winner, "correct": new_correct,
                                  "away_pct": new_away_pct}

        game_results.append({
            "date": date_str, "away": away, "home": home, "actual": actual,
            "v6_winner": v6_winner, "v6_correct": v6_correct,
            "alpha_data": alpha_data,
        })

# ── 결과 출력 ──────────────────────────────────────────────────────
print("=" * 75)
print(f"  {'alpha':>6}  {'정확도':>12}  {'v6대비':>8}  {'픽변경':>6}  {'개선':>5}  {'악화':>5}")
print("=" * 75)

best_alpha = 1.0
best_acc = 0
for alpha in alphas:
    r = results_by_alpha[alpha]
    t = r["total"]
    acc = r["correct"] / t * 100 if t else 0
    # v6 기준
    v6_acc = results_by_alpha[1.0]["correct"] / results_by_alpha[1.0]["total"] * 100
    diff = acc - v6_acc
    label = "← 기준(v6)" if alpha == 1.0 else ("★ BEST" if acc > best_acc and alpha != 1.0 else "")
    if alpha != 1.0 and acc > best_acc:
        best_acc = acc
        best_alpha = alpha
    print(f"  {alpha:>5.1f}   {r['correct']:>4}/{t}={acc:>5.1f}%  {diff:>+7.1f}%p  "
          f"{r['changed']:>5}경기  {r['improved']:>4}↑  {r['worsened']:>4}↓  {label}")

print("=" * 75)
print(f"\n  최적 alpha = {best_alpha}  (직전1경기 {best_alpha*100:.0f}% + 최근5경기 {(1-best_alpha)*100:.0f}%)")

# 최적 alpha에서 픽이 바뀐 경기 상세
if best_alpha != 1.0:
    print(f"\n[ alpha={best_alpha} 에서 픽이 바뀐 경기 중 개선된 케이스 ]")
    count = 0
    for gr in game_results:
        ad = gr["alpha_data"][best_alpha]
        if ad["winner"] != gr["v6_winner"] and ad["correct"] and not gr["v6_correct"]:
            count += 1
            if count <= 10:
                print(f"  {gr['date']} {gr['away']} @ {gr['home']}")
                print(f"    v6: {gr['v6_winner']}  →  best: {ad['winner']}  |  실제: {gr['actual']} ✅")
PYEOF