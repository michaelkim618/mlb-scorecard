"""
선발투수 최근 성적 분석 및 점수화
get_pitcher_gamelog() 반환값을 분석
"""
import math
from datetime import date, datetime
from typing import Optional, List


def _ip_to_float(ip_str) -> float:
    """이닝 파싱: '5.2' → 5.667 (5이닝 2아웃), '7' → 7.0"""
    try:
        s = str(ip_str)
        if '.' in s:
            parts = s.split('.')
            full = int(parts[0])
            thirds = int(parts[1]) if len(parts) > 1 else 0
            return full + thirds / 3.0
        return float(s)
    except Exception:
        return 0.0


def _rest_days(game_logs: list, today_str: str = None) -> Optional[int]:
    """
    마지막 등판 날짜로부터 오늘(예측 날짜)까지 휴식일 수 계산.
    game_logs는 오래된 순 정렬(index 0 = 가장 오래된 경기, index -1 = 가장 최근 경기).
    → 역순으로 탐색하여 가장 최근 등판일 사용.
    """
    try:
        today = datetime.fromisoformat(today_str).date() if today_str else date.today()
        # 역순으로 탐색 → 가장 최근 등판일 기준 휴식일 계산
        for log in reversed(game_logs):
            gd = log.get("_game_date")
            if gd:
                last_date = datetime.fromisoformat(gd[:10]).date()
                # 오늘 경기(예측 당일)는 제외
                if last_date < today:
                    return (today - last_date).days
    except Exception:
        pass
    return None


def analyze_pitcher_recent(game_logs: list, n: int = 10,
                           today_str: str = None) -> dict:
    """
    투수 최근 n경기 게임로그 분석
    game_logs: get_pitcher_gamelog() 반환값 (stat dict 리스트, 최신순)
    today_str: 예측 날짜 (YYYY-MM-DD), 없으면 오늘
    """
    logs = game_logs[:n]
    if not logs:
        return _default_pitcher()

    total_ip = total_er = total_h = total_bb = total_k = 0.0
    qs_count = 0

    for s in logs:
        ip = _ip_to_float(s.get("inningsPitched", "0"))
        er = float(s.get("earnedRuns", 0) or 0)
        h  = float(s.get("hits", 0) or 0)
        bb = float(s.get("baseOnBalls", 0) or 0)
        k  = float(s.get("strikeOuts", 0) or 0)

        total_ip += ip
        total_er += er
        total_h  += h
        total_bb += bb
        total_k  += k

        if ip >= 6.0 and er <= 3:
            qs_count += 1

    n_games = len(logs)
    era     = round(total_er / total_ip * 9, 2) if total_ip > 0 else 4.50
    if era == 0.0:
        era = 4.50
    whip    = round((total_h + total_bb) / total_ip, 2) if total_ip > 0 else 1.35
    k9      = round(total_k / total_ip * 9, 1) if total_ip > 0 else 7.0
    avg_ip  = round(total_ip / n_games, 1) if n_games > 0 else 5.0
    qs_rate = round(qs_count / n_games * 100, 1) if n_games > 0 else 30.0

    # 최근 5경기 ERA (트렌드) — 절대값 기준
    last5     = logs[-5:]
    l5_ip     = sum(_ip_to_float(s.get("inningsPitched", "0")) for s in last5)
    l5_er     = sum(float(s.get("earnedRuns", 0) or 0) for s in last5)
    last5_era = round(l5_er / l5_ip * 9, 2) if l5_ip > 0 else era
    l5_games  = len(last5)
    l5_avg_ip = round(l5_ip / l5_games, 1) if l5_games > 0 else 0.0

    # 직전 2경기 per-game ERA 계산 — 최신 경향 포착용
    last2_logs = logs[-2:] if len(logs) >= 2 else logs[-1:]
    last2_eras = []
    for s in last2_logs:
        ip = _ip_to_float(s.get("inningsPitched", "0"))
        er = float(s.get("earnedRuns", 0) or 0)
        if ip > 0:
            last2_eras.append(round(er / ip * 9, 2))

    # 직전 마지막 등판 ERA (UI 표시용)
    last_start_era = last2_eras[-1] if last2_eras else None

    # 트렌드 판정 (절대 ERA 기준)
    if l5_avg_ip < 3.0:
        trend = "cold"   # 조기강판 반복
    elif last5_era < 3.0:
        trend = "hot"    # ERA 3.0 미만 = 에이스급 폼
    elif last5_era < 4.0:
        trend = "stable" # ERA 3.0~4.0 = 평균
    else:
        trend = "cold"   # ERA 4.0 이상 = 불안정

    # 직전 등판 경보: Hot/Stable이어도 최근 2경기 중 1개라도 ERA >= 6.0이면 위험 신호
    # (5경기 평균이 희석시키는 최근 나쁜 등판 포착 — 1경기 7.0 기준보다 더 정교)
    recent_bad_start = (
        trend in ("hot", "stable")
        and any(e >= 6.0 for e in last2_eras)
    )

    # ── 샘플 신뢰도 ──────────────────────────────────────────────────
    # n_games < 5: 소수 샘플 → ERA 신뢰도 낮음, 기본값 방향으로 회귀
    if n_games < 5:
        sample_confidence = n_games / 5.0   # 0.0~1.0
    else:
        sample_confidence = 1.0

    # ── 휴식일 계산 ──────────────────────────────────────────────────
    rest = _rest_days(logs, today_str)
    # 정상 로테이션: 4~5일 휴식
    # 3일 이하: 짧은 휴식 → 체력 부담
    # 30일 이상: 장기 결장(부상복귀·IL) → trend 리셋
    rest_note = None
    if rest is not None:
        if rest <= 3:
            rest_note = "short_rest"    # 3일 이하
        elif rest >= 30:
            rest_note = "long_rest"     # 30일 이상 (부상복귀·장기결장)
            # 장기 휴식 후 복귀: hot/cold 트렌드 신뢰 불가 → neutral로 리셋
            trend = "neutral"
        elif rest >= 7:
            rest_note = "extra_rest"    # 7일 이상 (컨디션 불확실)

    return {
        "era":              era,
        "whip":             whip,
        "k9":               k9,
        "avg_ip":           avg_ip,
        "qs_rate":          qs_rate,
        "last3_era":        last5_era,       # 대시보드 표시용 (필드명 유지)
        "last_start_era":   last_start_era,  # 직전 마지막 등판 ERA
        "last2_eras":       last2_eras,      # 직전 2경기 per-game ERA 리스트
        "recent_bad_start": recent_bad_start,# Hot/Stable인데 최근 2경기 중 1개라도 ERA >= 6.0
        "trend":            trend,
        "n_games":          n_games,
        "sample_confidence": sample_confidence,
        "rest_days":        rest,
        "rest_note":        rest_note,
    }


