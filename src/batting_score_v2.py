"""
타선 점수 계산 (시즌 OPS + 최근 10경기 타격 + 최근 5경기 트렌드)
"""


def _calc_hitting_stats(logs: list, season_stats: dict = None) -> dict:
    """게임로그 리스트 → 타격 통계 dict (내부 공용 함수)"""
    season_stats = season_stats or {}
    n = len(logs)
    total_r = total_h = total_ab = total_hr = total_bb = 0

    for s in logs:
        total_r  += int(s.get("runs", 0)        or 0)
        total_h  += int(s.get("hits", 0)         or 0)
        total_ab += int(s.get("atBats", 0)       or 0)
        total_hr += int(s.get("homeRuns", 0)     or 0)
        total_bb += int(s.get("baseOnBalls", 0)  or 0)

    recent_avg = round(total_h / total_ab, 3) if total_ab > 0 else 0.250
    runs_per_g = round(total_r / n, 1)        if n > 0        else 4.3
    hr_per_g   = round(total_hr / n, 2)       if n > 0        else 1.1
    bb_per_g   = round(total_bb / n, 1)       if n > 0        else 3.0

    # ── 최근 5경기 트렌드 계산 ──────────────────────────────────────
    # MLB API는 오래된순 정렬 → 마지막 5개가 최신 5경기
    last5 = logs[-5:]
    l5_r  = sum(int(s.get("runs", 0) or 0) for s in last5)
    l5_ab = sum(int(s.get("atBats", 0) or 0) for s in last5)
    l5_h  = sum(int(s.get("hits", 0) or 0) for s in last5)
    l5_n  = len(last5)
    last5_rpg = round(l5_r / l5_n, 1)    if l5_n > 0  else runs_per_g
    last5_avg = round(l5_h / l5_ab, 3)   if l5_ab > 0 else recent_avg

    # 트렌드 판정: 최근 5경기 vs 전체 10경기 비교
    # AND → OR로 완화: 득점 또는 타율 중 하나만 기준 충족해도 트렌드 인정
    if l5_n >= 3:
        rpg_hot  = last5_rpg >= runs_per_g * 1.20   # 최근 5경기 득점 20%↑
        avg_hot  = last5_avg >= recent_avg + 0.015   # 최근 5경기 타율 .015↑
        rpg_cold = last5_rpg <= runs_per_g * 0.80   # 최근 5경기 득점 20%↓
        avg_cold = last5_avg <= recent_avg - 0.015   # 최근 5경기 타율 .015↓

        if rpg_hot and avg_hot:
            bat_trend = "hot"    # 득점 + 타율 모두 상승 → 확실한 hot
        elif rpg_cold and avg_cold:
            bat_trend = "cold"   # 득점 + 타율 모두 하락 → 확실한 cold
        else:
            bat_trend = "stable"
    else:
        bat_trend = "stable"

    try:
        season_ops = float(season_stats.get("ops", 0.720) or 0.720)
    except Exception:
        season_ops = 0.720
    try:
        season_avg = float(season_stats.get("avg", "0.250") or 0.250)
    except Exception:
        season_avg = 0.250
    try:
        season_slg = float(season_stats.get("slg", 0.400) or 0.400)
    except Exception:
        season_slg = 0.400

    return {
        "recent_avg":   recent_avg,
        "runs_per_g":   runs_per_g,
        "hr_per_g":     hr_per_g,
        "bb_per_g":     bb_per_g,
        "season_ops":   season_ops,
        "season_avg":   season_avg,
        "season_slg":   season_slg,
        "n_games":      n,
        "last5_rpg":    last5_rpg,
        "last5_avg":    last5_avg,
        "bat_trend":    bat_trend,
    }


