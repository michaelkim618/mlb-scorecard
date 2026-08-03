"""
전체 예측 파이프라인: 스케줄 → 스탯 → ML/MC 예측 → Kalshi → Value Bet → JSON

승률 결정 방식 (블렌딩 모드):
  - ML 모델 + Monte Carlo 승률을 가중 평균 → 최종 승률 (weights.json blend 설정)
  - 예상 득점은 최종 승률에 Pythagorean 역산으로 조정 → 승률과 점수 방향 일치 보장
  - ML 모델 없으면 Monte Carlo 단독 사용
"""
import json
import math
import sys
from pathlib import Path
from datetime import date
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from mlb_schedule       import get_games
from mlb_stats_fetcher  import get_team_hitting_log, get_team_pitching_log, get_pitcher_season, get_standings_map
from offense_score      import calc_offense_score
from defense_score      import calc_defense_score
from simulator          import simulate
from kalshi_client      import get_kalshi_prob
from value_bet          import evaluate
from injury_check       import get_injury_notes
import ml_predictor

OUTPUT_DIR     = Path(__file__).parent.parent / "output"
CONFIG_PATH    = Path(__file__).parent.parent / "config" / "weights.json"
PENALTY_PATH   = Path(__file__).parent.parent / "config" / "pitcher_penalties.json"


def _load_pitcher_penalties() -> dict:
    """pitcher_penalties.json 로드 → {pitcher_id: {penalty, reason, pitcher_name}}"""
    try:
        data = json.loads(PENALTY_PATH.read_text(encoding="utf-8"))
        return {int(p["pitcher_id"]): p for p in data.get("pitchers", [])}
    except Exception:
        return {}


def _load_pitcher_watch_list() -> dict:
    """pitcher_penalties.json watch_list 로드 → {pitcher_id: {watch_reason, flag}}"""
    try:
        data = json.loads(PENALTY_PATH.read_text(encoding="utf-8"))
        return {int(p["pitcher_id"]): p for p in data.get("watch_list", [])}
    except Exception:
        return {}


