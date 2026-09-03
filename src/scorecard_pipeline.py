"""
라인업 기반 스코어카드 예측 파이프라인

기존 ML+MC 이중 모델 대신 단일 스코어카드:
  - 선발투수 최근 성적 (30%)
  - 불펜 최근 성적 (20%)
  - 팀 타선 최근 성적 (35%)
  - 상황적 요소 (15%)

버전 이력:
  v12 (2026-09-01) — BAT 극단값 캡, SP↔BAT+Kalshi역방향 자동 패스,
                      라인업 미확정 패널티 -3%p, 3중 패스 조건 추가
  v11 (2026-08-29) — 난타전 감지 로직, SP 바닥값 하향, 트위터 280자 수정
  v10 (2026-08-27) — Season Accuracy / Blog 자동 업데이트 통합
  v9  (2026-08-27) — SP↔BAT 충돌 감지 플래그, Value Bet 기준 정교화
  v6  (2026-08-21) — 홈팀 박빙 보정, 부상자 핵심도 구분, Kalshi 괴리 보정
"""

MODEL_VERSION = "v12"
import json
import sys
from pathlib import Path
from datetime import date
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from mlb_schedule         import get_games
from mlb_stats_fetcher    import (get_team_hitting_log, get_team_hitting_log_split,
                                   get_team_pitching_log,
                                   get_pitcher_gamelog, get_pitcher_season,
                                   get_standings_map, get_bullpen_era_direct,
                                   get_recent_home_away_wpct)
from pitcher_recent_score import analyze_pitcher_recent, pitcher_score, _default_pitcher, get_pitcher_arsenal
from bullpen_score        import bullpen_score, _default_bullpen
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
from injury_check         import get_injury_notes, get_injury_penalty
import requests as _requests

OUTPUT_DIR  = Path(__file__).parent.parent / "output"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "weights.json"


