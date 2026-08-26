"""
백테스트: pitcher_recent_score v6 vs v7
- 기존 predictions_2026-08-XX.js 파일에 저장된 투수 gamelog 재활용
- v7 (이동 평균 ERA) 로 sp_score 재계산 → 픽 변화 및 정확도 비교
"""
import json, sys, os, re
from glob import glob
from pathlib import Path

# 경로 설정
BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
OUTPUT = BASE.parent / "output"

from pitcher_recent_score import analyze_pitcher_recent, pitcher_score


def adapt_gamelog(gl_entries: list) -> list:
    """
    .js 파일의 gamelog 형식 → analyze_pitcher_recent 입력 형식으로 변환
    {date, ip, er, h, bb, so} → {inningsPitched, earnedRuns, hits, baseOnBalls, strikeOuts, _game_date}
    .js 파일 정렬 방향에 따라 reverse 필요 시 처리
    """
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
    # 날짜 오름차순(오래된 순)으로 정렬 — analyze_pitcher_recent 기대 형식
    adapted.sort(key=lambda x: x["_game_date"])
    return adapted


def recompute_sp_score(gamelog: list, sp_detail_v6: dict, game_date: str,
                       season_era=None, season_wins=None, season_losses=None) -> tuple:
    """
    v7 pitcher score 재계산.
    반환: (v7_score, v7_recent_avg_era, v7_last_start_era)
    """
    if not gamelog:
        return sp_detail_v6.get("sp_score", 45.0), None, None

    adapted = adapt_gamelog(gamelog)
    stats = analyze_pitcher_recent(adapted, n=10, today_str=game_date)
    score = pitcher_score(stats, season_era=season_era,
                          season_wins=season_wins, season_losses=season_losses)
    return score, stats.get("recent_avg_era"), stats.get("last_start_era")