def _load_blend_weights():
    """weights.json 에서 ML/MC 가중치 로드. 기본 65/35."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        b = cfg.get("blend", {})
        ml_w = float(b.get("ml_weight", 0.65))
        mc_w = float(b.get("mc_weight", 0.35))
        total = ml_w + mc_w
        return ml_w / total, mc_w / total   # 합이 1 이 되도록 정규화
    except Exception:
        return 0.65, 0.35


def _blend_and_adjust(ml_away: float, ml_home: float,
                      mc_away: float, mc_home: float,
                      mc_exp_away: float, mc_exp_home: float,
                      ml_w: float, mc_w: float) -> dict:
    """
    ML 승률 + MC 승률을 가중 평균 → 최종 승률.
    MC 예상 점수를 Pythagorean 역산으로 조정해 승률과 방향 일치 보장.

    Pythagorean 공식:  home_win% = H² / (H² + A²)
    역산:              adj = (A/H)^0.5 · (target/(1-target))^0.25
                       H_new = H · adj,  A_new = A / adj
    """
    # ── 1. 가중 평균 승률 ──────────────────────────────────────────
    blend_home = ml_w * ml_home + mc_w * mc_home
    blend_away = 100.0 - blend_home

    # ── 2. MC 예상 점수 → 목표 승률에 맞게 Pythagorean 조정 ────────
    A, H = mc_exp_away, mc_exp_home
    target = blend_home / 100.0          # 홈팀 목표 승률 (0~1)

    adj_exp_away, adj_exp_home = A, H    # fallback: 조정 불가 시 원본 유지

    if 0.01 < target < 0.99 and H > 0 and A > 0:
        # adj 계수: 홈 득점 × adj, 원정 득점 / adj
        adj = (A / H) ** 0.5 * (target / (1.0 - target)) ** 0.25
        adj_exp_home = round(H * adj, 1)
        adj_exp_away = round(A / adj, 1)

        # 음수 방지 & 최솟값 보정 (최소 0.5점)
        adj_exp_home = max(adj_exp_home, 0.5)
        adj_exp_away = max(adj_exp_away, 0.5)

    return {
        "away_win_pct":   round(blend_away, 1),
        "home_win_pct":   round(blend_home, 1),
        "expected_away":  adj_exp_away,
        "expected_home":  adj_exp_home,
        "blend_detail": {
            "ml_away": ml_away, "ml_home": ml_home,
            "mc_away": mc_away, "mc_home": mc_home,
            "ml_weight": ml_w,  "mc_weight": mc_w,
        },
    }


def _safe(fn, default=None, label=""):
    try:
        return fn()
    except Exception as e:
        print(f"  [경고] {label}: {e}")
        return default


def run(game_date: Optional[str] = None) -> list:
    if game_date is None:
        game_date = date.today().isoformat()

    use_ml = ml_predictor.is_available()

    print(f"\n{'='*60}")
    print(f"MLB 예측 파이프라인 실행: {game_date}")
    print(f"승률 예측 모드: {'🤖 ML 모델' if use_ml else '🎲 Monte Carlo'}")
    if use_ml:
        meta = ml_predictor.get_model_meta()
        print(f"  학습 경기: {meta.get('n_games')}경기 | CV 정확도: {meta.get('cv_accuracy', 0):.1%}")
    print(f"{'='*60}")

    standings_map = _safe(get_standings_map, {}, "순위표")

    games = get_games(game_date)
    if not games:
        print("경기 없음.")
        return []

    print(f"총 {len(games)}경기 처리 시작\n")
    results = []

    for i, g in enumerate(games, 1):
        away_id         = g["away_id"]
        home_id         = g["home_id"]
        away_name       = g["away_name"]
        home_name       = g["home_name"]
        away_pitcher_id = g.get("away_pitcher_id")
        home_pitcher_id = g.get("home_pitcher_id")

        print(f"[{i}/{len(games)}] {away_name} @ {home_name}")

        # ── 스탯 수집 (예상 득점 + 모델 점수 표시용) ────────────────
        away_hit_log   = _safe(lambda aid=away_id: get_team_hitting_log(aid,  10), [], "away 타격로그")
        home_hit_log   = _safe(lambda hid=home_id: get_team_hitting_log(hid,  10), [], "home 타격로그")
        away_pitch_log = _safe(lambda aid=away_id: get_team_pitching_log(aid, 10), [], "away 투구로그")
        home_pitch_log = _safe(lambda hid=home_id: get_team_pitching_log(hid, 10), [], "home 투구로그")

        away_off = _safe(lambda: calc_offense_score(away_id, away_hit_log), {"score": 50.0}, "away 공격점수")
        home_off = _safe(lambda: calc_offense_score(home_id, home_hit_log), {"score": 50.0}, "home 공격점수")
        away_def = _safe(lambda: calc_defense_score(away_id, away_pitcher_id, away_pitch_log), {"score": 50.0}, "away 수비점수")
        home_def = _safe(lambda: calc_defense_score(home_id, home_pitcher_id, home_pitch_log), {"score": 50.0}, "home 수비점수")

        # ── 예상 득점: Monte Carlo (항상) ───────────────────────────
        mc_raw = _safe(lambda: simulate(
            away_offense=away_off["score"], away_defense=away_def["score"],
            home_offense=home_off["score"], home_defense=home_def["score"],
        ), {"away_win_pct": 50.0, "home_win_pct": 50.0,
            "expected_away": 4.5, "expected_home": 4.5, "tie_pct": 0.0}, "Monte Carlo")

        # ── MC 타이 정규화 ─────────────────────────────────────────────
        # Poisson 시뮬레이션에서 동점(tie)이 10~15% 발생 → away+home이 100%가 안 됨
        # 블렌드 시 MC win%가 인위적으로 낮아져 ML 가중치가 왜곡되는 버그 수정
        # 해결: 타이를 50:50으로 분배해 away_win + home_win = 100%로 정규화
        _mc_a = mc_raw.get("away_win_pct", 50.0)
        _mc_h = mc_raw.get("home_win_pct", 50.0)
        _mc_tie = mc_raw.get("tie_pct", 0.0)
        _mc_total = _mc_a + _mc_h
        if _mc_total > 0 and _mc_total < 99.0:   # 타이가 1% 이상 → 정규화
            # 타이의 절반씩 각 팀에 귀속 (or 비례 배분)
            _mc_a_norm = round(_mc_a + _mc_tie / 2.0, 1)
            _mc_h_norm = round(_mc_h + _mc_tie / 2.0, 1)
            if abs(_mc_a_norm + _mc_h_norm - 100.0) > 0.5:   # 합 보정
                _mc_a_norm = round(_mc_a / _mc_total * 100.0, 1)
                _mc_h_norm = round(100.0 - _mc_a_norm, 1)
            mc = dict(mc_raw)
            mc["away_win_pct"] = _mc_a_norm
            mc["home_win_pct"] = _mc_h_norm
            print(f"    [MC 타이 정규화] tie {_mc_tie:.1f}% 분배 → "
                  f"{away_name}: {_mc_a:.1f}%→{_mc_a_norm:.1f}%  "
                  f"{home_name}: {_mc_h:.1f}%→{_mc_h_norm:.1f}%")
        else:
            mc = mc_raw

        # 투수 W/L/IP/ERA (ML 피처용 + 쿠어스 보정용)
        def _pitcher_info(pid):
            if not pid: return (None, None, None, None)  # wins, losses, ip, era
            s = _safe(lambda p=pid: get_pitcher_season(p), {}, "투수정보")
            w = s.get("wins")
            l = s.get("losses")
            ip_str = s.get("inningsPitched", "0")
            try:
                ip = float(ip_str)
            except Exception:
                ip = 0.0
            era_str = s.get("era", "")
            try:
                era = float(era_str) if era_str and era_str not in ("-.--", "∞") else 4.50
            except Exception:
                era = 4.50
            return w, l, ip, era
        away_p_wins, away_p_losses, away_p_ip, away_p_era = _pitcher_info(away_pitcher_id)
        home_p_wins, home_p_losses, home_p_ip, home_p_era = _pitcher_info(home_pitcher_id)

        # ── W+L=0 투수 ERA 패널티: 결정 없는 투수 → 리그 평균으로 강제 보정 ──
        # 시즌 결정(승+패=0)이면 아직 의미 있는 ERA 없음 → IP=0으로 강제해 4.50에 수렴
        def _penalize_era_if_no_decisions(wins, losses, ip, era):
            if wins is not None and losses is not None:
                if (wins + losses) == 0:
                    return 4.50  # 결정 없음 → 리그 평균 ERA
            return era

        away_p_era = _penalize_era_if_no_decisions(away_p_wins, away_p_losses, away_p_ip, away_p_era)
        home_p_era = _penalize_era_if_no_decisions(home_p_wins, home_p_losses, home_p_ip, home_p_era)

        if away_p_wins is not None and away_p_losses is not None and (away_p_wins + away_p_losses) == 0:
            print(f"    [W+L=0 패널티] {g.get('away_pitcher','?')} 결정 없음 → ERA 4.50 적용")
        if home_p_wins is not None and home_p_losses is not None and (home_p_wins + home_p_losses) == 0:
            print(f"    [W+L=0 패널티] {g.get('home_pitcher','?')} 결정 없음 → ERA 4.50 적용")

        # ── 승률: ML 우선, fallback → Monte Carlo ───────────────────
        if use_ml:
            away_rdpg = standings_map.get(away_id, {}).get("run_diff_per_game", 0.0)
            home_rdpg = standings_map.get(home_id, {}).get("run_diff_per_game", 0.0)
            away_streak = standings_map.get(away_id, {}).get("streak_wins", 0)
            home_streak = standings_map.get(home_id, {}).get("streak_wins", 0)
            ml = _safe(lambda: ml_predictor.predict(
                away_wins=g["away_wins"], away_losses=g["away_losses"],
                home_wins=g["home_wins"], home_losses=g["home_losses"],
                away_pitcher_id=away_pitcher_id,
                home_pitcher_id=home_pitcher_id,
                away_run_diff_per_game=away_rdpg,
                home_run_diff_per_game=home_rdpg,
                away_pitcher_wins=away_p_wins,
                away_pitcher_losses=away_p_losses,
                home_pitcher_wins=home_p_wins,
                home_pitcher_losses=home_p_losses,
                away_pitcher_ip=away_p_ip,
                home_pitcher_ip=home_p_ip,
                away_streak=away_streak,
                home_streak=home_streak,
            ), None, "ML 예측")
        else:
            ml = None

        if ml:
            ml_w, mc_w = _load_blend_weights()
            blended = _blend_and_adjust(
                ml_away=ml["away_win_pct"], ml_home=ml["home_win_pct"],
                mc_away=mc["away_win_pct"], mc_home=mc["home_win_pct"],
                mc_exp_away=mc["expected_away"], mc_exp_home=mc["expected_home"],
                ml_w=ml_w, mc_w=mc_w,
            )
            away_win_pct    = blended["away_win_pct"]
            home_win_pct    = blended["home_win_pct"]
            final_exp_away  = blended["expected_away"]
            final_exp_home  = blended["expected_home"]
            blend_detail    = blended["blend_detail"]

            # ── ML-MC 불일치 처리 (3단계 동적 조정) ──────────────────────────
            # 개선: 불일치 크기에 따라 처리 강도를 차등 적용
            #   갭 < 20%p  → 일반 블렌드 유지
            #   갭 20~34%p → 40% 중립 당김 (기존 25%에서 강화)
            #   갭 ≥ 35%p  → ML 가중치 우선 재블렌딩 (60%ML+40%MC)
            #                이유: MC가 극단적일 때(80%+) 오히려 ML이 더 정확했음
            #                (ARI@LAD: ML→ARI 3연속 적중, MC→LAD 3연속 오류)
            ml_mc_gap = abs(ml["home_win_pct"] - mc["home_win_pct"])
            ml_mc_conflict_level = None

            if ml_mc_gap >= 35.0:
                # 🔴 극심한 불일치 → ML 우선 재블렌딩
                ml_mc_conflict_level = "high"
                FLIP_ML_W, FLIP_MC_W = 0.60, 0.40
                reblended = _blend_and_adjust(
                    ml_away=ml["away_win_pct"], ml_home=ml["home_win_pct"],
                    mc_away=mc["away_win_pct"], mc_home=mc["home_win_pct"],
                    mc_exp_away=mc["expected_away"], mc_exp_home=mc["expected_home"],
                    ml_w=FLIP_ML_W, mc_w=FLIP_MC_W,
                )
                away_win_pct   = reblended["away_win_pct"]
                home_win_pct   = reblended["home_win_pct"]
                final_exp_away = reblended["expected_away"]
                final_exp_home = reblended["expected_home"]
                print(f"    [🔴 ML-MC 극심 불일치 {ml_mc_gap:.1f}%p] ML 우선 재블렌딩(ML60%/MC40%) → {away_name}: {away_win_pct}%  {home_name}: {home_win_pct}%")

            elif ml_mc_gap >= 20.0:
                # 🟡 중간 불일치 → 40% 중립 당김
                ml_mc_conflict_level = "medium"
                DAMPEN = 0.40
                home_win_pct = round(home_win_pct * (1 - DAMPEN) + 50.0 * DAMPEN, 1)
                away_win_pct = round(100.0 - home_win_pct, 1)
                print(f"    [🟡 ML-MC 불일치 {ml_mc_gap:.1f}%p] 중립 40% 당김 → {away_name}: {away_win_pct}%  {home_name}: {home_win_pct}%")

            # ── 모멘텀 보정 (연승/연패) ─────────────────────────────────────
            # 개선: 슬럼프(연패) 뿐 아니라 핫스트릭(연승)도 반영
            # 연패 팀: ML 시즌 누적 신뢰 낮춤 → MC(최근 10경기) 방향으로 30% 당김
            # 연승 팀: 최근 상승세 반영 → +3% 보너스 (MC와 무관하게)
            SLUMP_THRESHOLD  = -3    # 3연패 이상 → 슬럼프 보정
            HOT_THRESHOLD    =  3    # 3연승 이상 → 핫스트릭 보너스
            SLUMP_PULL       = 0.30  # 30%를 MC 방향으로
            HOT_BONUS        = 3.0   # 연승팀 +3%

            # 연패 슬럼프 보정 (조건 완화: ML vs MC 격차 무관하게 적용)
            if home_streak <= SLUMP_THRESHOLD:
                prev = home_win_pct
                home_win_pct = round(home_win_pct * (1 - SLUMP_PULL) + mc["home_win_pct"] * SLUMP_PULL, 1)
                away_win_pct = round(100.0 - home_win_pct, 1)
                print(f"    [슬럼프 보정] {home_name} {abs(home_streak)}연패 → MC 방향 조정 ({prev}%→{home_win_pct}%)")
            if away_streak <= SLUMP_THRESHOLD:
                prev = away_win_pct
                away_win_pct = round(away_win_pct * (1 - SLUMP_PULL) + mc["away_win_pct"] * SLUMP_PULL, 1)
                home_win_pct = round(100.0 - away_win_pct, 1)
                print(f"    [슬럼프 보정] {away_name} {abs(away_streak)}연패 → MC 방향 조정 ({prev}%→{away_win_pct}%)")

            # 연승 핫스트릭 보너스 (슬럼프 보정과 중복 적용 안 함)
            if home_streak >= HOT_THRESHOLD and away_streak < HOT_THRESHOLD:
                home_win_pct = min(home_win_pct + HOT_BONUS, 95.0)
                away_win_pct = round(100.0 - home_win_pct, 1)
                print(f"    [핫스트릭] {home_name} {home_streak}연승 → +{HOT_BONUS}% 보너스 ({home_win_pct}%)")
            elif away_streak >= HOT_THRESHOLD and home_streak < HOT_THRESHOLD:
                away_win_pct = min(away_win_pct + HOT_BONUS, 95.0)
                home_win_pct = round(100.0 - away_win_pct, 1)
                print(f"    [핫스트릭] {away_name} {away_streak}연승 → +{HOT_BONUS}% 보너스 ({away_win_pct}%)")

            # ── ML 과신 제한 (ML 단독 예측이 62% 이상이면 shrinkage) ───────
            # ML이 너무 강하게 한쪽을 찍으면 과적합 가능성 → 57%쪽으로 당김
            # 개선: 발동 기준 65%→62%, shrink 목표 60%→57% (고신뢰 예측 더 적극적으로 억제)
            ml_max = max(ml["home_win_pct"], ml["away_win_pct"])
            if ml_max >= 62.0:
                SHRINK_TARGET = 57.0
                shrink_ratio  = 0.35  # 35%를 57% 방향으로 당김
                if ml["home_win_pct"] >= ml["away_win_pct"]:
                    home_win_pct = round(home_win_pct * (1 - shrink_ratio) + SHRINK_TARGET * shrink_ratio, 1)
                    away_win_pct = round(100.0 - home_win_pct, 1)
                else:
                    away_win_pct = round(away_win_pct * (1 - shrink_ratio) + SHRINK_TARGET * shrink_ratio, 1)
                    home_win_pct = round(100.0 - away_win_pct, 1)
                print(f"    [ML 과신 제한 {ml_max:.1f}%] shrinkage 적용 → {away_name}: {away_win_pct}%  {home_name}: {home_win_pct}%")

            pred_model      = "blend"
            print(f"    [ML {ml_w:.0%}] {away_name}: {ml['away_win_pct']}%  {home_name}: {ml['home_win_pct']}%")
            print(f"    [MC {mc_w:.0%}] {away_name}: {mc['away_win_pct']}%  {home_name}: {mc['home_win_pct']}%")
            print(f"    [최종 블렌드] {away_name}: {away_win_pct}%  {home_name}: {home_win_pct}%  "
                  f"→ 예상 {final_exp_away}-{final_exp_home}")
        else:
            away_win_pct   = mc["away_win_pct"]
            home_win_pct   = mc["home_win_pct"]
            final_exp_away = mc["expected_away"]
            final_exp_home = mc["expected_home"]
            blend_detail   = None
            pred_model     = "montecarlo"
            ml_mc_conflict_level = None
            print(f"    [MC] 승률 - {away_name}: {away_win_pct}%  {home_name}: {home_win_pct}%")

        # ── TBD 선발 패널티 ─────────────────────────────────────────────
        # 선발투수 미정(TBD) 또는 시즌 통계 없음 → 해당 팀 승률 12% 하향 조정
        # 단, 패널티 적용 후 상대 팀이 70% 초과하는 비상식적 결과는 캡(Cap)으로 제한
        TBD_PENALTY    = 12.0
        TBD_MAX_WIN_PCT = 70.0  # TBD 상황에서 상대팀 최대 승률 상한선

        # ▶ 스탯 없는 투수(ID는 있으나 W/L=None) → TBD 동급 처리
        away_no_stats = (away_pitcher_id is not None
                         and away_p_wins is None and away_p_losses is None)
        home_no_stats = (home_pitcher_id is not None
                         and home_p_wins is None and home_p_losses is None)

        away_is_tbd = (g.get("away_pitcher", "TBD") in ("TBD", "", None)
                       or away_pitcher_id is None
                       or away_no_stats)
        home_is_tbd = (g.get("home_pitcher", "TBD") in ("TBD", "", None)
                       or home_pitcher_id is None
                       or home_no_stats)

        if away_is_tbd and not home_is_tbd:
            label = "시즌 통계 없음" if away_no_stats else "선발 미정"
            print(f"    [TBD 패널티] {away_name} {label} → 승률 {TBD_PENALTY}% 하향")
            away_win_pct = max(away_win_pct - TBD_PENALTY, 5.0)
            home_win_pct = 100.0 - away_win_pct
            if home_win_pct > TBD_MAX_WIN_PCT:
                print(f"    [TBD 캡 적용] {home_name} 승률 {home_win_pct:.1f}% → {TBD_MAX_WIN_PCT}% 상한 적용 (비상식적 수치 방지)")
                home_win_pct = TBD_MAX_WIN_PCT
                away_win_pct = round(100.0 - home_win_pct, 1)
        elif home_is_tbd and not away_is_tbd:
            label = "시즌 통계 없음" if home_no_stats else "선발 미정"
            print(f"    [TBD 패널티] {home_name} {label} → 승률 {TBD_PENALTY}% 하향")
            home_win_pct = max(home_win_pct - TBD_PENALTY, 5.0)
            away_win_pct = 100.0 - home_win_pct
            if away_win_pct > TBD_MAX_WIN_PCT:
                print(f"    [TBD 캡 적용] {away_name} 승률 {away_win_pct:.1f}% → {TBD_MAX_WIN_PCT}% 상한 적용 (비상식적 수치 방지)")
                away_win_pct = TBD_MAX_WIN_PCT
                home_win_pct = round(100.0 - away_win_pct, 1)

        # ── 투수 페널티 (pitcher_penalties.json 설정 기반) ──────────────
        pitcher_penalties = _load_pitcher_penalties()
        if away_pitcher_id and away_pitcher_id in pitcher_penalties:
            p = pitcher_penalties[away_pitcher_id]
            penalty_val = float(p["penalty"])
            away_win_pct = max(min(away_win_pct + penalty_val, 95.0), 5.0)
            home_win_pct = round(100.0 - away_win_pct, 1)
            direction = "하향" if penalty_val < 0 else "상향"
            print(f"    [투수 페널티] {p['pitcher_name']} ({away_name} 선발) → {abs(penalty_val):.0f}% {direction} | {p['reason']}")

        if home_pitcher_id and home_pitcher_id in pitcher_penalties:
            p = pitcher_penalties[home_pitcher_id]
            penalty_val = float(p["penalty"])
            home_win_pct = max(min(home_win_pct + penalty_val, 95.0), 5.0)
            away_win_pct = round(100.0 - home_win_pct, 1)
            direction = "하향" if penalty_val < 0 else "상향"
            print(f"    [투수 페널티] {p['pitcher_name']} ({home_name} 선발) → {abs(penalty_val):.0f}% {direction} | {p['reason']}")

        # ── 관찰 투수 (watch_list) 코멘트 출력 ──────────────────────────
        # 페널티는 없지만, 특이사항 주의 투수가 선발일 때 상대 타선 정보 코멘트
        watch_list = _load_pitcher_watch_list()

        def _rhb_comment(pitcher_id, pitcher_name, opponent_id, opponent_name):
            """우타자 비중 코멘트: MLB Stats API 로스터에서 타자 타석 방향 집계"""
            try:
                import requests
                url = f"https://statsapi.mlb.com/api/v1/teams/{opponent_id}/roster/Active"
                resp = requests.get(url, params={"hydrate": "person(batSide)"}, timeout=10)
                roster = resp.json().get("roster", [])
                batters = [p for p in roster if p.get("position", {}).get("type") == "Hitter"]
                if not batters:
                    return f"상대({opponent_name}) 타선 구성 데이터 없음"
                rhb = sum(1 for p in batters if p.get("person", {}).get("batSide", {}).get("code") == "R")
                lhb = sum(1 for p in batters if p.get("person", {}).get("batSide", {}).get("code") == "L")
                swb = len(batters) - rhb - lhb
                total = len(batters)
                rhb_pct = rhb / total * 100 if total else 0
                return (f"상대 타선({opponent_name}) 좌우 구성 → "
                        f"우타 {rhb}명({rhb_pct:.0f}%) / 좌타 {lhb}명 / 스위치 {swb}명 (총 {total}명) | "
                        f"{'⚠️ 우타자 비중 높음 — 좌완 Sánchez 주의' if rhb_pct >= 55 else '우타자 비중 보통'}")
            except Exception as e:
                return f"타선 구성 조회 실패: {e}"

        for pid, side, opp_id, opp_name in [
            (away_pitcher_id, "away", home_id, home_name),
            (home_pitcher_id, "home", away_id, away_name),
        ]:
            if pid and pid in watch_list:
                w = watch_list[pid]
                comment = _rhb_comment(pid, w["pitcher_name"], opp_id, opp_name)
                print(f"    [관찰 투수 주의] {w['pitcher_name']} 선발 — {w['watch_reason']}")
                print(f"    [타선 분석] {comment}")

        # ── 고득점 환경 홈팀 이점 보정 ──────────────────────────────────
        # 양 팀 공격 점수 평균이 57점 이상이면 타격전 환경 → 홈팀 추가 이점 (최대 +4%)
        # 배경: 고득점 타격전일수록 홈팬/홈구장/익숙한 환경의 이점이 증폭됨
        COORS_TEAM_ID = 115  # Colorado Rockies (쿠어스 중복 방지용)
        avg_offense = (away_off["score"] + home_off["score"]) / 2
        if avg_offense >= 57.0 and home_id != COORS_TEAM_ID:
            high_score_boost = round(min((avg_offense - 57.0) / 43.0 * 4.0, 4.0), 1)
            if high_score_boost > 0:
                home_win_pct = min(home_win_pct + high_score_boost, 95.0)
                away_win_pct = round(100.0 - home_win_pct, 1)
                print(f"    [고득점 홈 이점] 공격 평균 {avg_offense:.1f}점 → 홈팀 +{high_score_boost}% ({home_name}: {home_win_pct}%)")

        # ── 쿠어스 필드 보정 (COL 홈 경기) ─────────────────────────────
        if home_id == COORS_TEAM_ID:
            coors_boost = 6.0  # 기본 보정
            # 양 팀 선발 ERA 평균이 5.0 이상 → 타자 대폭발 환경 → 추가 +4%
            a_era_val = away_p_era if away_p_era is not None else 4.50
            h_era_val = home_p_era if home_p_era is not None else 4.50
            avg_starter_era = (a_era_val + h_era_val) / 2
            if avg_starter_era >= 5.0:
                coors_boost += 4.0
                print(f"    [쿠어스 강화 보정] 양 팀 ERA 평균 {avg_starter_era:.2f} → +{coors_boost}%")
            else:
                print(f"    [쿠어스 필드 보정] COL 홈 → 홈팀 승률 +{coors_boost}%")
            home_win_pct = min(home_win_pct + coors_boost, 95.0)
            away_win_pct = 100.0 - home_win_pct

        # ── 최종 하드캡: 67% 초과 불가 ───────────────────────────────
        # TBD 캡(70%)과 별도로, 일반 예측에서도 67% 초과 시 억제
        # 근거: 역사적으로 MLB 단일 경기 승률 67% 이상은 실제로 거의 달성 불가
        # 7/10 ARI@LAD 72.3%→LAD가 9-3으로 패한 사례 등에서 검증
        FINAL_HARD_CAP = 67.0
        if away_win_pct > FINAL_HARD_CAP:
            print(f"    [최종 하드캡] {away_name} {away_win_pct:.1f}% → {FINAL_HARD_CAP}% 상한 적용")
            away_win_pct = FINAL_HARD_CAP
            home_win_pct = round(100.0 - away_win_pct, 1)
        elif home_win_pct > FINAL_HARD_CAP:
            print(f"    [최종 하드캡] {home_name} {home_win_pct:.1f}% → {FINAL_HARD_CAP}% 상한 적용")
            home_win_pct = FINAL_HARD_CAP
            away_win_pct = round(100.0 - home_win_pct, 1)

        # ── 극단적 예측 경고 ─────────────────────────────────────────
        # 어느 팀이든 75% 이상이면 과도한 예측 가능성 → 콘솔 경고 출력
        _extreme_max = max(away_win_pct, home_win_pct)
        if _extreme_max >= 75.0:
            _extreme_leader = away_name if away_win_pct > home_win_pct else home_name
            print(f"    [⚠️ 극단적 예측] {_extreme_leader} {_extreme_max:.1f}% — Kalshi 비교 후 신중하게 해석 권장")

        # ── Kalshi ──────────────────────────────────────────────────
        kalshi_home, _ = _safe(
            lambda: get_kalshi_prob(game_date, away_name, home_name),
            (None, None), "Kalshi"
        )

        # ── Value Bet ────────────────────────────────────────────────
        vb = evaluate(home_win_pct, kalshi_home, home_name, away_name)

        # ── 선발투수 시즌 스탯 ──────────────────────────────────────────
        def _pitcher_stats(pid):
            if not pid:
                return {"wins": None, "losses": None, "era": None}
            s = get_pitcher_season(pid)
            return {
                "wins":   s.get("wins"),
                "losses": s.get("losses"),
                "era":    s.get("era"),
            }
        away_pitcher_stats = _safe(lambda apid=away_pitcher_id: _pitcher_stats(apid),
                                   {"wins": None, "losses": None, "era": None}, "away 투수스탯")
        home_pitcher_stats = _safe(lambda hpid=home_pitcher_id: _pitcher_stats(hpid),
                                   {"wins": None, "losses": None, "era": None}, "home 투수스탯")

        # ── 부상 노트 ────────────────────────────────────────────────
        notes = _safe(lambda: get_injury_notes(away_id, home_id, away_name, home_name), None, "부상체크")

        # ── 관찰 투수 노트 → notes 필드에 추가 ───────────────────────
        watch_notes = []
        for pid, opp_id, opp_name in [
            (away_pitcher_id, home_id, home_name),
            (home_pitcher_id, away_id, away_name),
        ]:
            if pid and pid in watch_list:
                w = watch_list[pid]
                wc = _rhb_comment(pid, w["pitcher_name"], opp_id, opp_name)
                watch_notes.append(f"[관찰투수] {w['pitcher_name']}: {wc}")
        if watch_notes:
            notes = (notes or "") + " | " + " / ".join(watch_notes)

        # ── 모델 적중 판정 ────────────────────────────────────────────
        actual_winner = g.get("actual_winner")
        model_winner  = None
        model_correct = None
        if actual_winner:
            model_winner  = home_name if home_win_pct >= away_win_pct else away_name
            model_correct = (model_winner == actual_winner)

        # ── 팀 순위 정보 ─────────────────────────────────────────────
        def _team_standing(team_id):
            s = standings_map.get(team_id, {})
            return {
                "div_rank":  s.get("div_rank"),
                "div_name":  s.get("div_name", ""),
                "wins":      s.get("wins"),
                "losses":    s.get("losses"),
                "games_back": s.get("games_back", "-"),
            }
        away_standing = _team_standing(away_id)
        home_standing = _team_standing(home_id)

        results.append({
            "date":         game_date,
            "status":       g.get("status", ""),
            "away":         away_name,
            "home":         home_name,
            "away_standing": away_standing,
            "home_standing": home_standing,
            "away_pitcher": g.get("away_pitcher", "TBD"),
            "away_pitcher_stats": away_pitcher_stats,
            "home_pitcher": g.get("home_pitcher", "TBD"),
            "home_pitcher_stats": home_pitcher_stats,
            "pred_model":   pred_model,
            "win_prob": {
                "away": away_win_pct,
                "home": home_win_pct,
            },
            "expected_score": {
                "away": final_exp_away,
                "home": final_exp_home,
            },
            "blend_detail": blend_detail,
            "ml_mc_agree": (
                None if blend_detail is None
                else (
                    (blend_detail["ml_away"] > blend_detail["ml_home"]) ==
                    (blend_detail["mc_away"] > blend_detail["mc_home"])
                )
            ),
            "ml_mc_conflict_level": ml_mc_conflict_level,
            "scores": {
                "away_offense": away_off["score"],
                "away_defense": away_def["score"],
                "home_offense": home_off["score"],
                "home_defense": home_def["score"],
            },
            "actual_score": {
                "away": g.get("actual_away"),
                "home": g.get("actual_home"),
            },
            "actual_winner":  actual_winner,
            "model_winner":   model_winner,
            "model_correct":  model_correct,
            "notes":          notes or "",
            "kalshi_prob":    vb["kalshi_prob"],
            "edge":           vb["edge"],
            "value_bet":      vb["value_bet"],
        })

        print(f"    Value Bet: {vb['value_bet']}\n")

    # ── 저장 ──────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_payload = json.dumps(results, ensure_ascii=False, indent=2)

    (OUTPUT_DIR / "predictions.json").write_text(json_payload, encoding="utf-8")
    (OUTPUT_DIR / f"predictions_{game_date}.js").write_text(
        f"// Auto-generated — {game_date}\nwindow.PREDICTIONS_DATA = {json_payload};\n",
        encoding="utf-8"
    )
    print(f"✅ 저장 완료 ({len(results)}경기)")
    return results


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else None
    run(d)