def _calc_hitting_stats_simple(logs: list) -> dict:
    """게임로그 리스트 → 간단한 타격 통계 (split UI 표시용)"""
    n = len(logs)
    if n == 0:
        return {"recent_avg": 0.250, "runs_per_g": 0.0, "hr_per_g": 0.0, "n_games": 0}
    total_r = total_h = total_ab = total_hr = 0
    for s in logs:
        total_r  += int(s.get("runs", 0)     or 0)
        total_h  += int(s.get("hits", 0)     or 0)
        total_ab += int(s.get("atBats", 0)   or 0)
        total_hr += int(s.get("homeRuns", 0) or 0)
    return {
        "recent_avg": round(total_h / total_ab, 3) if total_ab > 0 else 0.250,
        "runs_per_g": round(total_r / n, 1),
        "hr_per_g":   round(total_hr / n, 2),
        "n_games":    n,
    }


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
    COORS_HOME_BAT_EXTRA    = float(sc_cfg.get("coors_home_bat_extra",    5.0))   # 홈팀 추가 보정
    HARD_CAP_OPP_THRESHOLD  = float(sc_cfg.get("hard_cap_opp_threshold", 50.0))
    HARD_CAP_REDUCED        = float(sc_cfg.get("hard_cap_reduced",       60.0))
    # 동적 차별화 캡: 점수 차이(raw_score_gap)에 따라 캡을 단계적으로 적용
    # 형식: [{"min_gap": 25, "cap": 65}, {"min_gap": 15, "cap": 63}, ...]  (내림차순 정렬 필요)
    _raw_tiers = sc_cfg.get("dynamic_cap_tiers", [])
    DYNAMIC_CAP_TIERS = sorted(
        [{"min_gap": float(t["min_gap"]), "cap": float(t["cap"])} for t in _raw_tiers],
        key=lambda x: x["min_gap"], reverse=True
    ) if _raw_tiers else []
    # ── 타선 점수 하한선 (방법 C: 순위 기반 티어 + 시즌 블렌딩) ──────────
    _bat_floor_cfg          = sc_cfg.get("bat_floor", {})
    BAT_FLOOR_ENABLED       = bool(_bat_floor_cfg.get("enabled", True))
    BAT_FLOOR_TRIGGER       = float(_bat_floor_cfg.get("trigger_below",    25.0))
    BAT_FLOOR_BLEND_RATIO   = float(_bat_floor_cfg.get("season_blend_ratio", 0.40))
    _tier_floors            = _bat_floor_cfg.get("tier_floors", {})
    BAT_FLOOR_TOP           = float(_tier_floors.get("top",    35.0))
    BAT_FLOOR_MID           = float(_tier_floors.get("mid",    25.0))
    BAT_FLOOR_BOT           = float(_tier_floors.get("bottom", 15.0))
    BAT_FLOOR_TOP_WPCT      = float(_bat_floor_cfg.get("top_win_pct",    0.560))
    BAT_FLOOR_BOT_WPCT      = float(_bat_floor_cfg.get("bottom_win_pct", 0.440))

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

    def _pitcher_gamelog_summary(gl: list, n: int = 10) -> list:
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
            wins   = int(float(s.get("wins", 0) or 0))
            losses = int(float(s.get("losses", 0) or 0))
            decision = "W" if wins > 0 else ("L" if losses > 0 else None)
            out.append({
                "date":     s.get("_game_date", ""),
                "opp":      opp_label,
                "decision": decision,
                "ip":       s.get("inningsPitched", "0"),
                "er":       er,
                "h":        h,
                "bb":       bb,
                "so":       so,
                "era":      era_g,
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

    # ── 기존 예측 로드 (lineup_confirmed 경기 재계산 방지) ─────────────────
    _existing_preds: dict = {}  # key: game_pk → 기존 예측 dict
    _existing_output = OUTPUT_DIR / "predictions.json"
    if _existing_output.exists():
        try:
            _ex_data = json.loads(_existing_output.read_text(encoding="utf-8"))
            for _ex in _ex_data:
                if _ex.get("date") == game_date and _ex.get("game_pk"):
                    _existing_preds[_ex["game_pk"]] = _ex
        except Exception:
            pass

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

        # ── 🔒 라인업 확정 경기: 기존 예측 재사용 (승률 고정) ─────────────
        _game_pk = g.get("gamePk")
        _ex = _existing_preds.get(_game_pk) if _game_pk else None
        if _ex and _ex.get("lineup_confirmed") and _ex.get("actual_score", {}).get("away") is None:
            # 경기 결과는 아직 없고, 라인업은 이미 확정된 경기 → 예측 고정
            # actual_score / actual_winner / model_correct 만 최신 스케줄 데이터로 갱신
            _ex_copy = dict(_ex)
            _ex_copy["actual_score"] = {
                "away": g.get("actual_away"),
                "home": g.get("actual_home"),
            }
            _actual_w = g.get("actual_away")
            _actual_h = g.get("actual_home")
            if _actual_w is not None and _actual_h is not None:
                _aw = _ex_copy.get("away", "")
                _hw = _ex_copy.get("home", "")
                _ex_copy["actual_winner"] = _aw if _actual_w > _actual_h else (_hw if _actual_h > _actual_w else "무승부")
                _ex_copy["model_correct"] = (_ex_copy["actual_winner"] == _ex_copy.get("model_winner"))
            print(f"    [🔒 예측고정] 라인업 확정 완료 — 승률 {_ex_copy['win_prob']['away']}% vs {_ex_copy['win_prob']['home']}% 유지")
            results.append(_ex_copy)
            continue

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

        # ── 투수 아스날 (나이 · 구속 · 구종) ────────────────────────
        if away_pitcher_id and not away_is_tbd:
            _aw_arsenal = _safe(lambda p=away_pitcher_id: get_pitcher_arsenal(p), {}, "원정투수아스날")
            if _aw_arsenal:
                away_sp_detail.update(_aw_arsenal)
        if home_pitcher_id and not home_is_tbd:
            _hm_arsenal = _safe(lambda p=home_pitcher_id: get_pitcher_arsenal(p), {}, "홈투수아스날")
            if _hm_arsenal:
                home_sp_detail.update(_hm_arsenal)

        # 시즌 ERA 가져오기 (하한선 보정용)
        away_season_era = None
        home_season_era = None
        away_season_wins = away_season_losses = None
        home_season_wins = home_season_losses = None
        if away_pitcher_id and not away_is_tbd:
            _aw_s = _safe(lambda p=away_pitcher_id: get_pitcher_season(p), {}, "원정선발시즌ERA")
            _aw_era = _aw_s.get("era")
            try: away_season_era = float(_aw_era) if _aw_era else None
            except: pass
            try: away_season_wins   = int(_aw_s.get("wins",   0) or 0)
            except: pass
            try: away_season_losses = int(_aw_s.get("losses", 0) or 0)
            except: pass
        if home_pitcher_id and not home_is_tbd:
            _hm_s = _safe(lambda p=home_pitcher_id: get_pitcher_season(p), {}, "홈선발시즌ERA")
            _hm_era = _hm_s.get("era")
            try: home_season_era = float(_hm_era) if _hm_era else None
            except: pass
            try: home_season_wins   = int(_hm_s.get("wins",   0) or 0)
            except: pass
            try: home_season_losses = int(_hm_s.get("losses", 0) or 0)
            except: pass

        away_sp_s = TBD_SCORE if away_is_tbd else pitcher_score(
            away_sp_detail, season_era=away_season_era,
            season_wins=away_season_wins, season_losses=away_season_losses)
        home_sp_s = TBD_SCORE if home_is_tbd else pitcher_score(
            home_sp_detail, season_era=home_season_era,
            season_wins=home_season_wins, season_losses=home_season_losses)

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
            velo = sp.get("fb_velo")
            if velo is not None and velo < 92.0:
                if velo < 88.0:
                    notes.append(f"저속{velo}mph(-6pt)")
                elif velo < 90.0:
                    notes.append(f"저속{velo}mph(-4pt)")
                else:
                    notes.append(f"저속{velo}mph(-3pt)")
            return (" [" + "/".join(notes) + "]") if notes else ""

        print(f"    [선발] {away_name} {away_pitcher}{tbd_tag_away}: ERA {away_sp_detail['era']} WHIP {away_sp_detail['whip']} trend={away_sp_detail['trend']}{_sp_note(away_sp_detail)} → {away_sp_s}점")
        print(f"    [선발] {home_name} {home_pitcher}{tbd_tag_home}: ERA {home_sp_detail['era']} WHIP {home_sp_detail['whip']} trend={home_sp_detail['trend']}{_sp_note(home_sp_detail)} → {home_sp_s}점")

        # ── 1b. 오프너 감지 — avg_ip < 3.0이면 SP 스코어 ×0.7 디스카운트 ──
        # 근거: BOS G2 사례 — Alec Gamboa(릴리버 선발)가 Max Fried와 동급 SP스코어(49.8)로
        #       처리됨. 오프너/릴리버 기용 시 실질적 선발 역할을 못하므로 SP점수 하향.
        #       avg_ip가 3이닝 미만이면 오프너 패턴으로 간주.
        # 임계값 2.0: Gamboa(1.8) 같은 진짜 오프너는 잡고
        # Lynch(2.0 경계), Bachar(2.1) 같은 조기강판 선발은 제외
        OPENER_IP_THRESHOLD = 2.0
        OPENER_SP_DISCOUNT  = 0.70   # ×70% → 30% 디스카운트
        if not away_is_tbd and away_sp_detail.get("avg_ip", 6.0) < OPENER_IP_THRESHOLD:
            orig = away_sp_s
            away_sp_s = round(away_sp_s * OPENER_SP_DISCOUNT, 1)
            print(f"    [🔓 오프너감지] {away_name} {away_pitcher} avg_ip={away_sp_detail.get('avg_ip'):.1f} < {OPENER_IP_THRESHOLD} → SP {orig}→{away_sp_s}점 (-30%)")
        if not home_is_tbd and home_sp_detail.get("avg_ip", 6.0) < OPENER_IP_THRESHOLD:
            orig = home_sp_s
            home_sp_s = round(home_sp_s * OPENER_SP_DISCOUNT, 1)
            print(f"    [🔓 오프너감지] {home_name} {home_pitcher} avg_ip={home_sp_detail.get('avg_ip'):.1f} < {OPENER_IP_THRESHOLD} → SP {orig}→{home_sp_s}점 (-30%)")

        # ── 2. 불펜 분석 (Option 2: 실제 불펜 투수 시즌 ERA 직접 계산) ──
        away_bp_detail = _safe(lambda aid=away_id: get_bullpen_era_direct(aid), _default_bullpen(), "원정 불펜ERA")
        home_bp_detail = _safe(lambda hid=home_id: get_bullpen_era_direct(hid), _default_bullpen(), "홈 불펜ERA")

        away_bp_s = bullpen_score(away_bp_detail)
        home_bp_s = bullpen_score(home_bp_detail)

        # ── 수요일 불펜 소진 패널티 ───────────────────────────────────────
        if is_wednesday and WED_ENABLED:
            away_bp_s = max(0.0, away_bp_s - WED_BP_PENALTY)
            home_bp_s = max(0.0, home_bp_s - WED_BP_PENALTY)
            print(f"    [수요일패널티] 불펜 각 -{WED_BP_PENALTY}pt 적용 (시리즈 마지막날 소진 리스크)")

        def _bp_label(detail):
            era = detail.get('bullpen_era', 4.00)
            recent = detail.get('recent_era', era)
            apps = detail.get('recent_appearances', 0)
            cnt = detail.get('bp_count', 0)
            fatigue = f" ⚡피로({apps}회)" if apps >= 5 else (f" ({apps}회)" if apps > 0 else "")
            if abs(recent - era) >= 0.5:
                return f"ERA {era}(최근7일:{recent}){fatigue} ({cnt}명)"
            return f"ERA {era}{fatigue} ({cnt}명)"
        print(f"    [불펜] {away_name}: {_bp_label(away_bp_detail)} → {away_bp_s}점 | {home_name}: {_bp_label(home_bp_detail)} → {home_bp_s}점")

        # 클로저 정보 로그 출력
        for _team_name, _bp_detail in [(away_name, away_bp_detail), (home_name, home_bp_detail)]:
            _c_name = _bp_detail.get("closer_name")
            _c_era  = _bp_detail.get("closer_era")
            if _c_name is not None and _c_era is not None:
                print(f"    [클로저] {_c_name}: ERA {_c_era} → 불펜점수 반영")

        # ── 3. 타선 분석 ─────────────────────────────────────────────
        #   우선순위: ① 확정 라인업+시즌OPS  ② 전날 라인업+LHP/RHP스플릿  ③ 팀통계
        # 홈/원정 분리 로그 (예측 정확도 + UI 표시용)
        away_hit_split = _safe(lambda aid=away_id: get_team_hitting_log_split(aid, 10),
                               {"home": [], "away": [], "all": []}, "원정 타격로그(split)")
        home_hit_split = _safe(lambda hid=home_id: get_team_hitting_log_split(hid, 10),
                               {"home": [], "away": [], "all": []}, "홈 타격로그(split)")
        # 기존 호환용: 전체 최근 10경기 (lineup_batting 함수에 전달)
        away_hit_log = away_hit_split.get("all") or _safe(lambda aid=away_id: get_team_hitting_log(aid, 10), [], "원정 타격로그")
        home_hit_log = home_hit_split.get("all") or _safe(lambda hid=home_id: get_team_hitting_log(hid, 10), [], "홈 타격로그")

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
                # ③ 팀 통계 fallback — 원정팀은 원정 10경기, 홈팀은 홈 10경기 사용
                bat_source = "team_stats"
                away_split_logs = away_hit_split.get("away") or away_hit_log
                home_split_logs = home_hit_split.get("home") or home_hit_log
                away_bat_detail = analyze_batting(away_split_logs, {}, split_logs=away_hit_split)
                home_bat_detail = analyze_batting(home_split_logs, {}, split_logs=home_hit_split)
                print(f"    [타선 ⏳팀통계] 라인업 미공개 → 원정팀 원정10경기 / 홈팀 홈10경기 사용")

        # ── split 데이터를 bat_detail에 보강 (라인업 모드도 포함) ──────
        if "home_split" not in away_bat_detail:
            hs = away_hit_split.get("home", [])
            as_ = away_hit_split.get("away", [])
            if hs:
                away_bat_detail["home_split"] = _calc_hitting_stats_simple(hs)
            if as_:
                away_bat_detail["away_split"] = _calc_hitting_stats_simple(as_)
        if "home_split" not in home_bat_detail:
            hs = home_hit_split.get("home", [])
            as_ = home_hit_split.get("away", [])
            if hs:
                home_bat_detail["home_split"] = _calc_hitting_stats_simple(hs)
            if as_:
                home_bat_detail["away_split"] = _calc_hitting_stats_simple(as_)

        away_bat_s = batting_score(away_bat_detail)
        home_bat_s = batting_score(home_bat_detail)

        # ── 3.4 타선/불펜 점수 극단값 캡 ────────────────────────────────
        # BAT 70pt↑ : 시즌 최상위팀도 평균 60pt 미만 → 이상값 가중치 왜곡 방지 (CHC 79pt 사례)
        # BP  75pt↑ : 소수 구원투수 샘플로 ERA가 극단적으로 낮을 때 과신 방지 (TB 70pt → NYM에 패)
        BAT_EXTREME_CAP = 70.0
        BP_EXTREME_CAP  = 75.0
        if away_bat_s > BAT_EXTREME_CAP:
            print(f"    [⚠️ 타선극단값캡] {away_name} bat={away_bat_s:.1f}pt → {BAT_EXTREME_CAP}pt로 제한")
            away_bat_s = BAT_EXTREME_CAP
        if home_bat_s > BAT_EXTREME_CAP:
            print(f"    [⚠️ 타선극단값캡] {home_name} bat={home_bat_s:.1f}pt → {BAT_EXTREME_CAP}pt로 제한")
            home_bat_s = BAT_EXTREME_CAP
        if away_bp_s > BP_EXTREME_CAP:
            print(f"    [⚠️ 불펜극단값캡] {away_name} bp={away_bp_s:.1f}pt → {BP_EXTREME_CAP}pt로 제한 (소샘플 과신 방지)")
            away_bp_s = BP_EXTREME_CAP
        if home_bp_s > BP_EXTREME_CAP:
            print(f"    [⚠️ 불펜극단값캡] {home_name} bp={home_bp_s:.1f}pt → {BP_EXTREME_CAP}pt로 제한 (소샘플 과신 방지)")
            home_bp_s = BP_EXTREME_CAP

        # ── 3.5 부상 페널티 (타자 주전 부상자 → 타선 점수 차감) ─────────
        inj_penalty = _safe(lambda: get_injury_penalty(away_id, home_id),
                            {"away_penalty": 0.0, "home_penalty": 0.0,
                             "away_detail": [], "home_detail": []}, "부상페널티")
        if inj_penalty["away_penalty"] > 0:
            n_away_inj = len(inj_penalty["away_detail"])
            away_bat_s = max(0.0, away_bat_s - inj_penalty["away_penalty"])
            print(f"    [부상페널티] {away_name} 타자주전 {n_away_inj}명 → 타선 -{inj_penalty['away_penalty']}pt")
        if inj_penalty["home_penalty"] > 0:
            n_home_inj = len(inj_penalty["home_detail"])
            home_bat_s = max(0.0, home_bat_s - inj_penalty["home_penalty"])
            print(f"    [부상페널티] {home_name} 타자주전 {n_home_inj}명 → 타선 -{inj_penalty['home_penalty']}pt")

        # ── 3.7 타선 점수 하한선 보정 (방법 C: 시즌 블렌딩 + 순위 티어 플로어) ──
        # 최근 10경기 슬럼프로 타선이 극단적으로 낮게 계산될 때 보정
        # 1단계: 시즌 OPS 기반 시즌 타선 점수 추정 후 블렌딩
        # 2단계: 팀 승률 기반 티어 하한선으로 최소값 보장
        def _apply_bat_floor(bat_s: float, bat_detail: dict, st: dict, team_name: str) -> float:
            if not BAT_FLOOR_ENABLED or bat_s >= BAT_FLOOR_TRIGGER:
                return bat_s

            # 시즌 OPS → 시즌 타선 점수 추정 (batting_score와 같은 스케일)
            s_ops  = float(bat_detail.get("season_ops", 0.720) or 0.720)
            s_slg  = float(bat_detail.get("season_slg", 0.400) or 0.400)
            s_avg  = float(bat_detail.get("season_avg", 0.250) or 0.250)
            ops_s  = max(0.0, min(100.0, (s_ops - 0.600) / 0.350 * 100.0))
            slg_s  = max(0.0, min(100.0, (s_slg - 0.300) / 0.300 * 100.0))
            avg_s  = max(0.0, min(100.0, (s_avg - 0.200) / 0.160 * 100.0))
            season_bat_s = ops_s * 0.40 + slg_s * 0.30 + avg_s * 0.30

            # 1단계: recent 60% + season 40% 블렌딩
            blended = bat_s * (1.0 - BAT_FLOOR_BLEND_RATIO) + season_bat_s * BAT_FLOOR_BLEND_RATIO

            # 2단계: 팀 승률로 티어 결정 → 최소 하한선 보장
            win_pct = (st.get("wins", 65) / max(st.get("games_played", 130), 1))
            if win_pct >= BAT_FLOOR_TOP_WPCT:
                floor = BAT_FLOOR_TOP
                tier  = "상위권"
            elif win_pct <= BAT_FLOOR_BOT_WPCT:
                floor = BAT_FLOOR_BOT
                tier  = "하위권"
            else:
                floor = BAT_FLOOR_MID
                tier  = "중위권"

            final = max(blended, floor)
            if final > bat_s:
                print(f"    [🛡️ 타선하한선] {team_name} {bat_s:.1f}pt → {final:.1f}pt "
                      f"(시즌블렌딩{blended:.1f}+{tier}하한{floor}pt, 승률{win_pct:.3f})")
            return round(final, 1)

        away_st = standings_map.get(away_id, {})
        home_st = standings_map.get(home_id, {})
        away_bat_s = _apply_bat_floor(away_bat_s, away_bat_detail, away_st, away_name)
        home_bat_s = _apply_bat_floor(home_bat_s, home_bat_detail, home_st, home_name)

        away_split_label = f"원정{len(away_hit_split.get('away',[]))}경기" if away_hit_split.get("away") else "전체10경기"
        home_split_label = f"홈{len(home_hit_split.get('home',[]))}경기"  if home_hit_split.get("home")  else "전체10경기"
        def _trend_label(detail):
            t = detail.get("bat_trend", "stable")
            if t == "hot":   return " 🔥타선hot"
            if t == "cold":  return " 🥶타선cold"
            return ""
        print(f"    [타선] {away_name}({away_split_label}): avg {away_bat_detail['recent_avg']:.3f} OPS {away_bat_detail['season_ops']:.3f} 득점 {away_bat_detail['runs_per_g']}{_trend_label(away_bat_detail)} → {away_bat_s}점")
        print(f"    [타선] {home_name}({home_split_label}): avg {home_bat_detail['recent_avg']:.3f} OPS {home_bat_detail['season_ops']:.3f} 득점 {home_bat_detail['runs_per_g']}{_trend_label(home_bat_detail)} → {home_bat_s}점")

        # ── 4. 상황 분석 ──────────────────────────────────────────────
        # (away_st, home_st 는 3.7 타선하한선 보정에서 이미 정의됨)

        # 최근 15경기 홈/원정 승률 (시즌 전체와 50:50 블렌딩)
        # 시즌 초 홈 강팀이 최근 부진해도 과대평가되는 문제 방지
        away_recent_wpct = _safe(lambda aid=away_id: get_recent_home_away_wpct(aid, 15), {}, "원정최근승률")
        home_recent_wpct = _safe(lambda hid=home_id: get_recent_home_away_wpct(hid, 15), {}, "홈최근승률")

        season_home_wpct = home_st.get("home_wpct", 0.523)
        season_away_wpct = away_st.get("away_wpct", 0.477)
        recent_home_wpct = home_recent_wpct.get("home_wpct")
        recent_away_wpct = away_recent_wpct.get("away_wpct")

        # 최근 데이터 충분 시 시즌 50% + 최근 50% 블렌딩
        blended_home_wpct = (season_home_wpct * 0.5 + recent_home_wpct * 0.5) if recent_home_wpct is not None else season_home_wpct
        blended_away_wpct = (season_away_wpct * 0.5 + recent_away_wpct * 0.5) if recent_away_wpct is not None else season_away_wpct

        away_sit_s = situational_score(
            is_home=False,
            streak=away_st.get("streak_wins", 0),
            div_rank=away_st.get("div_rank"),
            wins=away_st.get("wins"),
            losses=away_st.get("losses"),
            away_wpct=blended_away_wpct,
        )
        home_sit_s = situational_score(
            is_home=True,
            streak=home_st.get("streak_wins", 0),
            div_rank=home_st.get("div_rank"),
            wins=home_st.get("wins"),
            losses=home_st.get("losses"),
            home_wpct=blended_home_wpct,
        )
        home_wpct_val = blended_home_wpct
        away_wpct_val = blended_away_wpct
        recent_tag = f" [최근:{recent_home_wpct:.3f}]" if recent_home_wpct else ""
        print(f"    [상황] {away_name}: {away_sit_s}점 (streak {away_st.get('streak_wins',0):+d}, 원정승률 {away_wpct_val:.3f}) | {home_name}: {home_sit_s}점 (streak {home_st.get('streak_wins',0):+d}, 홈승률 {home_wpct_val:.3f}{recent_tag})")

        # ── 4.5 쿠어스 필드 보정 (COL 홈경기) ────────────────────────
        is_coors = ("Colorado" in home_name or "Rockies" in home_name)
        if is_coors:
            away_sp_s  = max(0.0, away_sp_s  - COORS_SP_PENALTY)
            home_sp_s  = max(0.0, home_sp_s  - COORS_SP_PENALTY)
            away_bat_s = min(100.0, away_bat_s + COORS_BAT_BONUS)
            home_bat_s = min(100.0, home_bat_s + COORS_BAT_BONUS + COORS_HOME_BAT_EXTRA)
            print(f"    [쿠어스] COL 홈경기: SP -{COORS_SP_PENALTY}pt | 원정타선 +{COORS_BAT_BONUS}pt | 홈타선 +{COORS_BAT_BONUS+COORS_HOME_BAT_EXTRA}pt (홈이점 추가)")

        # ── 4.7 선발 ERA 위험 플래그 보정 ────────────────────────────
        # 선발 ERA 5.0↑ + 상대 타선 60점↑ → 상대 타선 추가 보너스 (대량 실점 위험)
        # ERA None → 5.00 (보수적 기본값, pitcher_score와 동일 기준)
        away_sp_era = away_sp_detail.get("era") or 5.00
        home_sp_era = home_sp_detail.get("era") or 5.00

        if away_sp_era >= SP_ERA_RISK_THRESHOLD and home_bat_s >= SP_ERA_RISK_BAT_MIN:
            home_bat_s = min(100.0, home_bat_s + SP_ERA_RISK_BAT_BONUS)
            print(f"    [ERA위험] {away_name} 선발 ERA {away_sp_era} → {home_name} 타선 +{SP_ERA_RISK_BAT_BONUS}pt")
        if home_sp_era >= SP_ERA_RISK_THRESHOLD and away_bat_s >= SP_ERA_RISK_BAT_MIN:
            away_bat_s = min(100.0, away_bat_s + SP_ERA_RISK_BAT_BONUS)
            print(f"    [ERA위험] {home_name} 선발 ERA {home_sp_era} → {away_name} 타선 +{SP_ERA_RISK_BAT_BONUS}pt")

        # ── [Option C] 타선-투수 교차 보정 ────────────────────────────
        # 타선이 약한 팀(bat < 30)은 투수진 점수를 비례 감산
        # "타선 없이 투수만으로는 이길 수 없다" — 야구 현실 반영
        BAT_DEF_CROSS_THRESHOLD = 30.0
        BAT_DEF_EFF_FLOOR       = 0.80   # bat=0일 때 투수 80% 효율

        def _def_eff(bat_s: float) -> float:
            if bat_s >= BAT_DEF_CROSS_THRESHOLD:
                return 1.0
            return BAT_DEF_EFF_FLOOR + (1.0 - BAT_DEF_EFF_FLOOR) * (bat_s / BAT_DEF_CROSS_THRESHOLD)

        away_eff = _def_eff(away_bat_s)
        home_eff = _def_eff(home_bat_s)

        if away_eff < 1.0:
            orig_away_sp_s, orig_away_bp_s = away_sp_s, away_bp_s
            away_sp_s = round(away_sp_s * away_eff, 1)
            away_bp_s = round(away_bp_s * away_eff, 1)
            print(f"    [교차보정C] {away_name} bat={away_bat_s:.1f} eff={away_eff:.3f} | SP {orig_away_sp_s}→{away_sp_s} BP {orig_away_bp_s}→{away_bp_s}")
        if home_eff < 1.0:
            orig_home_sp_s, orig_home_bp_s = home_sp_s, home_bp_s
            home_sp_s = round(home_sp_s * home_eff, 1)
            home_bp_s = round(home_bp_s * home_eff, 1)
            print(f"    [교차보정C] {home_name} bat={home_bat_s:.1f} eff={home_eff:.3f} | SP {orig_home_sp_s}→{home_sp_s} BP {orig_home_bp_s}→{home_bp_s}")

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

        # ── 타선 격차 민감도 보정 ─────────────────────────────────────
        # 타선 점수 차이가 클수록 타선 가중치를 상향 조정
        # (ATL@MIN 8/18: MIN 39.2 vs ATL 20.9 → 18.3p 차이 무시됨 → 개선)
        # 기준: 격차 15p 이상이면 타선 가중치를 SP 가중치에서 이전해 상향
        #
        #   격차 15~19p: bat_w +0.03 (SP에서 차감)
        #   격차 20p 이상: bat_w +0.05 (SP에서 차감)
        # ── 난타전 감지 (High-Scoring Game Detection) ────────────────
        # 양팀 타선 점수가 모두 높을 때 → SP 의존도 낮추고 타선/불펜 비중 상향
        # 근거: CIN@CHC(10-8) 같은 난타전은 선발 성적보다 타선 화력이 결과 결정
        #
        # 조건: min(away_bat_s, home_bat_s) ≥ 기준치
        #   ≥ 35p (강한 난타전) : SP -0.07, BAT +0.05, BP +0.02
        #   ≥ 30p (일반 난타전) : SP -0.05, BAT +0.03, BP +0.02
        # SP 하한 0.20 보장 (과도한 축소 방지)
        SLUGFEST_LG = 35.0   # 강한 난타전 기준
        SLUGFEST_SM = 30.0   # 일반 난타전 기준
        min_bat = min(away_bat_s, home_bat_s)
        avg_bat = (away_bat_s + home_bat_s) / 2.0

        if min_bat >= SLUGFEST_LG:
            sp_cut  = min(0.07, max(0.0, eff_sp_w - 0.20))
            bat_add = round(sp_cut * 0.70, 2)   # SP 삭감분의 70%를 타선으로
            bp_add  = round(sp_cut * 0.30, 2)   # 나머지 30%를 불펜으로
            eff_sp_w  = round(eff_sp_w  - sp_cut,  2)
            eff_bat_w = round(eff_bat_w + bat_add, 2)
            eff_bp_w  = round(eff_bp_w  + bp_add,  2)
            print(f"    [⚾ 난타전감지-강] 양팀 타선 min={min_bat:.1f}p ≥ {SLUGFEST_LG}p "
                  f"→ SP {eff_sp_w+sp_cut:.0%}→{eff_sp_w:.0%} "
                  f"BAT {eff_bat_w-bat_add:.0%}→{eff_bat_w:.0%} "
                  f"BP {eff_bp_w-bp_add:.0%}→{eff_bp_w:.0%}")
        elif min_bat >= SLUGFEST_SM:
            sp_cut  = min(0.05, max(0.0, eff_sp_w - 0.20))
            bat_add = round(sp_cut * 0.60, 2)
            bp_add  = round(sp_cut * 0.40, 2)
            eff_sp_w  = round(eff_sp_w  - sp_cut,  2)
            eff_bat_w = round(eff_bat_w + bat_add, 2)
            eff_bp_w  = round(eff_bp_w  + bp_add,  2)
            print(f"    [⚾ 난타전감지] 양팀 타선 min={min_bat:.1f}p ≥ {SLUGFEST_SM}p "
                  f"→ SP {eff_sp_w+sp_cut:.0%}→{eff_sp_w:.0%} "
                  f"BAT {eff_bat_w-bat_add:.0%}→{eff_bat_w:.0%} "
                  f"BP {eff_bp_w-bp_add:.0%}→{eff_bp_w:.0%}")

        # ── 타선 격차 민감도 보정 ─────────────────────────────────────
        # KC@CLE 사례: KC bat 47.9 vs CLE bat 26.0 (21.9pt) → 모델이 CLE픽(SP/BP우세)
        # 타선 격차가 크면 타선 가중치를 SP에서 이전해 상향
        #
        #   격차 18~21p: bat_w +0.07 (SP에서 차감) → eff_bat ~42%
        #   격차 22p+ : bat_w +0.10 (SP에서 차감) → eff_bat ~45%
        #   격차 15~17p: bat_w +0.03 (기존 유지)
        # SP 하한 0.20 보장
        bat_gap = abs(away_bat_s - home_bat_s)
        BAT_GAP_SM  = 15.0   # 소폭 격차
        BAT_GAP_MD  = 18.0   # 중간 격차 (신규) — 압도 시작
        BAT_GAP_LG  = 22.0   # 대폭 격차 (신규) — 완전 압도
        if bat_gap >= BAT_GAP_LG:
            bat_boost = 0.10   # eff_bat ~45%
        elif bat_gap >= BAT_GAP_MD:
            bat_boost = 0.07   # eff_bat ~42%
        elif bat_gap >= BAT_GAP_SM:
            bat_boost = 0.03
        else:
            bat_boost = 0.0

        if bat_boost > 0.0:
            # SP 가중치에서 차감 (SP 하한 0.20 보장 — 난타전 보정 후 낮을 수 있음)
            sp_transfer = min(bat_boost, max(0.0, eff_sp_w - 0.20))
            eff_bat_w = round(eff_bat_w + sp_transfer, 2)
            eff_sp_w  = round(eff_sp_w  - sp_transfer, 2)
            stronger_bat = away_name if away_bat_s > home_bat_s else home_name
            tier = "압도" if bat_gap >= BAT_GAP_MD else "우위"
            print(f"    [🏏 타선격차보정({tier})] 격차 {bat_gap:.1f}p | "
                  f"타선 가중치 {eff_bat_w-sp_transfer:.0%}→{eff_bat_w:.0%} "
                  f"(SP {eff_sp_w+sp_transfer:.0%}→{eff_sp_w:.0%}) "
                  f"| {stronger_bat} 타선 우위")

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

        # ── 6.5 선발 ERA 리스크 보정 (season ERA 4.5+ 시 승률 하향) ────
        # 고ERA 선발이 출전하는데도 불펜/타선으로 승률이 60%+인 경우 과신 방지
        SP_ERA_RISK_THRESHOLD  = 4.5   # ERA 이 이상이면 리스크 적용
        SP_ERA_RISK_MAX_PENALTY = 5.0  # 최대 패널티 %p
        if away_season_era and away_season_era >= SP_ERA_RISK_THRESHOLD:
            era_penalty = min(SP_ERA_RISK_MAX_PENALTY, (away_season_era - SP_ERA_RISK_THRESHOLD) * 1.5)
            if away_win_pct > 55.0:   # 55% 이상일 때만 적용
                away_win_pct = max(50.0, away_win_pct - era_penalty)
                home_win_pct = round(100.0 - away_win_pct, 1)
                print(f"    [ERA리스크] {away_name} 선발 ERA {away_season_era:.2f} → -{era_penalty:.1f}% 보정")
        if home_season_era and home_season_era >= SP_ERA_RISK_THRESHOLD:
            era_penalty = min(SP_ERA_RISK_MAX_PENALTY, (home_season_era - SP_ERA_RISK_THRESHOLD) * 1.5)
            if home_win_pct > 55.0:
                home_win_pct = max(50.0, home_win_pct - era_penalty)
                away_win_pct = round(100.0 - home_win_pct, 1)
                print(f"    [ERA리스크] {home_name} 선발 ERA {home_season_era:.2f} → -{era_penalty:.1f}% 보정")

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

        # ── 7b-2. 피처스 듀얼 캡 ──────────────────────────────────────
        # LAD@DET 사례: Snell(hot,72pt) vs Montero(hot,48pt) → LAD 63% 과신 → DET 승
        # 양팀 선발이 모두 hot 트렌드이고 상대 SP도 45pt+ 이면:
        #   → 업셋 확률이 높아지므로 픽팀 승률을 63%로 상한 제한
        # 조건: 양팀 SP trend == "hot" AND loser_sp_score >= 45
        PITCHERS_DUEL_CAP       = 63.0
        PITCHERS_DUEL_OPP_MIN   = 45.0   # 상대 SP 최소 점수
        if away_trend == "hot" and home_trend == "hot":
            if away_win_pct > home_win_pct and home_sp_s >= PITCHERS_DUEL_OPP_MIN:
                if away_win_pct > PITCHERS_DUEL_CAP:
                    away_win_pct = PITCHERS_DUEL_CAP
                    home_win_pct = round(100.0 - away_win_pct, 1)
                    print(f"    [⚾ 투수전캡] 양팀SP모두Hot, 상대SP {home_sp_s}pt≥{PITCHERS_DUEL_OPP_MIN} → {away_name} 승률 {PITCHERS_DUEL_CAP}%로 제한")
            elif home_win_pct > away_win_pct and away_sp_s >= PITCHERS_DUEL_OPP_MIN:
                if home_win_pct > PITCHERS_DUEL_CAP:
                    home_win_pct = PITCHERS_DUEL_CAP
                    away_win_pct = round(100.0 - home_win_pct, 1)
                    print(f"    [⚾ 투수전캡] 양팀SP모두Hot, 상대SP {away_sp_s}pt≥{PITCHERS_DUEL_OPP_MIN} → {home_name} 승률 {PITCHERS_DUEL_CAP}%로 제한")

        # ── 7c. SP 과신 보정 (SP 점수 차이 +30 이상 시 불펜/타선 역전 가능성 반영) ──
        # 실측 데이터 기반: SP 차이가 압도적일수록 모델이 과신하는 경향 (56% 적중)
        # 조건: SP 우위팀이 모델이 예측하는 팀이어야 함 + 불펜/타선은 상대가 우세한 경우만
        SP_OVERCONF_THRESHOLD = 30.0   # SP 점수 차이 임계값
        SP_OVERCONF_PENALTY   = 3.0    # 보정값 (%p 하향)

        sp_diff = home_sp_s - away_sp_s  # 양수 = 홈SP 우세, 음수 = 어웨이SP 우세
        home_bp_bat = (home_bp_s + home_bat_s) / 2
        away_bp_bat = (away_bp_s + away_bat_s) / 2

        if abs(sp_diff) >= SP_OVERCONF_THRESHOLD:
            if sp_diff > 0 and home_win_pct >= away_win_pct:
                # 홈 SP 압도적 + 모델도 홈 예측 + 홈 불펜/타선은 어웨이보다 열세
                if home_bp_bat < away_bp_bat:
                    home_win_pct = max(50.0, home_win_pct - SP_OVERCONF_PENALTY)
                    away_win_pct = round(100.0 - home_win_pct, 1)
                    print(f"    [SP과신보정] 홈SP 압도적 우세(+{sp_diff:.0f}pt)지만 불펜/타선 열세 → 홈 -{SP_OVERCONF_PENALTY}% 보정")
            elif sp_diff < 0 and away_win_pct >= home_win_pct:
                # 어웨이 SP 압도적 + 모델도 어웨이 예측 + 어웨이 불펜/타선은 홈보다 열세
                if away_bp_bat < home_bp_bat:
                    away_win_pct = max(50.0, away_win_pct - SP_OVERCONF_PENALTY)
                    home_win_pct = round(100.0 - away_win_pct, 1)
                    print(f"    [SP과신보정] 어웨이SP 압도적 우세({sp_diff:.0f}pt)지만 불펜/타선 열세 → 어웨이 -{SP_OVERCONF_PENALTY}% 보정")

        # ── 7.85 한쪽 ERA 5+ 경기: SP 격차 집중 보정 ────────────────────
        # 데이터: ERA 5+ 포함 경기에서 SP 우위팀 승률 58.7% (BP 우위팀 47.6%)
        # → ERA 5+ 상대 선발이 있을 때 SP 격차가 가장 신뢰할 지표
        # 조건: 한쪽만 ERA 5+ (양팀 모두면 이미 고위험 처리됨) + SP 격차 ≥ 12pt
        # 처리: SP 우위팀 방향으로 win_pct 2~3% 추가 이동
        SP_ERA_HIGH_THRESH    = 5.0
        SP_GAP_BOOST_THRESH   = 12.0   # SP 격차 이 이상일 때 발동
        SP_ERA_BOOST          = 2.5    # win_pct 이동폭(%)
        # n_high_era: 여기서 미리 계산 (7.95 shrinkage 섹션에서도 재사용)
        _away_era_chk = away_sp_era if away_sp_era else 4.5
        _home_era_chk = home_sp_era if home_sp_era else 4.5
        n_high_era = sum([_away_era_chk >= SP_ERA_HIGH_THRESH, _home_era_chk >= SP_ERA_HIGH_THRESH])
        one_sp_high = (n_high_era == 1)  # 한쪽만 ERA 5+

        if one_sp_high:
            sp_diff_era = home_sp_s - away_sp_s  # 양수=홈SP 우위
            if abs(sp_diff_era) >= SP_GAP_BOOST_THRESH:
                if sp_diff_era > 0:
                    # 홈 SP 명확 우위 → 홈팀 방향으로 보정
                    home_win_pct = min(home_win_pct + SP_ERA_BOOST, HARD_CAP)
                    away_win_pct = round(100.0 - home_win_pct, 1)
                    print(f"    [🎯 SP격차집중] 한쪽ERA5+ + SP격차 {sp_diff_era:.1f}pt → {home_name} +{SP_ERA_BOOST}%")
                else:
                    # 원정 SP 명확 우위 → 원정팀 방향으로 보정
                    away_win_pct = min(away_win_pct + SP_ERA_BOOST, HARD_CAP)
                    home_win_pct = round(100.0 - away_win_pct, 1)
                    print(f"    [🎯 SP격차집중] 한쪽ERA5+ + SP격차 {sp_diff_era:.1f}pt → {away_name} +{SP_ERA_BOOST}%")

        # ── 7.9 Option B: 타선 약팀 승률 상한 캡 ────────────────────────
        # 타선이 극도로 약한 팀(Bat < 28)이 픽으로 선택될 경우 승률 62%로 제한
        # 불펜이 좋아도 점수를 못 내면 65%+ 확률은 과신
        WEAK_BAT_THRESHOLD = 28.0
        WEAK_BAT_CAP       = 62.0
        if away_win_pct > WEAK_BAT_CAP and away_bat_s < WEAK_BAT_THRESHOLD:
            away_win_pct = WEAK_BAT_CAP
            home_win_pct = round(100.0 - away_win_pct, 1)
            print(f"    [타선약팀캡] {away_name} 타선 약(bat={away_bat_s:.1f}) → 승률 {WEAK_BAT_CAP}%로 제한")
        elif home_win_pct > WEAK_BAT_CAP and home_bat_s < WEAK_BAT_THRESHOLD:
            home_win_pct = WEAK_BAT_CAP
            away_win_pct = round(100.0 - home_win_pct, 1)
            print(f"    [타선약팀캡] {home_name} 타선 약(bat={home_bat_s:.1f}) → 승률 {WEAK_BAT_CAP}%로 제한")

        # ── 7.95 확률 압축 (Shrinkage toward 50%) ────────────────────
        # 분석: 2일간 70%+ 예측도 실제 50% 적중 → 극단적 확률은 과신
        # MLB 실제 최강팀도 단일경기 승률 65% 넘기 어려움
        # shrink factor 0.88 → 기존 67%는 62.6%로, 75%는 66%로 압축
        #
        # SP ERA 5+ 고위험 경기: 추가 압축 적용
        # 데이터: ERA 5+ 포함 경기 모델 정확도 55.6% (정상 58.9%)
        #         양팀 ERA 5+: 40.0% — 사실상 예측 불가
        # → 불확실성을 확률에 반영해 과신 방지
        HIGH_ERA_THRESHOLD   = 5.0
        SHRINK_FACTOR        = 0.88   # 기본
        SHRINK_FACTOR_ONE_HIGH = 0.82  # 한쪽 ERA 5+ → 더 압축
        SHRINK_FACTOR_BOTH_HIGH = 0.75 # 양팀 ERA 5+ → 강하게 압축

        # n_high_era는 7.85 섹션에서 이미 계산됨 (_away_era_chk, _home_era_chk 재사용)
        away_sp_era_for_shrink = _away_era_chk
        home_sp_era_for_shrink = _home_era_chk
        # n_high_era 이미 설정됨 (SP_ERA_HIGH_THRESH == HIGH_ERA_THRESHOLD == 5.0)

        if n_high_era == 2:
            eff_shrink = SHRINK_FACTOR_BOTH_HIGH
            print(f"    [⚠️ 고위험압축] 양팀 ERA {HIGH_ERA_THRESHOLD}+ → shrink {eff_shrink} (불확실성 반영)")
        elif n_high_era == 1:
            eff_shrink = SHRINK_FACTOR_ONE_HIGH
            high_side = away_name if away_sp_era_for_shrink >= HIGH_ERA_THRESHOLD else home_name
            print(f"    [⚠️ 고위험압축] {high_side} ERA {HIGH_ERA_THRESHOLD}+ → shrink {eff_shrink}")
        else:
            eff_shrink = SHRINK_FACTOR

        away_win_pct = round(50.0 + (away_win_pct - 50.0) * eff_shrink, 1)
        home_win_pct = round(100.0 - away_win_pct, 1)

        # ── 8. 최종 하드캡 (차별화된 동적 캡 시스템 v7) ───────────────
        # raw_score_gap: 두 팀의 스코어카드 점수 차이 (HOME_BONUS 반영 전 순수 점수)
        _raw_cap_gap = abs(sc["home_total"] - sc["away_total"])

        if is_wednesday and WED_ENABLED:
            # 수요일: 불펜 소진으로 낮은 캡 고정
            effective_cap = min(WED_HARD_CAP, HARD_CAP)
            cap_label = f"수요일{WED_HARD_CAP}%캡"
        elif DYNAMIC_CAP_TIERS:
            # 동적 차별화 캡: raw_score_gap에 따라 단계적 캡 적용
            # 예: gap≥25 → 65%, gap≥15 → 63%, gap≥8 → 61.5%, gap≥0 → 60%
            effective_cap = HARD_CAP_REDUCED  # 기본값 (fallback)
            for tier in DYNAMIC_CAP_TIERS:   # 내림차순 정렬되어 있음
                if _raw_cap_gap >= tier["min_gap"]:
                    effective_cap = tier["cap"]
                    break
            # 전체 상한선은 HARD_CAP으로 제한
            effective_cap = min(effective_cap, HARD_CAP)
            cap_label = f"동적캡(gap={_raw_cap_gap:.1f}pt→{effective_cap}%)"
        else:
            # fallback: 구버전 상대강팀 기반 캡
            opp_strong = (sc["home_total"] >= HARD_CAP_OPP_THRESHOLD or
                          sc["away_total"] >= HARD_CAP_OPP_THRESHOLD)
            effective_cap = HARD_CAP_REDUCED if opp_strong else HARD_CAP
            cap_label = f"상대강팀{HARD_CAP_REDUCED}%캡"

        if away_win_pct > effective_cap:
            away_win_pct = effective_cap
            home_win_pct = round(100.0 - away_win_pct, 1)
            print(f"    [하드캡] {away_name} {away_win_pct}%로 제한 ({cap_label})")
        elif home_win_pct > effective_cap:
            home_win_pct = effective_cap
            away_win_pct = round(100.0 - home_win_pct, 1)
            print(f"    [하드캡] {home_name} {home_win_pct}%로 제한 ({cap_label})")

        # ── 8.5 Cold SP 불펜 안전망 보정 (아이디어 B) ────────────────
        # Cold SP 팀은 조기 강판 후 불펜이 경기를 지배 → 수비력 계산 시 불펜 비중 상향
        # 조건: SP Cold + ERA 5.5↑ → 수비력 = SP×0.35 + BP×0.65 (기본: 0.5/0.5)
        COLD_SP_ERA_THRESHOLD = 5.5
        COLD_BP_WEIGHT = 0.65
        COLD_SP_WEIGHT = 0.35

        away_sp_era_val = away_sp_detail.get("era") or 5.0
        home_sp_era_val = home_sp_detail.get("era") or 5.0

        away_cold_bp_boost = (away_trend == "cold" and away_sp_era_val >= COLD_SP_ERA_THRESHOLD)
        home_cold_bp_boost = (home_trend == "cold" and home_sp_era_val >= COLD_SP_ERA_THRESHOLD)

        if away_cold_bp_boost:
            print(f"    [🔧 Cold불펜보정] {away_name} SP Cold+ERA {away_sp_era_val:.2f} → 수비력 계산 BP 비중 {COLD_BP_WEIGHT:.0%}")
        if home_cold_bp_boost:
            print(f"    [🔧 Cold불펜보정] {home_name} SP Cold+ERA {home_sp_era_val:.2f} → 수비력 계산 BP 비중 {COLD_BP_WEIGHT:.0%}")

        # ── 9. 예상 득점 ──────────────────────────────────────────────
        if away_cold_bp_boost:
            away_def_s = away_sp_s * COLD_SP_WEIGHT + away_bp_s * COLD_BP_WEIGHT
        else:
            away_def_s = (away_sp_s + away_bp_s) / 2

        if home_cold_bp_boost:
            home_def_s = home_sp_s * COLD_SP_WEIGHT + home_bp_s * COLD_BP_WEIGHT
        else:
            home_def_s = (home_sp_s + home_bp_s) / 2

        exp_away = _expected_runs(away_bat_s, home_def_s, 0.0)
        exp_home = _expected_runs(home_bat_s, away_def_s, 0.05)  # 홈 미세 보정

        # ── 9.5 홈팀 박빙 보정 ────────────────────────────────────────
        # 최종 확률 50~55% 박빙 구간: 원정팀 pick이면 홈 이점 감안 -2.5% 보정
        # 홈팀 pick이면 이미 HOME_BONUS 포함 → 변경 없음
        CLOSE_GAME_LOW  = 50.0
        CLOSE_GAME_HIGH = 55.0
        CLOSE_GAME_ADJ  = 2.5

        if CLOSE_GAME_LOW <= away_win_pct <= CLOSE_GAME_HIGH and away_win_pct > home_win_pct:
            # 원정팀이 픽된 박빙 경기 → 원정팀 확률 하향
            orig_away_pct = away_win_pct
            away_win_pct = max(CLOSE_GAME_LOW, away_win_pct - CLOSE_GAME_ADJ)
            home_win_pct = round(100.0 - away_win_pct, 1)
            if away_win_pct < CLOSE_GAME_LOW:
                result_label = f"홈픽전환({home_name} {home_win_pct}%)"
            else:
                result_label = f"원정픽유지({away_name} {away_win_pct}%)"
            print(f"    [🏠 박빙홈보정] 원정픽 {orig_away_pct}% → 홈이점 -{CLOSE_GAME_ADJ}% → {result_label}")

        # ── 9.5 SP vs BAT 충돌 감지 ──────────────────────────────────────
        # SP 점수가 한 팀을 가리키는데, BAT 점수가 반대 팀을 가리킬 때 경고
        # 조건: SP 우위 팀 ≠ BAT 우위 팀  AND  각 차이가 모두 최소 임계값 이상
        SP_BAT_CONFLICT_SP_MIN  = 10.0   # SP 점수 차이 최소 (이 이상이어야 충돌로 인정)
        SP_BAT_CONFLICT_BAT_MIN =  8.0   # BAT 점수 차이 최소

        sp_gap  = away_sp_s  - home_sp_s    # 양수 = 원정팀 SP 우위
        bat_gap = away_bat_s - home_bat_s   # 양수 = 원정팀 타선 우위

        # SP와 BAT가 서로 반대 팀을 가리키는지 (부호 반대)
        sp_bat_conflict = (
            abs(sp_gap)  >= SP_BAT_CONFLICT_SP_MIN and
            abs(bat_gap) >= SP_BAT_CONFLICT_BAT_MIN and
            (sp_gap > 0) != (bat_gap > 0)   # 부호 반대 = 다른 팀 가리킴
        )

        if sp_bat_conflict:
            sp_favors  = away_name if sp_gap  > 0 else home_name
            bat_favors = away_name if bat_gap > 0 else home_name
            print(f"    [⚠️ SP↔BAT 충돌] SP우위={sp_favors}({sp_gap:+.1f}pt) ↔ "
                  f"BAT우위={bat_favors}({bat_gap:+.1f}pt) → 예측 신뢰도 주의")

        # ── 🔍 예측 신뢰도 판정 ──────────────────────────────────────────
        # SP 점수 차이 5pt 이하 → 선발 동급 → 예측 불확실
        SP_CONFIDENCE_THRESHOLD = 5.0
        sp_score_gap = abs(away_sp_s - home_sp_s)
        low_confidence = (sp_score_gap <= SP_CONFIDENCE_THRESHOLD)
        if low_confidence:
            print(f"    [🔍 신뢰도낮음] SP 점수 차이 {sp_score_gap:.1f}pt ≤ {SP_CONFIDENCE_THRESHOLD}pt → 선발 동급, 예측 불확실")

        # ── 10. Kalshi + Value Bet ─────────────────────────────────────
        # 고위험 경기 판정: 양 팀 선발 ERA 모두 BOTH_SP_HIGH_ERA_THRESHOLD 이상
        both_sp_high_era = (away_sp_era >= BOTH_SP_HIGH_ERA_THRESHOLD and
                            home_sp_era >= BOTH_SP_HIGH_ERA_THRESHOLD)
        if both_sp_high_era:
            print(f"    [⚠️ 고위험] 양팀 선발 ERA 모두 {BOTH_SP_HIGH_ERA_THRESHOLD}+ "
                  f"({away_name} {away_sp_era} / {home_name} {home_sp_era}) → 예측 신뢰도 낮음")

        # 라인업 미공개(팀통계) 경기는 신뢰도 부족 → Value Bet 패스 (evaluate() 내부에서 처리)
        lineup_confirmed = (bat_source != "team_stats")
        if not lineup_confirmed:
            kalshi_home = None
            vb = evaluate(
                home_win_pct, kalshi_home, home_name, away_name,
                model_pct=max(home_win_pct, away_win_pct),
                lineup_confirmed=False,
            )
            print(f"    [Value Bet] 라인업 미공개 → 패스")
        elif both_cold:
            # 양팀 선발 모두 Cold → 불펜전: 예측 신뢰도 매우 낮음 → Value Bet 자동 제외
            kalshi_home, _ = _safe(
                lambda: get_kalshi_prob(game_date, away_name, home_name),
                (None, None), "Kalshi"
            )
            vb = evaluate(
                home_win_pct, kalshi_home, home_name, away_name,
                model_pct=max(home_win_pct, away_win_pct),
                lineup_confirmed=lineup_confirmed,
            )
            if vb.get("value_bet", "").startswith("✅") or vb.get("value_bet", "").startswith("🔥"):
                vb["value_bet"] = "⏭️ 패스 (불펜전 — 양팀 선발 Cold, 예측 신뢰 낮음)"
                print(f"    [Value Bet] 양팀 선발 Cold 불펜전 → Value Bet 자동 제외")
        elif both_sp_high_era:
            # 양팀 ERA 5+ → Value Bet 자동 제외
            # 데이터: 양팀 ERA 5+ 모델 정확도 40.0% (5경기) — 사실상 예측 불가
            # BP 우위팀 승률 47.6% < 50% → BP 가중치 조정도 의미 없음
            # SP 점수 격차만이 그나마 예측력 있음(58.7%) → 참고 지표로만 표시
            kalshi_home = None
            sp_gap = abs(away_sp_s - home_sp_s)
            sp_pick = home_name if home_sp_s >= away_sp_s else away_name
            vb = {
                "kalshi_prob": None,
                "edge": None,
                "value_bet": f"⏭️ 패스 (양팀 ERA {BOTH_SP_HIGH_ERA_THRESHOLD}+ — 불확실성 과다, SP격차 {sp_gap:.1f}p 참고)",
                "extreme_edge": False,
                "consensus": None,
            }
            print(f"    [Value Bet] 양팀 ERA {BOTH_SP_HIGH_ERA_THRESHOLD}+ → Value Bet 자동 제외 "
                  f"(SP격차 {sp_gap:.1f}p: {sp_pick} 우위)")
        else:
            kalshi_home, _ = _safe(
                lambda: get_kalshi_prob(game_date, away_name, home_name),
                (None, None), "Kalshi"
            )

            # ── Kalshi 큰 괴리 보정 ───────────────────────────────────
            # |edge| >= 15%p일 때 모델을 Kalshi 방향으로 25% 당김
            KALSHI_GAP_THRESHOLD = 15.0
            KALSHI_PULL_RATIO    = 0.25
            if kalshi_home is not None:
                kalshi_edge = home_win_pct - kalshi_home  # 양수: 모델이 홈 더 선호
                if abs(kalshi_edge) >= KALSHI_GAP_THRESHOLD:
                    adj = abs(kalshi_edge) * KALSHI_PULL_RATIO
                    if kalshi_edge > 0:
                        # 모델이 홈을 과대평가 → 홈 pct 하향 (Kalshi 방향)
                        home_win_pct = round(home_win_pct - adj, 1)
                        away_win_pct = round(100.0 - home_win_pct, 1)
                        direction = "홈하향"
                    else:
                        # 모델이 홈을 과소평가 → 홈 pct 상향 (Kalshi 방향)
                        home_win_pct = round(home_win_pct + adj, 1)
                        away_win_pct = round(100.0 - home_win_pct, 1)
                        direction = "홈상향"
                    print(f"    [🔄 Kalshi보정] edge {kalshi_edge:+.1f}%p ≥ {KALSHI_GAP_THRESHOLD} → 모델 {direction} {adj:.1f}%p 보정")

            vb = evaluate(
                home_win_pct, kalshi_home, home_name, away_name,
                model_pct=max(home_win_pct, away_win_pct),
                lineup_confirmed=lineup_confirmed,
            )

            # ── 🚨 SP↔BAT 충돌 처리 ──────────────────────────────────────
            # Case A: Kalshi 있음 + 역방향 → 자동 패스 (기존)
            # Case B: Kalshi 없음          → 신뢰도 낮음 경고 (9/2 STL@LAD, DET@MIN 사례)
            SP_BAT_CONFLICT_KALSHI_THRESHOLD = -3.0
            SP_BAT_CONFLICT_NO_MARKET_SP_MIN = 25.0  # SP 차이 이 이상이면 충돌 심각으로 판단
            if sp_bat_conflict:
                if kalshi_home is not None:
                    # Case A: 시장 역방향 확인
                    model_picks_home  = (home_win_pct >= away_win_pct)
                    kalshi_picks_home = (kalshi_home >= 50.0)
                    kalshi_edge_val   = vb.get("edge") or 0.0
                    if model_picks_home != kalshi_picks_home and abs(kalshi_edge_val) >= abs(SP_BAT_CONFLICT_KALSHI_THRESHOLD):
                        print(f"    [🚨 SP↔BAT+Kalshi역방향] 충돌({sp_favors}↔{bat_favors}) + 시장 반대방향(edge {kalshi_edge_val:+.1f}%p) → 자동 패스")
                        vb["value_bet"] = f"⏭️ 패스 (SP↔BAT충돌+시장역방향 edge {kalshi_edge_val:+.1f}%p — 신뢰불가)"
                else:
                    # Case B: 칼시 없음 + SP↔BAT 충돌 → 심각도에 따라 패스 or 경고
                    sp_conflict_gap = abs(sp_gap)
                    bat_conflict_gap = abs(bat_gap)
                    if sp_conflict_gap >= SP_BAT_CONFLICT_NO_MARKET_SP_MIN and not vb.get("value_bet", "").startswith("⏭️"):
                        print(f"    [⚠️ SP↔BAT충돌+시장없음] SP차이 {sp_conflict_gap:.1f}pt, BAT차이 {bat_conflict_gap:.1f}pt → 시장 미검증으로 자동 패스")
                        vb["value_bet"] = f"⏭️ 패스 (SP↔BAT충돌+Kalshi없음 — 시장 미검증, 신뢰불가)"

            # ── 라인업 미확정 픽 승률 패널티 ──────────────────────────────
            # 라인업 미확정 경기(prev_day/team_stats): 정보 불확실성 → 승률 -5%p 보정
            # 단, 이미 low_confidence이거나 value_bet이 패스된 경기는 제외
            UNCONFIRMED_LINEUP_PENALTY = 3.0  # 패널티 크기
            UNCONFIRMED_MIN_PROB = 55.0        # 이 확률 이상일 때만 적용 (박빙은 건드리지 않음)
            if not lineup_confirmed and bat_source != "lineup":
                model_top = max(home_win_pct, away_win_pct)
                if model_top >= UNCONFIRMED_MIN_PROB:
                    if home_win_pct >= away_win_pct:
                        home_win_pct = round(max(50.0, home_win_pct - UNCONFIRMED_LINEUP_PENALTY), 1)
                        away_win_pct = round(100.0 - home_win_pct, 1)
                    else:
                        away_win_pct = round(max(50.0, away_win_pct - UNCONFIRMED_LINEUP_PENALTY), 1)
                        home_win_pct = round(100.0 - away_win_pct, 1)
                    print(f"    [📋 미확정패널티] 라인업 미확정({bat_source}) → 승률 -{UNCONFIRMED_LINEUP_PENALTY}%p 하향 ({model_top:.1f}%→{max(home_win_pct, away_win_pct):.1f}%)")
                    # vb 재계산 (패널티 후 확률로)
                    vb = evaluate(
                        home_win_pct, kalshi_home, home_name, away_name,
                        model_pct=max(home_win_pct, away_win_pct),
                        lineup_confirmed=lineup_confirmed,
                    )

            # ── 3중 패스: 저신뢰 + 양팀 SP cold + 라인업 미확정 ─────────
            # 세 가지 불확실성이 겹치면 예측 불가 수준 → 무조건 패스
            if (low_confidence and both_cold and not lineup_confirmed
                    and not vb.get("value_bet", "").startswith("⏭️")):
                print(f"    [🔴 3중패스] 저신뢰+양팀Cold+라인업미확정 → 자동 패스")
                vb["value_bet"] = "⏭️ 패스 (저신뢰+양팀SP cold+라인업미확정 — 예측불가 수준)"

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
                # Cold 선발 필터: 예측팀 선발 Cold → VB 경고
                elif winner_trend == "cold":
                    print(f"    [⚠️ VB Cold필터] 예측팀 선발 Cold 트렌드 → Value Bet 신뢰도 하락")
                    vb["value_bet"] = "⚠️ VB주의(선발Cold) — " + vb["value_bet"]

                # ── 🚫 상대팀 선발 Cold → VB 완전 금지 ─────────────────
                # Cold SP는 반등 가능성이 있어 모델이 과대평가하는 경향
                # 예) HOU@NYM: NYM SP cold였으나 실제 HOU가 6-3 완승
                if vb.get("value_bet", "").startswith("✅"):
                    if loser_trend == "cold":
                        print(f"    [🚫 VB Cold SP금지] 상대팀 선발 Cold → 반등 위험 → Value Bet 제외")
                        vb["value_bet"] = f"⏭️ 패스 (상대선발Cold — 반등위험, 예측신뢰 낮음)"

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

            # ── 🔍 low_confidence → Value Bet 자동 제외 ──────────────
            # SP 점수 차이 ≤ 5pt 경기: 선발 동급 → 예측 신뢰도 낮음 → VB 제외
            if low_confidence and vb.get("value_bet", "").startswith("✅"):
                print(f"    [🔍 VB 신뢰도] SP 동급({sp_score_gap:.1f}pt) → Value Bet 자동 제외")
                vb["value_bet"] = f"⏭️ 패스 (SP동급 {sp_score_gap:.1f}pt — 예측 불확실)"

            # ── Kalshi 데이터 없는 경기 고신뢰 차단 ─────────────────────
            # Kalshi 없으면 시장 검증 불가 → ⭐ 고신뢰·✅ Value Bet 등급 하향
            # (MIA@KC 사례: Kalshi 0%로 시장 검증 없이 KC 62% 픽 → 역방향 결과)
            if kalshi_home is None and not lineup_confirmed:
                if vb.get("value_bet", "").startswith(("✅", "⭐")):
                    print(f"    [📵 Kalshi없음+미확정] 시장 미검증 → 고신뢰 등급 하향")
                    vb["value_bet"] = f"⚠️ 시장미검증 (Kalshi없음·라인업미확정) — 참고만"

        # ── 11. 부상 노트 ─────────────────────────────────────────────
        notes = _safe(lambda: get_injury_notes(away_id, home_id, away_name, home_name), "", "부상체크")

        # ── 12. 선발투수 표시용 시즌 스탯 ────────────────────────────
        away_pitcher_stats = _safe(lambda p=away_pitcher_id: _pitcher_display_stats(p),
                                   {"wins": None, "losses": None, "era": None}, "원정 투수시즌스탯")
        home_pitcher_stats = _safe(lambda p=home_pitcher_id: _pitcher_display_stats(p),
                                   {"wins": None, "losses": None, "era": None}, "홈 투수시즌스탯")

        # ── 12. 박빙 타이브레이커 (스코어카드 raw total 차이 ≤ 3.0p) ────
        # sc["away_total"] / sc["home_total"]: HOME_BONUS 반영 전 순수 스코어카드 합계
        # 이 값이 거의 동점일 때 "홈이니까 유리" 단순 적용 대신
        # 팀 순위·streak 를 정량화해서 홈 어드밴티지를 이길 만큼 원정팀이 강한지 판단.
        #
        # 설계 원칙:
        #   "홈 어드밴티지 = 3승 차이와 동등한 가중치"
        #   → 홈 이점 유지 기준값 HOME_ADV_WINS = 3
        #   → 순위 우위(승수 차이) + streak 차이가 이를 넘으면 원정팀으로 픽 전환
        TIEBREAK_THRESHOLD = 3.0    # raw total 차이(점) 이하면 타이브레이커 적용
        HOME_ADV_WINS      = 3.0    # 홈 어드밴티지 = 3승 차이 상당
        STREAK_WEIGHT      = 0.5    # streak 1경기 = 0.5승 상당

        raw_score_gap = abs(sc["home_total"] - sc["away_total"])

        if raw_score_gap <= TIEBREAK_THRESHOLD:
            # 시즌 승수(W) 직접 사용
            home_wins_tb  = int(away_st.get("wins",  0) or 0)   # 주의: standings_map key는 팀 ID
            away_wins_tb  = int(away_st.get("wins",  0) or 0)
            home_wins_tb  = int(home_st.get("wins",  0) or 0)
            away_wins_tb  = int(away_st.get("wins",  0) or 0)

            # 최근 streak (양수=연승, 음수=연패)
            away_streak_tb = int(away_st.get("streak_wins", 0) or 0)
            home_streak_tb  = int(home_st.get("streak_wins", 0) or 0)

            # 홈팀 net 우위 (양수 = 홈팀 유리, 음수 = 원정팀 유리)
            #   win_gap:    홈팀 - 원정팀 승수 차이
            #   streak_gap: (홈 streak - 원정 streak) × weight
            #   home_field: 기본 홈 어드밴티지 = +HOME_ADV_WINS
            win_gap       = (home_wins_tb - away_wins_tb)
            streak_gap    = (home_streak_tb - away_streak_tb) * STREAK_WEIGHT
            home_field    = HOME_ADV_WINS

            net_home = win_gap + streak_gap + home_field

            if net_home >= 0:
                # 홈팀 우위 또는 동등 → 기존 HOME_BONUS 유지
                tb_winner = home_name
            else:
                # 원정팀이 홈 어드밴티지를 극복할 만큼 우위
                # → 홈 보너스를 제거하고 원정팀 소폭 우위로 전환
                home_win_pct = max(45.0, home_win_pct - HOME_BONUS - 1.5)
                away_win_pct = round(100.0 - home_win_pct, 1)
                tb_winner = away_name

            print(f"    [🔀 타이브레이커] raw스코어 차이 {raw_score_gap:.1f}p ≤ {TIEBREAK_THRESHOLD}p | "
                  f"승수차 {win_gap:+d} streak차 {streak_gap:+.1f} 홈어드밴티지 +{home_field:.0f} "
                  f"= net_home {net_home:+.1f} → {tb_winner} 선택")

        # ── 모델 적중 판정 ────────────────────────────────────────────
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
            "game_time":    g.get("game_time", ""),
            "game_number":  g.get("game_number", 1),
            "doubleheader": g.get("doubleheader", False),
            "game_pk":      g.get("gamePk"),
            "away":         away_name,
            "home":         home_name,
            "away_standing":   _team_standing(away_id),
            "home_standing":   _team_standing(home_id),
            "away_recent_form": _recent_form(away_id, game_date),
            "home_recent_form": _recent_form(home_id, game_date),
            "away_pitcher":         away_pitcher,
            "away_pitcher_id":      away_pitcher_id,
            "away_pitcher_stats":   away_pitcher_stats,
            "away_pitcher_gamelog": _pitcher_gamelog_summary(away_gl if away_gl is not None else []),
            "home_pitcher":         home_pitcher,
            "home_pitcher_id":      home_pitcher_id,
            "home_pitcher_stats":   home_pitcher_stats,
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
            "lineup_confirmed": bat_source == "lineup",  # ← 실제 MLB 라인업만 확정으로 인정
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
            "sp_bat_conflict": sp_bat_conflict,
            "sp_bat_conflict_detail": {
                "sp_favors":  (away_name if sp_gap  > 0 else home_name) if sp_bat_conflict else None,
                "bat_favors": (away_name if bat_gap > 0 else home_name) if sp_bat_conflict else None,
                "sp_gap":     round(sp_gap,  1),
                "bat_gap":    round(bat_gap, 1),
            } if sp_bat_conflict else None,
            "low_confidence": low_confidence,
            "low_confidence_reason": f"SP 점수 차이 {sp_score_gap:.1f}pt (≤{SP_CONFIDENCE_THRESHOLD}pt)" if low_confidence else None,
            "model_version": MODEL_VERSION,
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

    # ── 웹사이트 public/predictions.json 자동 동기화 ──────────────────
    # mlb-scorecard-web/public/predictions.json 을 항상 최신으로 유지
    WEB_PUBLIC_DIRS = [
        Path(__file__).parent.parent.parent / "mlb-scorecard-web" / "public",
        Path(__file__).parent.parent.parent / "mlb-scorecard-web" / "dist",
    ]
    for web_dir in WEB_PUBLIC_DIRS:
        target = web_dir / "predictions.json"
        if web_dir.exists():
            target.write_text(json_payload, encoding="utf-8")
            print(f"  [🌐 웹싱크] {target} 업데이트 완료")
        else:
            print(f"  [🌐 웹싱크] {web_dir} 없음 — 스킵")

    return results


if __name__ == "__main__":
    import sys as _sys
    d = _sys.argv[1] if len(_sys.argv) > 1 else None
    run(d)
