"""
라인업 기반 스코어카드 예측 파이프라인

기존 ML+MC 이중 모델 대신 단일 스코어카드:
  - 선발투수 최근 성적 (30%)
  - 불펜 최근 성적 (20%)
  - 팀 타선 최근 성적 (35%)
  - 상황적 요소 (15%)
"""
import json
import sys
from pathlib import Path
from datetime import date
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from mlb_schedule         import get_games
from mlb_stats_fetcher    import (get_team_hitting_log, get_team_pitching_log,
                                   get_pitcher_gamelog, get_pitcher_season,
                                   get_standings_map)
from pitcher_recent_score import analyze_pitcher_recent, pitcher_score, _default_pitcher
from bullpen_score        import analyze_bullpen, bullpen_score, _default_bullpen
from batting_score_v2     import (analyze_batting, analyze_lineup_batting,
                                   analyze_lineup_batting_with_splits,
                                   batting_score, _default_batting)
from situational_score    import situational_score
from scorecard            import build_scorecard
from lineup_fetcher       import (get_game_lineup, get_pitcher_handedness,
                                   estimate_rotation_pitcher,
                                   get_previous_day_lineup)
from kalshi_client        import get_kalshi_prob
from value_bet            import evaluate
from injury_check         import get_injury_notes
import requests as _requests

OUTPUT_DIR  = Path(__file__).parent.parent / "output"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "weights.json"


def _load_scorecard_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg.get("scorecard", {})
    except Exception:
        return {}


def _load_full_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe(fn, default=None, label=""):
    try:
        return fn()
    except Exception as e:
        print(f"  [경고] {label}: {e}")
        return default


def _pitcher_display_stats(pitcher_id):
    """대시보드 표시용 투수 시즌 통계"""
    if not pitcher_id:
        return {"wins": None, "losses": None, "era": None}
    s = _safe(lambda p=pitcher_id: get_pitcher_season(p), {}, "투수시즌스탯")
    return {
        "wins":   s.get("wins"),
        "losses": s.get("losses"),
        "era":    s.get("era"),
    }


def _expected_runs(bat_score: float, opp_def_score: float,
                   home_bonus: float = 0.0) -> float:
    """
    타선 점수 + 상대 수비 점수 → 예상 득점
    opp_def_score = (상대 SP점수 + 상대 BP점수) / 2
    """
    lam = (bat_score / 100.0) * max(0.4, 1.0 - opp_def_score / 200.0) * 9.0
    return round(lam * (1.0 + home_bonus), 1)