def pitcher_score(stats: dict, season_era: float = None) -> float:
    """
    투수 분석 → 0~100 점수
    ERA 45% + WHIP 35% + 이닝 10% + QS율 10%

    개선 사항 (v2):
      - Hot +3pt (과가중 완화, 이전 +6pt)
      - Cold -8pt (완화, 이전 -14pt)
      - Neutral(장기휴식 복귀): 트렌드 보정 없음
      - 시즌 ERA 하한선: season_era 기준 최소 점수 보장
      - 최종 범위 압축: 30~72점 (극단값 방지)
      - 소수 샘플(n_games<5): 점수를 리그 평균(45pt) 방향으로 회귀
      - 짧은 휴식(short_rest): -5pt
      - 과잉 휴식(extra_rest): -2pt (컨디션 불확실)
    """
    era    = stats.get("era",     4.50)
    whip   = stats.get("whip",    1.35)
    avg_ip = stats.get("avg_ip",  5.0)
    qs     = stats.get("qs_rate", 30.0)
    trend  = stats.get("trend",   "stable")
    conf   = stats.get("sample_confidence", 1.0)
    rest_note = stats.get("rest_note")
    recent_bad_start = stats.get("recent_bad_start", False)

    # 0~100 정규화
    era_s  = max(0.0, min(100.0, (7.5 - era)    / 7.5  * 100.0))
    whip_s = max(0.0, min(100.0, (2.0 - whip)   / 1.2  * 100.0))
    ip_s   = max(0.0, min(100.0, (avg_ip - 3.0) / 5.0  * 100.0))
    qs_s   = max(0.0, min(100.0, qs / 80.0 * 100.0))

    # ERA 45% + WHIP 35% + 이닝 10% + QS율 10%
    raw_score = era_s * 0.45 + whip_s * 0.35 + ip_s * 0.10 + qs_s * 0.10

    # 샘플 신뢰도 보정: n_games < 5이면 리그 평균(45pt) 방향으로 회귀
    LEAGUE_AVG = 45.0
    score = raw_score * conf + LEAGUE_AVG * (1.0 - conf)

    # 트렌드 보정 (완화된 값)
    # hot: +3pt (이전 +6pt) | cold: -8pt (이전 -14pt) | neutral: 보정 없음
    if trend == "hot" and not recent_bad_start:
        score = min(100.0, score + 3.0)
    elif trend == "cold":
        score = max(0.0,   score - 8.0)
    # neutral (장기 휴식 복귀): trend 보정 없음 → 최근 기록 그대로 반영

    # 직전 등판 경보 페널티: Hot/Stable인데 마지막 등판 ERA >= 7.0
    # 5경기 평균이 좋아도 최근 1경기가 나쁘면 위험 신호 반영
    if recent_bad_start:
        score = max(0.0, score - 7.0)

    # 휴식일 보정
    if rest_note == "short_rest":
        score = max(0.0, score - 5.0)   # 짧은 휴식 페널티
    elif rest_note == "extra_rest":
        score = max(0.0, score - 2.0)   # 과잉 휴식 불확실성 (완화)
    # long_rest: trend가 neutral로 이미 리셋됨 → 추가 페널티 없음

    # ── 시즌 ERA 하한선 ─────────────────────────────────────────────
    # 최근 성적이 나빠도 시즌 누적 ERA가 양호하면 최소 점수 보장
    # (hot streak 종료 후 bounce-back 가능성 반영)
    if season_era is not None:
        if season_era <= 3.00:
            score = max(score, 48.0)   # 에이스급: 최소 48점
        elif season_era <= 3.50:
            score = max(score, 42.0)   # 우수: 최소 42점
        elif season_era <= 4.00:
            score = max(score, 36.0)   # 평균 이상: 최소 36점
        elif season_era <= 4.50:
            score = max(score, 32.0)   # 리그 평균: 최소 32점

    # ── 최종 범위 압축: 30~72점 ──────────────────────────────────────
    # 어떤 MLB 선발도 30점 이하(극도로 나쁨)나 72점 이상(완벽)으로 평가하지 않음
    score = max(30.0, min(72.0, score))

    return round(score, 1)


def _default_pitcher() -> dict:
    return {
        "era": 4.50, "whip": 1.35, "k9": 7.0,
        "avg_ip": 5.0, "qs_rate": 30.0,
        "last3_era": 4.50, "trend": "stable", "n_games": 0,
        "sample_confidence": 1.0, "rest_days": None, "rest_note": None,
    }