def analyze_batting(hit_logs: list, season_stats: dict = None,
                    split_logs: dict = None) -> dict:
    """
    팀 타격 게임로그 분석
    hit_logs:   get_team_hitting_log() 반환값 (stat dict 리스트) — 최근 전체
    season_stats: get_team_hitting_season() 반환값 (optional)
    split_logs: get_team_hitting_log_split() 반환값 (optional)
                {"home": [...], "away": [...], "all": [...]}
                제공 시 메인 통계는 홈/원정 실제 경기 방향 기준으로 계산
    """
    season_stats = season_stats or {}

    # 메인 통계: 최근 10경기 (전체)
    logs = hit_logs[:10]
    result = _calc_hitting_stats(logs, season_stats)

    # 홈/원정 분리 통계 (split_logs 제공 시)
    if split_logs:
        home_logs = split_logs.get("home", [])
        away_logs = split_logs.get("away", [])
        if home_logs:
            result["home_split"] = _calc_hitting_stats(home_logs, season_stats)
            result["home_split"]["n_games"] = len(home_logs)
        if away_logs:
            result["away_split"] = _calc_hitting_stats(away_logs, season_stats)
            result["away_split"]["n_games"] = len(away_logs)

    return result


def batting_score(stats: dict) -> float:
    """
    타선 통계 → 0~100 점수

    v4 가중치:
      시즌 OPS  20%
      시즌 SLG  15% (장타 폭발력)
      최근 AVG  30%
      득점/경기 25%
      HR/경기   10%

    트렌드 보정 (최근 5경기 vs 전체 10경기):
      hot   → +4pt (득점 25%↑ + 타율 .020↑)
      cold  → -4pt (득점 25%↓ + 타율 .020↓)
      stable→ 보정 없음

    변경사항 (v4):
      - explosive_games 보너스 제거 (득점/경기와 중복 집계)
      - 최근 5경기 트렌드(hot/cold) 보정 추가
    """
    ops  = stats.get("season_ops", 0.720)
    slg  = stats.get("season_slg", 0.400)
    ravg = stats.get("recent_avg", 0.250)
    rpg  = stats.get("runs_per_g", 4.3)
    hr   = stats.get("hr_per_g",   1.1)
    trend = stats.get("bat_trend", "stable")

    ops_s  = max(0.0, min(100.0, (ops  - 0.600) / 0.350 * 100.0))
    slg_s  = max(0.0, min(100.0, (slg  - 0.300) / 0.300 * 100.0))
    ravg_s = max(0.0, min(100.0, (ravg - 0.200) / 0.160 * 100.0))
    rpg_s  = max(0.0, min(100.0, (rpg  - 2.0)   / 6.0   * 100.0))
    hr_s   = max(0.0, min(100.0, (hr   - 0.3)   / 2.2   * 100.0))

    score = (ops_s  * 0.20 +
             slg_s  * 0.15 +
             ravg_s * 0.30 +
             rpg_s  * 0.25 +
             hr_s   * 0.10)

    # 최근 5경기 트렌드 보정
    if trend == "hot":
        score = min(100.0, score + 4.0)
    elif trend == "cold":
        score = max(0.0,   score - 4.0)

    return round(max(0.0, min(100.0, score)), 1)


def analyze_lineup_batting(players: list, team_hit_logs: list = None) -> dict:
    """
    실제 라인업(타자 dict 리스트) 기반 타선 점수 계산 — Phase 2
    각 타자 개인 blended_ops 평균 + 팀 득점/HR은 팀 통계 보완

    players: lineup_fetcher.get_game_lineup() 반환값의 away/home 리스트
             [{"id": int, "name": str, "pos": str}, ...]
    team_hit_logs: 팀 최근 게임로그 (득점/HR 보완용)
    """
    from batter_stats import analyze_batter

    ops_list       = []
    avg_list       = []
    names          = []
    lineup_players = []   # [{id, name, ops, avg}] — Hero 선수 선정용

    for p in players:
        pid = p.get("id")
        if not pid:
            continue
        b = analyze_batter(pid)
        ops_list.append(b["blended_ops"])
        avg_list.append(b["recent_avg"])
        names.append(f"{p.get('name','?')}({b['blended_ops']:.3f})")
        lineup_players.append({
            "id":   pid,
            "name": p.get("name", "?"),
            "ops":  b["blended_ops"],
            "avg":  b["recent_avg"],
        })

    if not ops_list:
        return _default_batting()

    avg_ops    = round(sum(ops_list) / len(ops_list), 3)
    avg_recent = round(sum(avg_list) / len(avg_list), 3)

    # 팀 통계에서 득점/HR 보완 (개인 합산보다 팀 집계가 더 정확)
    if team_hit_logs:
        logs = team_hit_logs[:10]
        n = len(logs)
        total_r  = sum(int(s.get("runs",      0) or 0) for s in logs)
        total_hr = sum(int(s.get("homeRuns",  0) or 0) for s in logs)
        runs_per_g = round(total_r  / n, 1) if n > 0 else 4.3
        hr_per_g   = round(total_hr / n, 2) if n > 0 else 1.1
    else:
        runs_per_g = 4.3
        hr_per_g   = 1.1

    # SLG: 라인업 개인 통계에서 직접 계산 (analyze_batter 이미 호출했으므로 재사용)
    slg_list = [b["season_slg"] for p in players
                if (pid := p.get("id")) and (b := analyze_batter(pid))]
    avg_slg = round(sum(slg_list) / len(slg_list), 3) if slg_list else 0.400

    return {
        "recent_avg":      avg_recent,
        "runs_per_g":      runs_per_g,
        "hr_per_g":        hr_per_g,
        "bb_per_g":        3.0,
        "season_ops":      avg_ops,
        "season_avg":      avg_recent,
        "season_slg":      avg_slg,
        "n_games":         len(players),
        "source":          "lineup",
        "lineup_ops":      names,
        "lineup_players":  lineup_players,  # Hero 선수 선정용
    }