def run(game_date: Optional[str] = None) -> list:
    if game_date is None:
        game_date = date.today().isoformat()

    full_cfg = _load_full_config()
    sc_cfg = full_cfg.get("scorecard", {})

    # ── 수요일 보정 설정 로드 ─────────────────────────────────────────────────
    wed_cfg = full_cfg.get("wednesday_adjustment", {})
    WED_ENABLED    = bool(wed_cfg.get("enabled", False))
    WED_HARD_CAP   = float(wed_cfg.get("hard_cap_override", 62.0))
    WED_BP_PENALTY = float(wed_cfg.get("bp_penalty", 5.0))

    # 오늘이 수요일인지 확인
    from datetime import date as _date
    is_wednesday = (_date.fromisoformat(game_date).weekday() == 2)
    if is_wednesday and WED_ENABLED:
        print(f"📅 [수요일 보정] 불펜 소진 패널티 활성화 (BP -{WED_BP_PENALTY}pt, Hard Cap → {WED_HARD_CAP}%)")

    # ── 팀 바이어스 보정 설정 로드 ────────────────────────────────────────────
    bias_cfg = full_cfg.get("team_bias_correction", {})
    BIAS_ENABLED = bool(bias_cfg.get("enabled", False))
    TEAM_BIAS    = bias_cfg.get("teams", {}) if BIAS_ENABLED else {}

    sc_cfg = _load_scorecard_config()
    HARD_CAP   = float(sc_cfg.get("hard_cap",          67.0))
    TBD_SCORE  = float(sc_cfg.get("tbd_penalty_score", 45.0))
    HOME_BONUS = float(sc_cfg.get("home_bonus",         3.0))
    SIG_K      = float(sc_cfg.get("sigmoid_k",         0.055))
    SP_W       = float(sc_cfg.get("sp_weight",         0.30))
    BP_W       = float(sc_cfg.get("bp_weight",         0.20))
    BAT_W      = float(sc_cfg.get("bat_weight",        0.35))
    SIT_W      = float(sc_cfg.get("sit_weight",        0.15))
    COORS_SP_PENALTY        = float(sc_cfg.get("coors_sp_penalty",        8.0))
    COORS_BAT_BONUS         = float(sc_cfg.get("coors_bat_bonus",         6.0))
    HARD_CAP_OPP_THRESHOLD  = float(sc_cfg.get("hard_cap_opp_threshold", 50.0))
    HARD_CAP_REDUCED        = float(sc_cfg.get("hard_cap_reduced",       63.0))
    # 선발 ERA 위험 플래그: 선발 ERA 5.0↑ + 상대 타선 60점↑ 시 상대 타선 보너스
    SP_ERA_RISK_THRESHOLD   = float(sc_cfg.get("sp_era_risk_threshold",   5.0))
    SP_ERA_RISK_BAT_BONUS   = float(sc_cfg.get("sp_era_risk_bat_bonus",   8.0))
    SP_ERA_RISK_BAT_MIN     = float(sc_cfg.get("sp_era_risk_bat_min",    60.0))
    # 고위험 경기: 양 팀 선발 ERA 모두 임계값 이상 → Value Bet 자동 제외
    BOTH_SP_HIGH_ERA_THRESHOLD = float(sc_cfg.get("both_sp_high_era_threshold", 5.0))

    # Hot 투수 Value Bet 필터: edge 임계값 상향
    vb_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("value_bet", {})
    VB_EDGE_DEFAULT   = float(vb_cfg.get("edge_threshold_pct",          8.0))
    VB_EDGE_HOT_SP    = float(vb_cfg.get("hot_sp_edge_threshold_pct",  14.0))
    VB_EDGE_HOT_OPP   = float(vb_cfg.get("hot_opp_sp_edge_threshold_pct", 14.0))
    # 모델+시장 동시 동의 필터 설정
    CONSENSUS_MIN_MODEL_PCT = float(vb_cfg.get("consensus_min_model_pct",  60.0))  # 모델 최소 승률
    CONSENSUS_MAX_EDGE_PCT  = float(vb_cfg.get("consensus_max_edge_pct",    8.0))  # 최대 |edge| (이 이하여야 동의)

    print(f"\n{'='*60}")
    print(f"MLB 스코어카드 예측 파이프라인 실행: {game_date}")
    print(f"가중치: SP {SP_W:.0%} | BP {BP_W:.0%} | 타선 {BAT_W:.0%} | 상황 {SIT_W:.0%}")
    print(f"{'='*60}")

    def _pitcher_gamelog_summary(gl: list, n: int = 5) -> list:
        """게임로그 최근 n경기 요약 (대시보드 표시용)"""
        if not gl:
            return []
        def _ip(s):
            try:
                parts = str(s).split(".")
                return int(parts[0]) + (int(parts[1]) / 3 if len(parts) > 1 else 0)
            except: return 0.0
        recent = gl[-n:] if len(gl) >= n else gl
        out = []
        for s in recent:
            ip_val = _ip(s.get("inningsPitched", "0"))
            er     = int(float(s.get("earnedRuns", 0) or 0))
            h      = int(float(s.get("hits", 0) or 0))
            bb     = int(float(s.get("baseOnBalls", 0) or 0))
            so     = int(float(s.get("strikeOuts", 0) or 0))
            era_g  = round(er / ip_val * 9, 2) if ip_val > 0 else None
            opp = s.get("_opponent", "")
            is_home = s.get("_is_home", False)
            opp_label = f"vs {opp}" if is_home else f"@ {opp}"
            out.append({
                "date":  s.get("_game_date", ""),
                "opp":   opp_label,
                "ip":    s.get("inningsPitched", "0"),
                "er":    er,
                "h":     h,
                "bb":    bb,
                "so":    so,
                "era":   era_g,
            })
        return out

    standings_map = _safe(get_standings_map, {}, "순위표")

    def _recent_form(team_id: int, before_date: str, n: int = 5) -> dict:
        """팀 최근 n경기 W/L 결과 반환 (before_date 이전 경기만)"""
        from datetime import datetime, timedelta
        try:
            end = datetime.strptime(before_date, "%Y-%m-%d") - timedelta(days=1)
            start = end - timedelta(days=20)
            url = (
                f"https://statsapi.mlb.com/api/v1/schedule"
                f"?sportId=1&teamId={team_id}"
                f"&startDate={start.strftime('%Y-%m-%d')}"
                f"&endDate={end.strftime('%Y-%m-%d')}"
                f"&hydrate=decisions,linescore&gameType=R"
            )
            data = _requests.get(url, timeout=8).json()
            results_list = []
            for d in data.get("dates", []):
                for gm in d.get("games", []):
                    if gm.get("status", {}).get("abstractGameState") != "Final":
                        continue
                    t = gm.get("teams", {})
                    away_t = t.get("away", {})
                    home_t = t.get("home", {})
                    is_away = away_t.get("team", {}).get("id") == team_id
                    team_data = away_t if is_away else home_t
                    opp_data  = home_t if is_away else away_t
                    won = team_data.get("isWinner", False)
                    ts  = team_data.get("score", 0)
                    os  = opp_data.get("score", 0)
                    results_list.append({
                        "date": d["date"],
                        "result": "W" if won else "L",
                        "score": f"{ts}-{os}",
                        "home": not is_away,
                    })
            recent = results_list[-n:] if len(results_list) >= n else results_list
            wins   = sum(1 for r in recent if r["result"] == "W")
            losses = len(recent) - wins
            # 연승/연패 계산
            streak = 0
            if recent:
                last_result = recent[-1]["result"]
                for r in reversed(recent):
                    if r["result"] == last_result:
                        streak += 1 if last_result == "W" else -1
                    else:
                        break
            return {"games": recent, "wins": wins, "losses": losses, "streak": streak}
        except Exception:
            return {"games": [], "wins": 0, "losses": 0, "streak": 0}

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
        away_pitcher    = g.get("away_pitcher", "TBD")
        home_pitcher    = g.get("home_pitcher", "TBD")

        print(f"[{i}/{len(games)}] {away_name} @ {home_name}")

        # ── 1. 선발투수 분석 ──────────────────────────────────────────
        away_is_tbd = (away_pitcher in ("TBD", "", None) or away_pitcher_id is None)
        home_is_tbd = (home_pitcher in ("TBD", "", None) or home_pitcher_id is None)
        away_gl = []
        home_gl = []

        # 원정 선발투수: TBD면 로테이션 추정
        away_est_pitcher = None
        if away_pitcher_id and not away_is_tbd:
            away_gl = _safe(lambda p=away_pitcher_id: get_pitcher_gamelog(p, 10), [], "원정 투수 게임로그")
            away_sp_detail = analyze_pitcher_recent(away_gl, 10, game_date)
            away_handedness = _safe(lambda p=away_pitcher_id: get_pitcher_handedness(p), "R", "원정투수손방향")
        else:
            # 로테이션 추정 시도
            away_est_pitcher = _safe(
                lambda tid=away_id: estimate_rotation_pitcher(tid, game_date),
                None, "원정 로테이션 추정"
            )
            if away_est_pitcher:
                away_pitcher_id = away_est_pitcher["id"]
                away_pitcher    = away_est_pitcher["name"] + " (미정)"
                away_gl = _safe(lambda p=away_est_pitcher["id"]: get_pitcher_gamelog(p, 10), [], "원정 추정투수 게임로그")
                away_sp_detail  = analyze_pitcher_recent(away_gl, 10, game_date)
                away_handedness = away_est_pitcher.get("handedness", "R")
                away_is_tbd     = False  # 추정으로 대체됨
            else:
                away_sp_detail  = _default_pitcher()
                away_handedness = "R"

        # 홈 선발투수: TBD면 로테이션 추정
        home_est_pitcher = None
        if home_pitcher_id and not home_is_tbd:
            home_gl = _safe(lambda p=home_pitcher_id: get_pitcher_gamelog(p, 10), [], "홈 투수 게임로그")
            home_sp_detail = analyze_pitcher_recent(home_gl, 10, game_date)
            home_handedness = _safe(lambda p=home_pitcher_id: get_pitcher_handedness(p), "R", "홈투수손방향")
        else:
            home_est_pitcher = _safe(
                lambda tid=home_id: estimate_rotation_pitcher(tid, game_date),
                None, "홈 로테이션 추정"
            )
            if home_est_pitcher:
                home_pitcher_id = home_est_pitcher["id"]
                home_pitcher    = home_est_pitcher["name"] + " (미정)"
                home_gl = _safe(lambda p=home_est_pitcher["id"]: get_pitcher_gamelog(p, 10), [], "홈 추정투수 게임로그")
                home_sp_detail  = analyze_pitcher_recent(home_gl, 10, game_date)
                home_handedness = home_est_pitcher.get("handedness", "R")
                home_is_tbd     = False  # 추정으로 대체됨
            else:
                home_sp_detail  = _default_pitcher()
                home_handedness = "R"

        # 시즌 ERA 가져오기 (하한선 보정용)
        away_season_era = None
        home_season_era = None
        if away_pitcher_id and not away_is_tbd:
            _aw_s = _safe(lambda p=away_pitcher_id: get_pitcher_season(p), {}, "원정선발시즌ERA")
            _aw_era = _aw_s.get("era")
            try: away_season_era = float(_aw_era) if _aw_era else None
            except: pass
        if home_pitcher_id and not home_is_tbd:
            _hm_s = _safe(lambda p=home_pitcher_id: get_pitcher_season(p), {}, "홈선발시즌ERA")
            _hm_era = _hm_s.get("era")
            try: home_season_era = float(_hm_era) if _hm_era else None
            except: pass

        away_sp_s = TBD_SCORE if away_is_tbd else pitcher_score(away_sp_detail, season_era=away_season_era)
        home_sp_s = TBD_SCORE if home_is_tbd else pitcher_score(home_sp_detail, season_era=home_season_era)

        tbd_tag_away = " [미정]" if away_est_pitcher else (" [TBD]" if away_is_tbd else "")
        tbd_tag_home = " [미정]" if home_est_pitcher else (" [TBD]" if home_is_tbd else "")

        def _sp_note(sp):
            notes = []
            if sp.get("rest_note") == "short_rest": notes.append(f"⚡단기휴식{sp['rest_days']}일")
            elif sp.get("rest_note") == "long_rest":  notes.append(f"🔄복귀{sp['rest_days']}일(trend리셋)")
            elif sp.get("rest_note") == "extra_rest": notes.append(f"💤장기휴식{sp['rest_days']}일")
            elif sp.get("rest_days") is not None: notes.append(f"휴식{sp['rest_days']}일")
            if sp.get("sample_confidence", 1.0) < 1.0:
                notes.append(f"샘플{sp['n_games']}경기")
            return (" [" + "/".join(notes) + "]") if notes else ""

        print(f"    [선발] {away_name} {away_pitcher}{tbd_tag_away}: ERA {away_sp_detail['era']} WHIP {away_sp_detail['whip']} trend={away_sp_detail['trend']}{_sp_note(away_sp_detail)} → {away_sp_s}점")
        print(f"    [선발] {home_name} {home_pitcher}{tbd_tag_home}: ERA {home_sp_detail['era']} WHIP {home_sp_detail['whip']} trend={home_sp_detail['trend']}{_sp_note(home_sp_detail)} → {home_sp_s}점")

        # ── 2. 불펜 분석 ──────────────────────────────────────────────
        away_ptch_log = _safe(lambda aid=away_id: get_team_pitching_log(aid, 10), [], "원정 투구로그")
        home_ptch_log = _safe(lambda hid=home_id: get_team_pitching_log(hid, 10), [], "홈 투구로그")

        away_bp_detail = analyze_bullpen(away_ptch_log, away_sp_detail.get("avg_ip", 5.5))
        home_bp_detail = analyze_bullpen(home_ptch_log, home_sp_detail.get("avg_ip", 5.5))

        away_bp_s = bullpen_score(away_bp_detail)
        home_bp_s = bullpen_score(home_bp_detail)

        # ── 수요일 불펜 소진 패널티 ───────────────────────────────────────
        if is_wednesday and WED_ENABLED:
            away_bp_s = max(0.0, away_bp_s - WED_BP_PENALTY)
            home_bp_s = max(0.0, home_bp_s - WED_BP_PENALTY)
            print(f"    [수요일패널티] 불펜 각 -{WED_BP_PENALTY}pt 적용 (시리즈 마지막날 소진 리스크)")

        print(f"    [불펜] {away_name}: ERA {away_bp_detail['bullpen_era']} → {away_bp_s}점 | {home_name}: ERA {home_bp_detail['bullpen_era']} → {home_bp_s}점")

        # ── 3. 타선 분석 ─────────────────────────────────────────────
        #   우선순위: ① 확정 라인업+시즌OPS  ② 전날 라인업+LHP/RHP스플릿  ③ 팀통계
        away_hit_log = _safe(lambda aid=away_id: get_team_hitting_log(aid, 10), [], "원정 타격로그")
        home_hit_log = _safe(lambda hid=home_id: get_team_hitting_log(hid, 10), [], "홈 타격로그")

        game_pk = g.get("gamePk")
        lineup  = _safe(lambda pk=game_pk: get_game_lineup(pk), None, "라인업") if game_pk else None

        if lineup and lineup.get("away") and lineup.get("home"):
            # ✅ ① 확정 라인업: 개인 타자 시즌OPS 기반 + 상대 손방향 스플릿
            bat_source = "lineup"
            away_bat_detail = _safe(
                lambda lp=lineup["away"], hl=away_hit_log, hd=home_handedness:
                    analyze_lineup_batting_with_splits(lp, hd, hl),
                _default_batting(), "원정 라인업 타선"
            )
            home_bat_detail = _safe(
                lambda lp=lineup["home"], hl=home_hit_log, hd=away_handedness:
                    analyze_lineup_batting_with_splits(lp, hd, hl),
                _default_batting(), "홈 라인업 타선"
            )
            away_lineup_names = [p["name"] for p in lineup["away"]]
            home_lineup_names = [p["name"] for p in lineup["home"]]
            print(f"    [타선 ✅확정] {away_name} vs {home_handedness}P: {', '.join(away_lineup_names[:3])} 등")
            print(f"    [타선 ✅확정] {home_name} vs {away_handedness}P: {', '.join(home_lineup_names[:3])} 등")

        else:
            # 전날 라인업 시도
            prev_away = _safe(
                lambda tid=away_id: get_previous_day_lineup(tid, game_date),
                None, "원정 전날라인업"
            )
            prev_home = _safe(
                lambda tid=home_id: get_previous_day_lineup(tid, game_date),
                None, "홈 전날라인업"
            )

            if prev_away and prev_home:
                # ✅ ② 전날 라인업 + LHP/RHP 스플릿 (스플릿 없으면 시즌OPS로 자동 fallback)
                away_bat_detail = _safe(
                    lambda lp=prev_away, hl=away_hit_log, hd=home_handedness:
                        analyze_lineup_batting_with_splits(lp, hd, hl),
                    _default_batting(), "원정 전날라인업 타선"
                )
                home_bat_detail = _safe(
                    lambda lp=prev_home, hl=home_hit_log, hd=away_handedness:
                        analyze_lineup_batting_with_splits(lp, hd, hl),
                    _default_batting(), "홈 전날라인업 타선"
                )
                # source는 배팅분석 결과에서 가져옴 (splits 사용 여부에 따라 자동 결정)
                bat_source = away_bat_detail.get("source", "prev_day")
                away_names_prev = [p["name"] for p in prev_away[:3]]
                home_names_prev = [p["name"] for p in prev_home[:3]]
                splits_tag = "스플릿 반영" if away_bat_detail.get("splits_used") else "시즌OPS"
                print(f"    [타선 📋추정] {away_name} vs {home_handedness}P ({splits_tag}): {', '.join(away_names_prev)} 등")
                print(f"    [타선 📋추정] {home_name} vs {away_handedness}P ({splits_tag}): {', '.join(home_names_prev)} 등")

            else:
                # ③ 팀 통계 fallback
                bat_source = "team_stats"
                away_bat_detail = analyze_batting(away_hit_log, {})
                home_bat_detail = analyze_batting(home_hit_log, {})
                print(f"    [타선 ⏳팀통계] 라인업 미공개 → 팀 최근 10경기 통계 사용")

        away_bat_s = batting_score(away_bat_detail)
        home_bat_s = batting_score(home_bat_detail)
        print(f"    [타선] {away_name}: avg {away_bat_detail['recent_avg']:.3f} OPS {away_bat_detail['season_ops']:.3f} 득점 {away_bat_detail['runs_per_g']} → {away_bat_s}점")
        print(f"    [타선] {home_name}: avg {home_bat_detail['recent_avg']:.3f} OPS {home_bat_detail['season_ops']:.3f} 득점 {home_bat_detail['runs_per_g']} → {home_bat_s}점")

        # ── 4. 상황 분석 ──────────────────────────────────────────────
        away_st = standings_map.get(away_id, {})
        home_st = standings_map.get(home_id, {})

        away_sit_s = situational_score(
            is_home=False,
            streak=away_st.get("streak_wins", 0),
            div_rank=away_st.get("div_rank"),
            wins=away_st.get("wins"),
            losses=away_st.get("losses"),
        )
        home_sit_s = situational_score(
            is_home=True,
            streak=home_st.get("streak_wins", 0),
            div_rank=home_st.get("div_rank"),
            wins=home_st.get("wins"),
            losses=home_st.get("losses"),
        )
        print(f"    [상황] {away_name}: {away_sit_s}점 (streak {away_st.get('streak_wins',0):+d}) | {home_name}: {home_sit_s}점 (streak {home_st.get('streak_wins',0):+d})")

        # ── 4.5 쿠어스 필드 보정 (COL 홈경기) ────────────────────────
        is_coors = ("Colorado" in home_name or "Rockies" in home_name)
        if is_coors:
            away_sp_s  = max(0.0, away_sp_s  - COORS_SP_PENALTY)
            home_sp_s  = max(0.0, home_sp_s  - COORS_SP_PENALTY)
            away_bat_s = min(100.0, away_bat_s + COORS_BAT_BONUS)
            home_bat_s = min(100.0, home_bat_s + COORS_BAT_BONUS)
            print(f"    [쿠어스] COL 홈경기: SP -{COORS_SP_PENALTY}pt, 타선 +{COORS_BAT_BONUS}pt 적용")

        # ── 4.7 선발 ERA 위험 플래그 보정 ────────────────────────────
        # 선발 ERA 5.0↑ + 상대 타선 60점↑ → 상대 타선 추가 보너스 (대량 실점 위험)
        away_sp_era = away_sp_detail.get("era", 4.50)
        home_sp_era = home_sp_detail.get("era", 4.50)

        if away_sp_era >= SP_ERA_RISK_THRESHOLD and home_bat_s >= SP_ERA_RISK_BAT_MIN:
            home_bat_s = min(100.0, home_bat_s + SP_ERA_RISK_BAT_BONUS)
            print(f"    [ERA위험] {away_name} 선발 ERA {away_sp_era} → {home_name} 타선 +{SP_ERA_RISK_BAT_BONUS}pt")
        if home_sp_era >= SP_ERA_RISK_THRESHOLD and away_bat_s >= SP_ERA_RISK_BAT_MIN:
            away_bat_s = min(100.0, away_bat_s + SP_ERA_RISK_BAT_BONUS)
            print(f"    [ERA위험] {home_name} 선발 ERA {home_sp_era} → {away_name} 타선 +{SP_ERA_RISK_BAT_BONUS}pt")

        # ── 5. 스코어카드 종합 ────────────────────────────────────────
        # 선발 Cold 패턴에 따른 동적 가중치 조정
        away_trend = away_sp_detail.get("trend", "stable")
        home_trend = home_sp_detail.get("trend", "stable")
        both_cold  = (away_trend == "cold" and home_trend == "cold")
        any_cold   = (away_trend == "cold" or  home_trend == "cold")

        eff_sp_w  = SP_W
        eff_bp_w  = BP_W
        eff_bat_w = BAT_W
        eff_sit_w = SIT_W

        if both_cold:
            # 양팀 선발 모두 Cold → 불펜전 확률 높음
            # 타선 35%→25%, 불펜 20%→30% (SP/상황은 유지, 합계 100% 보장)
            eff_bat_w = 0.25
            eff_bp_w  = 0.30
            print(f"    [🔄 불펜전] 양팀 선발 모두 Cold → 타선 {eff_bat_w:.0%} / 불펜 {eff_bp_w:.0%}로 조정")
        elif any_cold:
            # 한쪽 Cold → 불펜 의존도 소폭 상승
            # 타선 35%→30%, 불펜 20%→25%
            eff_bat_w = 0.30
            eff_bp_w  = 0.25
            cold_side = away_name if away_trend == "cold" else home_name
            print(f"    [🔄 불펜보정] {cold_side} 선발 Cold → 타선 {eff_bat_w:.0%} / 불펜 {eff_bp_w:.0%}로 조정")

        sc = build_scorecard(
            away_sp=away_sp_s, home_sp=home_sp_s,
            away_bp=away_bp_s, home_bp=home_bp_s,
            away_bat=away_bat_s, home_bat=home_bat_s,
            away_sit=away_sit_s, home_sit=home_sit_s,
            sp_w=eff_sp_w, bp_w=eff_bp_w, bat_w=eff_bat_w, sit_w=eff_sit_w,
            home_bonus=HOME_BONUS, sigmoid_k=SIG_K,
        )
        away_win_pct = sc["away_win_pct"]
        home_win_pct = sc["home_win_pct"]

        print(f"    [스코어카드] {away_name}: {sc['away_total']}점 vs {home_name}: {sc['home_total']}점 → {away_win_pct}% : {home_win_pct}%")

        # ── 6. 투수 패널티 (pitcher_penalties.json) ──────────────────
        PENALTY_PATH = Path(__file__).parent.parent / "config" / "pitcher_penalties.json"
        try:
            _pj = json.loads(PENALTY_PATH.read_text(encoding="utf-8"))
            _pm = {p["pitcher_id"]: p for p in _pj.get("pitchers", [])}
        except Exception:
            _pm = {}

        if away_pitcher_id and away_pitcher_id in _pm:
            _p = _pm[away_pitcher_id]
            _pval = float(_p["penalty"])
            away_win_pct = max(min(away_win_pct + _pval, 95.0), 5.0)
            home_win_pct = round(100.0 - away_win_pct, 1)
            _dir = "하향" if _pval < 0 else "상향"
            print(f"    [투수 패널티] {_p['pitcher_name']} ({away_name} 선발) → {abs(_pval):.0f}% {_dir}")

        if home_pitcher_id and home_pitcher_id in _pm:
            _p = _pm[home_pitcher_id]
            _pval = float(_p["penalty"])
            home_win_pct = max(min(home_win_pct + _pval, 95.0), 5.0)
            away_win_pct = round(100.0 - home_win_pct, 1)
            _dir = "하향" if _pval < 0 else "상향"
            print(f"    [투수 패널티] {_p['pitcher_name']} ({home_name} 선발) → {abs(_pval):.0f}% {_dir}")

        # ── 7. TBD 추가 패널티 ────────────────────────────────────────
        if away_is_tbd and not home_is_tbd:
            home_win_pct = min(home_win_pct + 4.0, HARD_CAP)
            away_win_pct = round(100.0 - home_win_pct, 1)
            print(f"    [TBD 패널티] {away_name} 선발 미정 → {home_name} +4%")
        elif home_is_tbd and not away_is_tbd:
            away_win_pct = min(away_win_pct + 4.0, HARD_CAP)
            home_win_pct = round(100.0 - away_win_pct, 1)
            print(f"    [TBD 패널티] {home_name} 선발 미정 → {away_name} +4%")

        # ── 7b. 팀 바이어스 보정 ─────────────────────────────────────
        # 실측 데이터 기반: 특정 팀을 픽할 때 과대/과소평가 보정
        if TEAM_BIAS:
            pick_team = home_name if home_win_pct >= away_win_pct else away_name
            bias = TEAM_BIAS.get(pick_team, 0.0)
            if bias != 0.0:
                direction = "하향" if bias < 0 else "상향"
                if home_win_pct >= away_win_pct:
                    home_win_pct = max(50.0, min(home_win_pct + bias, HARD_CAP))
                    away_win_pct = round(100.0 - home_win_pct, 1)
                else:
                    away_win_pct = max(50.0, min(away_win_pct + bias, HARD_CAP))
                    home_win_pct = round(100.0 - away_win_pct, 1)
                print(f"    [팀바이어스] {pick_team} 픽 {direction} {abs(bias):.0f}% (실측 보정)")

        # ── 8. 최종 하드캡 (상대팀 강도 반영 동적 캡) ───────────────
        # 수요일은 별도 낮은 캡 적용
        if is_wednesday and WED_ENABLED:
            effective_cap = min(WED_HARD_CAP, HARD_CAP)
        else:
            # 상대팀 총점이 임계값 이상이면 캡을 낮춤 (양팀 다 강한 경우 과신 방지)
            opp_strong = (sc["home_total"] >= HARD_CAP_OPP_THRESHOLD or
                          sc["away_total"] >= HARD_CAP_OPP_THRESHOLD)
            effective_cap = HARD_CAP_REDUCED if opp_strong else HARD_CAP

        if away_win_pct > effective_cap:
            away_win_pct = effective_cap
            home_win_pct = round(100.0 - away_win_pct, 1)
            cap_label = f"수요일{WED_HARD_CAP}%캡" if (is_wednesday and WED_ENABLED) else (f"상대강팀{HARD_CAP_REDUCED}%캡" if not (is_wednesday and WED_ENABLED) else "")
            print(f"    [하드캡] {away_name} {away_win_pct}%로 제한 ({cap_label})")
        elif home_win_pct > effective_cap:
            home_win_pct = effective_cap
            away_win_pct = round(100.0 - home_win_pct, 1)
            cap_label = f"수요일{WED_HARD_CAP}%캡" if (is_wednesday and WED_ENABLED) else (f"상대강팀{HARD_CAP_REDUCED}%캡" if not (is_wednesday and WED_ENABLED) else "")
            print(f"    [하드캡] {home_name} {home_win_pct}%로 제한 ({cap_label})")

        # ── 9. 예상 득점 ──────────────────────────────────────────────
        away_def_s = (away_sp_s + away_bp_s) / 2
        home_def_s = (home_sp_s + home_bp_s) / 2

        exp_away = _expected_runs(away_bat_s, home_def_s, 0.0)
        exp_home = _expected_runs(home_bat_s, away_def_s, 0.05)  # 홈 미세 보정

        # ── 10. Kalshi + Value Bet ─────────────────────────────────────
        # 고위험 경기 판정: 양 팀 선발 ERA 모두 BOTH_SP_HIGH_ERA_THRESHOLD 이상
        both_sp_high_era = (away_sp_era >= BOTH_SP_HIGH_ERA_THRESHOLD and
                            home_sp_era >= BOTH_SP_HIGH_ERA_THRESHOLD)
        if both_sp_high_era:
            print(f"    [⚠️ 고위험] 양팀 선발 ERA 모두 {BOTH_SP_HIGH_ERA_THRESHOLD}+ "
                  f"({away_name} {away_sp_era} / {home_name} {home_sp_era}) → 예측 신뢰도 낮음")

        # 라인업 미공개(팀통계) 경기는 신뢰도 부족 → Value Bet 패스
        if bat_source == "team_stats":
            kalshi_home = None
            vb = {
                "kalshi_prob": None,
                "edge": None,
                "value_bet": "⏭️ 패스 (라인업 미공개)",
            }
            print(f"    [Value Bet] 라인업 미공개 → 패스")
        elif both_cold:
            # 양팀 선발 모두 Cold → 불펜전: 예측 신뢰도 매우 낮음 → Value Bet 자동 제외
            kalshi_home, _ = _safe(
                lambda: get_kalshi_prob(game_date, away_name, home_name),
                (None, None), "Kalshi"
            )
            vb = evaluate(home_win_pct, kalshi_home, home_name, away_name)
            if vb.get("value_bet", "").startswith("✅"):
                vb["value_bet"] = "⏭️ 패스 (불펜전 — 양팀 선발 Cold, 예측 신뢰 낮음)"
                print(f"    [Value Bet] 양팀 선발 Cold 불펜전 → Value Bet 자동 제외")
        elif both_sp_high_era:
            kalshi_home, _ = _safe(
                lambda: get_kalshi_prob(game_date, away_name, home_name),
                (None, None), "Kalshi"
            )
            vb = evaluate(home_win_pct, kalshi_home, home_name, away_name)
            # Value Bet이 있어도 고위험 경기 경고 추가
            if vb.get("value_bet", "").startswith("✅"):
                vb["value_bet"] = f"⚠️ 고위험경기 주의 — " + vb["value_bet"]
        else:
            kalshi_home, _ = _safe(
                lambda: get_kalshi_prob(game_date, away_name, home_name),
                (None, None), "Kalshi"
            )
            vb = evaluate(home_win_pct, kalshi_home, home_name, away_name)

            # ── Value Bet 선발 Hot/Cold 필터 ─────────────────────────
            if vb.get("value_bet", "").startswith("✅"):
                model_winner_is_home = (home_win_pct >= away_win_pct)
                winner_sp  = home_sp_detail if model_winner_is_home else away_sp_detail
                loser_sp   = away_sp_detail if model_winner_is_home else home_sp_detail
                winner_trend = winner_sp.get("trend", "stable")
                loser_trend  = loser_sp.get("trend",  "stable")
                edge_val = vb.get("edge") or 0.0

                # Hot 투수가 선발인 경기: edge 임계값 상향 적용
                # (Hot ERA는 과거 성적이지 당일 보장이 아님 — 연속 미스 패턴)
                winner_rest_note = winner_sp.get("rest_note")
                loser_rest_note  = loser_sp.get("rest_note")

                # extra_rest(장기 휴식) 시 추가 임계값 상향 (+4%p)
                hot_sp_threshold  = VB_EDGE_HOT_SP  + (4.0 if winner_rest_note == "extra_rest" else 0.0)
                hot_opp_threshold = VB_EDGE_HOT_OPP + (4.0 if loser_rest_note  == "extra_rest" else 0.0)

                if winner_trend == "hot" and abs(edge_val) < hot_sp_threshold:
                    rest_tag = f"·장기휴식{winner_sp.get('rest_days')}일" if winner_rest_note == "extra_rest" else ""
                    print(f"    [🔥 VB Hot필터] 예측팀 선발 Hot{rest_tag} → edge {edge_val}%p < 임계값 {hot_sp_threshold}%p → Value Bet 격하")
                    vb["value_bet"] = f"⚠️ VB주의(선발Hot{rest_tag}·edge부족 {edge_val}%p<{hot_sp_threshold}%p)"
                elif loser_trend == "hot" and abs(edge_val) < hot_opp_threshold:
                    rest_tag = f"·장기휴식{loser_sp.get('rest_days')}일" if loser_rest_note == "extra_rest" else ""
                    print(f"    [🔥 VB Hot필터] 상대 선발 Hot{rest_tag} → edge {edge_val}%p < 임계값 {hot_opp_threshold}%p → Value Bet 격하")
                    vb["value_bet"] = f"⚠️ VB주의(상대선발Hot{rest_tag}·edge부족 {edge_val}%p<{hot_opp_threshold}%p)"
                # Cold 선발 필터 (기존 유지)
                elif winner_trend == "cold":
                    print(f"    [⚠️ VB Cold필터] 예측팀 선발 Cold 트렌드 → Value Bet 신뢰도 하락")
                    vb["value_bet"] = "⚠️ VB주의(선발Cold) — " + vb["value_bet"]

            # ── 모델+시장 동시 동의 필터 (고신뢰 태그) ──────────────────
            # 조건: 모델 승률 ≥ 60% + |edge| ≤ 8%p (모델·시장 같은 방향) + 라인업 확정
            # → 두 신호가 동시에 같은 팀 지목 → 고신뢰 예측 태그 부여
            _edge_val = vb.get("edge")
            _model_top = max(home_win_pct, away_win_pct)
            if (bat_source == "lineup"
                    and _edge_val is not None
                    and _model_top >= CONSENSUS_MIN_MODEL_PCT
                    and abs(_edge_val) <= CONSENSUS_MAX_EDGE_PCT):
                # 모델 예측 팀과 시장 예측 팀이 같은지 확인
                # edge > 0 → 시장도 홈팀 우위, home_win_pct > away_win_pct → 모델도 홈팀 우위
                model_prefers_home = (home_win_pct >= away_win_pct)
                market_prefers_home = (_edge_val >= 0)  # edge = model_home - kalshi_home ≥ 0 → 모델이 홈 선호, kalshi도 홈 선호하면 edge 작음
                # kalshi_home > 50 → 시장도 홈 우위
                kalshi_home_prob = vb.get("kalshi_prob") or 50.0
                market_prefers_home = (kalshi_home_prob >= 50.0)
                consensus = (model_prefers_home == market_prefers_home)
                if consensus and not vb.get("value_bet", "").startswith(("⏭️", "⚠️")):
                    pred_team = home_name if model_prefers_home else away_name
                    vb["consensus"] = True
                    vb["value_bet"] = f"⭐ 고신뢰 예측 — 모델·시장 동의 ({pred_team} {_model_top:.0f}%)"
                    print(f"    [⭐ 고신뢰] 모델({_model_top:.0f}%)·시장(Kalshi {kalshi_home_prob:.0f}%) 동시 동의 → 고신뢰 예측")
            if "consensus" not in vb:
                vb["consensus"] = False

        # ── 11. 부상 노트 ─────────────────────────────────────────────
        notes = _safe(lambda: get_injury_notes(away_id, home_id, away_name, home_name), "", "부상체크")

        # ── 12. 선발투수 표시용 시즌 스탯 ────────────────────────────
        away_pitcher_stats = _safe(lambda p=away_pitcher_id: _pitcher_display_stats(p),
                                   {"wins": None, "losses": None, "era": None}, "원정 투수시즌스탯")
        home_pitcher_stats = _safe(lambda p=home_pitcher_id: _pitcher_display_stats(p),
                                   {"wins": None, "losses": None, "era": None}, "홈 투수시즌스탯")

        # ── 12. 모델 적중 판정 ────────────────────────────────────────
        actual_winner = g.get("actual_winner")
        model_winner  = home_name if home_win_pct >= away_win_pct else away_name
        model_correct = (model_winner == actual_winner) if actual_winner else None

        # ── 13. 팀 순위 정보 ──────────────────────────────────────────
        def _team_standing(team_id):
            s = standings_map.get(team_id, {})
            return {
                "div_rank":   s.get("div_rank"),
                "div_name":   s.get("div_name", ""),
                "wins":       s.get("wins"),
                "losses":     s.get("losses"),
                "games_back": s.get("games_back", "-"),
            }

        if vb.get("extreme_edge"):
            print(f"    [🚨 극단적 edge] {vb['edge']:+.1f}%p — 칼시 마켓 데이터 이상 의심, 실제 배당 직접 확인 권장")
        print(f"    Value Bet: {vb['value_bet']}\n")

        results.append({
            "date":         game_date,
            "status":       g.get("status", ""),
            "away":         away_name,
            "home":         home_name,
            "away_standing":   _team_standing(away_id),
            "home_standing":   _team_standing(home_id),
            "away_recent_form": _recent_form(away_id, game_date),
            "home_recent_form": _recent_form(home_id, game_date),
            "away_pitcher": away_pitcher,
            "away_pitcher_stats": away_pitcher_stats,
            "away_pitcher_gamelog": _pitcher_gamelog_summary(away_gl if away_gl is not None else []),
            "home_pitcher": home_pitcher,
            "home_pitcher_stats": home_pitcher_stats,
            "home_pitcher_gamelog": _pitcher_gamelog_summary(home_gl if home_gl is not None else []),
            "pred_model":   "scorecard",
            "win_prob": {
                "away": away_win_pct,
                "home": home_win_pct,
            },
            "expected_score": {
                "away": exp_away,
                "home": exp_home,
            },
            "blend_detail":          None,
            "ml_mc_agree":           None,
            "ml_mc_conflict_level":  None,
            "scorecard": {
                "bat_source":       bat_source,
                "away_handedness":  away_handedness,
                "home_handedness":  home_handedness,
                "bullpen_game":     both_cold,
                "any_cold_sp":      any_cold,
                "eff_weights": {
                    "sp": eff_sp_w, "bp": eff_bp_w,
                    "bat": eff_bat_w, "sit": eff_sit_w,
                },
                "away": {
                    "sp_score":   away_sp_s,
                    "sp_detail":  away_sp_detail,
                    "bp_score":   away_bp_s,
                    "bp_detail":  away_bp_detail,
                    "bat_score":  away_bat_s,
                    "bat_detail": away_bat_detail,
                    "sit_score":  away_sit_s,
                    "total":      sc["away_total"],
                },
                "home": {
                    "sp_score":   home_sp_s,
                    "sp_detail":  home_sp_detail,
                    "bp_score":   home_bp_s,
                    "bp_detail":  home_bp_detail,
                    "bat_score":  home_bat_s,
                    "bat_detail": home_bat_detail,
                    "sit_score":  home_sit_s,
                    "total":      sc["home_total"],
                },
            },
            "scores": {
                "away_offense": away_bat_s,
                "away_defense": round(away_def_s, 1),
                "home_offense": home_bat_s,
                "home_defense": round(home_def_s, 1),
            },
            "actual_score": {
                "away": g.get("actual_away"),
                "home": g.get("actual_home"),
            },
            "lineup_confirmed": bat_source == "lineup",
            "sp_tbd": {
                "away": away_is_tbd,
                "home": home_is_tbd,
                "both": away_is_tbd and home_is_tbd,
                "any":  away_is_tbd or home_is_tbd,
            },
            "actual_winner":  actual_winner,
            "model_winner":   model_winner,
            "model_correct":  model_correct,
            "notes":          notes or "",
            "kalshi_prob":    vb["kalshi_prob"],
            "edge":           vb["edge"],
            "value_bet":      vb["value_bet"],
            "extreme_edge":   vb.get("extreme_edge", False),
            "consensus":      vb.get("consensus", False),
        })

    # ── 저장 ──────────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_payload = json.dumps(results, ensure_ascii=False, indent=2)
    (OUTPUT_DIR / "predictions.json").write_text(json_payload, encoding="utf-8")
    (OUTPUT_DIR / f"predictions_{game_date}.js").write_text(
        f"// Auto-generated (scorecard) — {game_date}\nwindow.PREDICTIONS_DATA = {json_payload};\n",
        encoding="utf-8"
    )
    print(f"저장 완료 ({len(results)}경기)")
    return results


if __name__ == "__main__":
    import sys as _sys
    d = _sys.argv[1] if len(_sys.argv) > 1 else None
    run(d)