def load_js(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    data = json.loads(raw.split("=", 1)[1].strip().rstrip(";"))
    return data if isinstance(data, list) else data.get("games", [])


def win_prob_from_totals(away_total: float, home_total: float) -> tuple:
    """scorecard total → 승률 계산 (파이프라인과 동일 방식)"""
    diff = away_total - home_total
    # logistic 변환 (파이프라인 기준: diff 10pt ≈ 5%p 차이)
    import math
    raw = 50.0 + diff * 0.5
    raw = max(30.0, min(70.0, raw))
    return round(raw, 1), round(100 - raw, 1)


# ── 날짜 범위: 8/7 ~ 8/24 (v7 적용 전) ──────────────────────────────
files = sorted(glob(str(OUTPUT / "predictions_2026-08-*.js")))
# 8/25는 오늘 v7로 이미 돌린 날이므로 제외
files = [f for f in files if "2026-08-25" not in f]

print(f"백테스트 대상: {len(files)}일 ({Path(files[0]).stem} ~ {Path(files[-1]).stem})")
print()

results = []
total_v6_correct = total_v7_correct = 0
total_games = 0
changed_picks = []
high_conf_v6 = high_conf_v7 = high_conf_total = 0

for fpath in files:
    games = load_js(fpath)
    date_str = re.search(r"(\d{4}-\d{2}-\d{2})", fpath).group(1)

    for g in games:
        actual = g.get("actual_winner")
        if not actual:
            continue  # 결과 없음 스킵

        away = g.get("away", "?")
        home = g.get("home", "?")
        sc = g.get("scorecard", {})
        ew = sc.get("eff_weights", {"sp": 0.30, "bp": 0.25, "bat": 0.30, "sit": 0.15})
        sp_w = ew.get("sp", 0.30)

        # ── v6 데이터 ──
        away_sc = sc.get("away", {})
        home_sc = sc.get("home", {})
        v6_away_sp = away_sc.get("sp_score", 45.0)
        v6_home_sp = home_sc.get("sp_score", 45.0)
        v6_away_total = away_sc.get("total", 0)
        v6_home_total = home_sc.get("total", 0)
        v6_winner = g.get("model_winner", "?")
        v6_wp = g.get("win_prob", {})
        v6_away_pct = v6_wp.get("away", 50)
        v6_home_pct = v6_wp.get("home", 50)

        # ── v7 SP 점수 재계산 ──
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

        v7_away_sp, v7_away_avg_era, v7_away_last = recompute_sp_score(
            away_gl, away_sc, date_str, away_season_era, away_season_wins, away_season_losses)
        v7_home_sp, v7_home_avg_era, v7_home_last = recompute_sp_score(
            home_gl, home_sc, date_str, home_season_era, home_season_wins, home_season_losses)

        # ── v7 총점 조정: SP 변동분만 반영 ──
        away_sp_delta = v7_away_sp - v6_away_sp
        home_sp_delta = v7_home_sp - v6_home_sp
        v7_away_total = v6_away_total + away_sp_delta * sp_w
        v7_home_total = v6_home_total + home_sp_delta * sp_w

        # ── v7 승률 재계산 ──
        v7_away_pct, v7_home_pct = win_prob_from_totals(v7_away_total, v7_home_total)

        # v7 픽
        if v7_away_pct > v7_home_pct:
            v7_winner = away
            v7_pick_pct = v7_away_pct
        else:
            v7_winner = home
            v7_pick_pct = v7_home_pct

        v6_pick_pct = v6_away_pct if v6_winner == away else v6_home_pct

        # ── 정확도 집계 ──
        v6_correct = (v6_winner == actual)
        v7_correct = (v7_winner == actual)
        total_games += 1
        if v6_correct: total_v6_correct += 1
        if v7_correct: total_v7_correct += 1

        # High confidence (60%+)
        is_high_v6 = v6_pick_pct >= 60
        is_high_v7 = v7_pick_pct >= 60
        if is_high_v6:
            high_conf_total += 1
            if v6_correct: high_conf_v6 += 1
        if is_high_v7 and v7_correct:
            high_conf_v7 += 1

        # 픽 변경 여부
        pick_changed = (v6_winner != v7_winner)
        if pick_changed:
            impact = ("✅ 개선" if (not v6_correct and v7_correct)
                      else ("❌ 악화" if (v6_correct and not v7_correct)
                            else "동일결과"))
            changed_picks.append({
                "date": date_str, "matchup": f"{away} @ {home}",
                "v6_pick": f"{v6_winner} {v6_pick_pct:.0f}%",
                "v7_pick": f"{v7_winner} {v7_pick_pct:.0f}%",
                "actual": actual, "impact": impact,
                "away_era_diff": f"{away_sp_delta:+.1f}pt (last1={v7_away_last}, avg3={v7_away_avg_era})",
                "home_era_diff": f"{home_sp_delta:+.1f}pt (last1={v7_home_last}, avg3={v7_home_avg_era})",
            })

        results.append({
            "date": date_str, "away": away, "home": home,
            "actual": actual,
            "v6_winner": v6_winner, "v6_pct": v6_pick_pct, "v6_ok": v6_correct,
            "v7_winner": v7_winner, "v7_pct": v7_pick_pct, "v7_ok": v7_correct,
            "pick_changed": pick_changed,
        })

# ── 결과 출력 ──────────────────────────────────────────────────────
print("=" * 70)
print("  백테스트 결과 요약")
print("=" * 70)
print(f"  대상 경기: {total_games}경기  ({len(files)}일)")
print(f"  v6 정확도: {total_v6_correct}/{total_games} = {total_v6_correct/total_games*100:.1f}%")
print(f"  v7 정확도: {total_v7_correct}/{total_games} = {total_v7_correct/total_games*100:.1f}%")
diff = (total_v7_correct - total_v6_correct) / total_games * 100
print(f"  변화:      {diff:+.1f}%p  ({total_v7_correct - total_v6_correct:+d}경기)")
print()
print(f"  픽 변경:   {len(changed_picks)}경기")
if changed_picks:
    improved = sum(1 for c in changed_picks if c['impact'] == '✅ 개선')
    worsened = sum(1 for c in changed_picks if c['impact'] == '❌ 악화')
    same     = len(changed_picks) - improved - worsened
    print(f"    개선: {improved}  |  악화: {worsened}  |  결과동일: {same}")
print("=" * 70)

if changed_picks:
    print()
    print("[ 픽이 바뀐 경기 상세 ]")
    for c in changed_picks:
        print(f"  {c['date']} {c['matchup']}")
        print(f"    v6: {c['v6_pick']}  →  v7: {c['v7_pick']}  |  실제: {c['actual']}  {c['impact']}")
        print(f"    원정SP: {c['away_era_diff']}")
        print(f"    홈SP:   {c['home_era_diff']}")

# 날짜별 정확도
print()
print("[ 날짜별 정확도 ]")
from collections import defaultdict
by_date_v6 = defaultdict(lambda: [0,0])
by_date_v7 = defaultdict(lambda: [0,0])
for r in results:
    by_date_v6[r['date']][1] += 1
    by_date_v7[r['date']][1] += 1
    if r['v6_ok']: by_date_v6[r['date']][0] += 1
    if r['v7_ok']: by_date_v7[r['date']][0] += 1

print(f"  {'날짜':<12} {'v6':>10} {'v7':>10} {'변화':>8}")
for d in sorted(by_date_v6.keys()):
    c6, t6 = by_date_v6[d]
    c7, t7 = by_date_v7[d]
    chg = c7 - c6
    chg_str = f"{chg:+d}" if chg != 0 else "-"
    print(f"  {d:<12} {c6}/{t6}={c6/t6*100:4.0f}%  {c7}/{t7}={c7/t7*100:4.0f}%  {chg_str:>6}")