def analyze_lineup_batting_with_splits(
    players: list,
    opp_handedness: str,
    team_hit_logs: list = None,
) -> dict:
    """
    실제 라인업 타자 + 상대 투수 투구 방향 스플릿 기반 타선 분석

    players:         [{"id","name","pos"}, ...]
    opp_handedness:  상대 선발투수 투구 방향 ('L' or 'R')
    team_hit_logs:   팀 최근 게임로그 (득점/HR 보완용)
    """
    from batter_stats import analyze_batter_with_splits

    ops_list       = []
    avg_list       = []
    names          = []
    lineup_players = []   # [{id, name, ops, avg}] — Hero 선수 선정용

    for p in players:
        pid = p.get("id")
        if not pid:
            continue
        b = analyze_batter_with_splits(pid, opp_handedness)
        ops_list.append(b["blended_ops"])
        avg_list.append(b["recent_avg"])
        names.append(f"{p.get('name','?')}({b['blended_ops']:.3f})")
        lineup_players.append({
            "id":   pid,
            "name": p.get("name", "?"),
            "ops":  b["blended_ops"],
            "avg":  b["recent_avg"],
        })

    if not ops_list:
        return _default_batting()

    avg_ops    = round(sum(ops_list) / len(ops_list), 3)
    avg_recent = round(sum(avg_list) / len(avg_list), 3)

    # 팀 통계에서 득점/HR 보완
    if team_hit_logs:
        logs = team_hit_logs[:10]
        n = len(logs)
        total_r  = sum(int(s.get("runs",     0) or 0) for s in logs)
        total_hr = sum(int(s.get("homeRuns", 0) or 0) for s in logs)
        runs_per_g = round(total_r  / n, 1) if n > 0 else 4.3
        hr_per_g   = round(total_hr / n, 2) if n > 0 else 1.1
    else:
        runs_per_g = 4.3
        hr_per_g   = 1.1

    # splits 실제 사용 여부 확인 (source에서 판단)
    from batter_stats import analyze_batter_with_splits as _abws
    sample = _abws(players[0]["id"], opp_handedness) if players else {}
    splits_used = sample.get("split_source") is not None

    return {
        "recent_avg":      avg_recent,
        "runs_per_g":      runs_per_g,
        "hr_per_g":        hr_per_g,
        "bb_per_g":        3.0,
        "season_ops":      avg_ops,
        "season_avg":      avg_recent,
        "n_games":         len(players),
        "source":          "prev_day_splits" if splits_used else "prev_day",
        "handedness":      opp_handedness,
        "splits_used":     splits_used,
        "lineup_ops":      names,
        "lineup_players":  lineup_players,  # Hero 선수 선정용
    }


def _default_batting() -> dict:
    return {
        "recent_avg": 0.250, "runs_per_g": 4.3, "hr_per_g": 1.1,
        "bb_per_g": 3.0, "season_ops": 0.720, "season_avg": 0.250,
        "n_games": 0, "source": "team_stats",
    }
