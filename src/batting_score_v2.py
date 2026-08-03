"""
타선 점수 계산 (시즌 OPS + 최근 10경기 타격)
"""


def analyze_batting(hit_logs: list, season_stats: dict = None) -> dict:
    """
    팀 타격 게임로그 분석
    hit_logs: get_team_hitting_log() 반환값 (stat dict 리스트)
    season_stats: get_team_hitting_season() 반환값 (optional)
    """
    season_stats = season_stats or {}

    # 최근 10경기 집계
    logs = hit_logs[:10]
    n = len(logs)
    total_r = total_h = total_ab = total_hr = total_bb = 0

    for s in logs:
        total_r  += int(s.get("runs", 0)         or 0)
        total_h  += int(s.get("hits", 0)          or 0)
        total_ab += int(s.get("atBats", 0)        or 0)
        total_hr += int(s.get("homeRuns", 0)      or 0)
        total_bb += int(s.get("baseOnBalls", 0)   or 0)

    recent_avg  = round(total_h / total_ab, 3) if total_ab > 0 else 0.250
    runs_per_g  = round(total_r / n, 1)        if n > 0        else 4.3
    hr_per_g    = round(total_hr / n, 2)       if n > 0        else 1.1
    bb_per_g    = round(total_bb / n, 1)       if n > 0        else 3.0

    # 폭발 지표: 최근 10경기 중 7점+ 득점 횟수 (CWS 10점처럼 저평가 타선 폭발 대비)
    explosive_games = sum(1 for s in logs if int(s.get("runs", 0) or 0) >= 7)

    # 시즌 OPS / AVG
    try:
        season_ops = float(season_stats.get("ops", 0.720) or 0.720)
    except Exception:
        season_ops = 0.720
    try:
        season_avg = float(season_stats.get("avg", "0.250") or 0.250)
    except Exception:
        season_avg = 0.250

    return {
        "recent_avg":      recent_avg,
        "runs_per_g":      runs_per_g,
        "hr_per_g":        hr_per_g,
        "bb_per_g":        bb_per_g,
        "season_ops":      season_ops,
        "season_avg":      season_avg,
        "n_games":         n,
        "explosive_games": explosive_games,  # 7점+ 득점 경기 수
    }


def batting_score(stats: dict) -> float:
    """
    타선 통계 → 0~100 점수
    시즌 OPS 25% + 최근 타율 35% + 득점/경기 30% + HR/경기 10%
    (v2: runs_per_g 25%→30%, season_ops 30%→25% — 실제 득점력 반영 강화)
    """
    ops  = stats.get("season_ops", 0.720)
    ravg = stats.get("recent_avg", 0.250)
    rpg  = stats.get("runs_per_g", 4.3)
    hr   = stats.get("hr_per_g",   1.1)

    ops_s  = max(0.0, min(100.0, (ops  - 0.600) / 0.350 * 100.0))
    ravg_s = max(0.0, min(100.0, (ravg - 0.200) / 0.160 * 100.0))
    rpg_s  = max(0.0, min(100.0, (rpg  - 2.0)   / 6.0   * 100.0))
    hr_s   = max(0.0, min(100.0, (hr   - 0.3)   / 2.2   * 100.0))

    score = ops_s * 0.25 + ravg_s * 0.35 + rpg_s * 0.30 + hr_s * 0.10

    # 폭발 지표 보너스: 7점+ 경기 2회 이상이면 +3pt, 3회 이상이면 +5pt
    explosive = stats.get("explosive_games", 0)
    if explosive >= 3:
        score = min(100.0, score + 5.0)
    elif explosive >= 2:
        score = min(100.0, score + 3.0)

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

    ops_list  = []
    avg_list  = []
    names     = []

    for p in players:
        pid = p.get("id")
        if not pid:
            continue
        b = analyze_batter(pid)
        ops_list.append(b["blended_ops"])
        avg_list.append(b["recent_avg"])
        names.append(f"{p.get('name','?')}({b['blended_ops']:.3f})")

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

    return {
        "recent_avg":   avg_recent,
        "runs_per_g":   runs_per_g,
        "hr_per_g":     hr_per_g,
        "bb_per_g":     3.0,
        "season_ops":   avg_ops,
        "season_avg":   avg_recent,
        "n_games":      len(players),
        "source":       "lineup",
        "lineup_ops":   names,   # 디버그용 타자별 OPS
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

    ops_list  = []
    avg_list  = []
    names     = []

    for p in players:
        pid = p.get("id")
        if not pid:
            continue
        b = analyze_batter_with_splits(pid, opp_handedness)
        ops_list.append(b["blended_ops"])
        avg_list.append(b["recent_avg"])
        names.append(f"{p.get('name','?')}({b['blended_ops']:.3f})")

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
        "recent_avg":   avg_recent,
        "runs_per_g":   runs_per_g,
        "hr_per_g":     hr_per_g,
        "bb_per_g":     3.0,
        "season_ops":   avg_ops,
        "season_avg":   avg_recent,
        "n_games":      len(players),
        "source":       "prev_day_splits" if splits_used else "prev_day",
        "handedness":   opp_handedness,
        "splits_used":  splits_used,
        "lineup_ops":   names,   # 디버그용 타자별 OPS
    }


def _default_batting() -> dict:
    return {
        "recent_avg": 0.250, "runs_per_g": 4.3, "hr_per_g": 1.1,
        "bb_per_g": 3.0, "season_ops": 0.720, "season_avg": 0.250,
        "n_games": 0, "source": "team_stats",
    }
