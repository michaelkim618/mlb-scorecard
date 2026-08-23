"""
선발투수 최근 성적 분석 및 점수화
get_pitcher_gamelog() 반환값을 분석
"""
import math
import requests
from datetime import date, datetime
from typing import Optional, List

# ── 구종 코드 → 약어 매핑 ──────────────────────────────────────
_PITCH_ABBR = {
    "FF": "FB",   # Four-seam Fastball
    "SI": "SI",   # Sinker
    "FC": "CT",   # Cutter
    "SL": "SL",   # Slider
    "ST": "SW",   # Sweeper
    "CU": "CB",   # Curveball
    "CH": "CH",   # Changeup
    "FS": "FS",   # Splitter
    "KC": "KC",   # Knuckle-Curve
    "SV": "SV",   # Slurve
    "FO": "FO",   # Forkball
    "SC": "SC",   # Screwball
    "KN": "KN",   # Knuckleball
}
_FASTBALL_CODES = {"FF", "SI", "FC"}

_arsenal_cache: dict = {}   # player_id → arsenal dict (세션 캐시)


def get_pitcher_arsenal(player_id: int) -> dict:
    """
    MLB Stats API pitchArsenal로 투수의 나이·구속·구종 정보 반환.
    반환: {age, fb_velo, secondary_pitches, pitch_arsenal, pitches_detail}
    실패 시 빈 dict 반환 (파이프라인 중단 방지).
    """
    if not player_id:
        return {}
    if player_id in _arsenal_cache:
        return _arsenal_cache[player_id]

    try:
        headers = {"User-Agent": "Mozilla/5.0"}

        # ── 나이 ──────────────────────────────────────────────
        r1 = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{player_id}",
            headers=headers, timeout=8
        )
        person = r1.json().get("people", [{}])[0]
        age = person.get("currentAge")

        # ── 구종 아스날 ───────────────────────────────────────
        r2 = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{player_id}"
            f"?hydrate=stats(group=[pitching],type=[pitchArsenal],season=2026)",
            headers=headers, timeout=8
        )
        stats = r2.json().get("people", [{}])[0].get("stats", [])

        pitches = []
        fb_velo = None
        for s in stats:
            for sp in s.get("splits", []):
                stat = sp.get("stat", {})
                code = stat.get("type", {}).get("code", "")
                pct  = stat.get("percentage", 0) or 0
                spd  = stat.get("averageSpeed", 0) or 0
                if pct < 0.03 or not code:     # 3% 미만 구종 제외
                    continue
                abbr = _PITCH_ABBR.get(code, code)
                pitches.append({"code": code, "abbr": abbr,
                                "pct": round(pct * 100, 1), "velo": round(spd, 1)})
                if code in _FASTBALL_CODES and (fb_velo is None or spd > fb_velo):
                    fb_velo = round(spd, 1)

        # 사용률 내림차순 정렬
        pitches.sort(key=lambda x: -x["pct"])
        arsenal   = [p["abbr"] for p in pitches]
        secondary = [p["abbr"] for p in pitches if p["code"] not in _FASTBALL_CODES]

        result = {
            "age":              age,
            "fb_velo":          fb_velo,
            "pitch_arsenal":    arsenal,
            "secondary_pitches": secondary,
            "pitches_detail":   pitches,
        }
        _arsenal_cache[player_id] = result
        return result

    except Exception:
        return {}


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

    # 직전 등판 경보 — 직전 1경기만 체크 (2경기 전 나쁜 등판은 이미 회복으로 볼 수 있음)
    # ㆍhot/stable: 직전 마지막 등판 ERA >= 6.0 → 위험 신호
    # ㆍcold:       직전 마지막 등판 ERA >= 9.0 → 극단적 붕괴 (cold -8pt 이미 적용)
    last_era = last2_eras[-1] if last2_eras else None
    if last_era is not None:
        if trend in ("hot", "stable"):
            recent_bad_start = last_era >= 6.0
        elif trend == "cold":
            recent_bad_start = last_era >= 9.0
        else:
            recent_bad_start = False
    else:
        recent_bad_start = False

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


def pitcher_score(stats: dict, season_era: float = None,
                  season_wins: int = None, season_losses: int = None) -> float:
    """
    투수 분석 → 0~100 점수
    ERA 40% + WHIP 30% + K/9 20% + 이닝 5% + QS율 5%

    개선 사항 (v3):
      - K/9 가중치 20% 신설 (탈삼진은 ERA보다 안정적 지표)
      - QS Rate 가중치 10%→5% (pitch-count 관리 시대에 QS는 과대평가)
      - ERA 45%→40%, WHIP 35%→30%, IP 10%→5%
      - W-L 승률 보너스/페널티 추가 (season_wins/season_losses)
      - Hot +3pt (과가중 완화, 이전 +6pt)
      - Cold -8pt (완화, 이전 -14pt)
      - Neutral(장기휴식 복귀): 트렌드 보정 없음
      - 시즌 ERA 하한선: season_era 기준 최소 점수 보장
      - 최종 범위 압축: 30~72점 (극단값 방지)
      - 소수 샘플(n_games<5): 점수를 리그 평균(45pt) 방향으로 회귀
      - 짧은 휴식(short_rest): -5pt
      - 과잉 휴식(extra_rest): -2pt (컨디션 불확실)

    개선 사항 (v4):
      - avg_ip 기반 Hot 보너스 감쇠: 오래 못 버티는 투수의 hot 과대평가 방지
          avg_ip < 4.0 → hot_bonus × 0.3
          avg_ip < 5.0 → hot_bonus × 0.7
          avg_ip < 5.5 → hot_bonus × 0.9
      - avg_ip 짧은 등판 페널티: 선발로서 기대값 미달
          avg_ip < 4.0 → -4pt  avg_ip < 5.0 → -2pt
      - QS율 기반 SP 점수 상한 캡: 자주 조기 강판되는 투수 과신 방지
          qs_rate < 33% → 최대 50pt   qs_rate < 50% → 최대 56pt
      - W-L 페널티 구간 확장: 서브-.500 레코드 포함
          win_pct ≤ 0.48 → -1pt (예: 6-7, 7-8 등 근소 부진)

    개선 사항 (v5):
      - 패스트볼 구속 기반 페널티: 92 mph 미만 투수는 현대 MLB에서 구위 한계
          fb_velo < 88 mph → -6pt  (극저속: 배팅 프랙티스 수준, 심각한 구위 부재)
          fb_velo < 90 mph → -4pt  (저속: 생존 투구 의존, 커맨드 흔들리면 즉시 폭발)
          fb_velo < 92 mph → -3pt  (기준 미달: 타자가 타이밍 잡기 쉬움, 실점 리스크 상승)
          fb_velo is None  →  0pt  (데이터 없음: 페널티 미적용)
    """
    # ERA None 처리: 데이터 없음 → 리그 평균보다 약간 나쁜 5.00 적용 + 샘플 신뢰도 낮춤
    # (기존 4.50 기본값은 "평균 수준"으로 가정 → 실제론 정보 없음이므로 보수적으로)
    _era_raw = stats.get("era")
    era_is_unknown = (_era_raw is None)
    era    = float(_era_raw) if _era_raw is not None else 5.00

    whip   = stats.get("whip",    1.35)
    if stats.get("whip") is None:
        whip = 1.40  # ERA 없으면 WHIP도 모름 → 평균보다 약간 나쁘게

    k9     = stats.get("k9",      7.0)
    avg_ip = stats.get("avg_ip",  5.0)
    qs     = stats.get("qs_rate", 30.0)
    trend  = stats.get("trend",   "stable")
    conf   = stats.get("sample_confidence", 1.0)
    rest_note = stats.get("rest_note")
    recent_bad_start = stats.get("recent_bad_start", False)
    fb_velo = stats.get("fb_velo")   # 패스트볼 평균 구속 (mph), None이면 데이터 없음

    # 0~100 정규화
    era_s  = max(0.0, min(100.0, (7.5 - era)    / 7.5  * 100.0))
    whip_s = max(0.0, min(100.0, (2.0 - whip)   / 1.2  * 100.0))
    k9_s   = max(0.0, min(100.0, (k9 - 4.0)     / 8.0  * 100.0))
    ip_s   = max(0.0, min(100.0, (avg_ip - 3.0) / 5.0  * 100.0))
    qs_s   = max(0.0, min(100.0, qs / 80.0 * 100.0))

    # ERA 40% + WHIP 30% + K/9 20% + 이닝 5% + QS율 5%
    raw_score = era_s * 0.40 + whip_s * 0.30 + k9_s * 0.20 + ip_s * 0.05 + qs_s * 0.05

    # 샘플 신뢰도 보정: n_games < 5이면 리그 평균(45pt) 방향으로 회귀
    LEAGUE_AVG = 45.0
    # ERA 데이터 자체가 없는 경우 → 신뢰도를 0.5로 강제 낮춤 (불확실성 반영)
    if era_is_unknown:
        conf = min(conf, 0.5)
    score = raw_score * conf + LEAGUE_AVG * (1.0 - conf)

    # 트렌드 보정
    # hot: +3pt (소샘플 감쇠 + avg_ip 감쇠 적용) | cold: -8pt | neutral: 보정 없음
    #
    # ── Hot 소샘플 감쇠 ──────────────────────────────────────────────
    # n_games < 3이면 Hot 판정이 1~2경기만 기반 → 신뢰도 낮음
    # 최근 2경기 연속 ERA ≤ 1.5 같은 극단적 핫스트릭에 +3pt 전부 적용하면
    # 과대평가 발생 → n_games 기반으로 보너스를 감쇠
    #   n_games ≥ 5: +3.0pt (정상)
    #   n_games == 4: +2.0pt
    #   n_games == 3: +1.5pt
    #   n_games ≤ 2: +1.0pt (최소 신뢰 보너스만 적용)
    n_games_val = stats.get("n_games", 5)
    if n_games_val >= 5:
        hot_bonus = 3.0
    elif n_games_val == 4:
        hot_bonus = 2.0
    elif n_games_val == 3:
        hot_bonus = 1.5
    else:  # 1~2경기
        hot_bonus = 1.0

    # ── Hot avg_ip 감쇠 (v4) ─────────────────────────────────────────
    # 평균 이닝이 짧은 투수는 hot 트렌드여도 실제 경기 기여도가 낮음
    # 조기 강판 빈도가 높을수록 hot 보너스를 줄여 과대평가 방지
    #   avg_ip < 4.0 → ×0.3 (심각한 조기 강판 반복)
    #   avg_ip < 5.0 → ×0.7 (5이닝 미달: QS 기준에 못 미침)
    #   avg_ip < 5.5 → ×0.9 (약간 짧음)
    #   avg_ip ≥ 5.5 → ×1.0 (정상)
    if avg_ip < 4.0:
        ip_hot_factor = 0.3
    elif avg_ip < 5.0:
        ip_hot_factor = 0.7
    elif avg_ip < 5.5:
        ip_hot_factor = 0.9
    else:
        ip_hot_factor = 1.0
    hot_bonus = round(hot_bonus * ip_hot_factor, 1)

    if trend == "hot" and not recent_bad_start:
        score = min(100.0, score + hot_bonus)
    elif trend == "cold":
        score = max(0.0, score - 8.0)
    # neutral (장기 휴식 복귀): trend 보정 없음 → 최근 기록 그대로 반영

    # ── avg_ip 짧은 등판 페널티 (v4) ────────────────────────────────
    # 5이닝 미만 선발은 선발로서의 기대값 자체가 낮음
    # 불펜 소진 가속 리스크 → SP 점수에서 직접 차감
    #   avg_ip < 4.0 → -4pt   avg_ip < 5.0 → -2pt
    if avg_ip < 4.0:
        score = max(0.0, score - 4.0)
    elif avg_ip < 5.0:
        score = max(0.0, score - 2.0)

    # 직전 등판 경보 페널티
    # ㆍhot/stable + ERA >= 6.0: -7pt (5경기 평균에 희석된 급락 신호)
    # ㆍcold       + ERA >= 9.0: -5pt (cold -8pt 이미 적용 → 추가 완화 페널티)
    if recent_bad_start:
        extra_penalty = 5.0 if trend == "cold" else 7.0
        score = max(0.0, score - extra_penalty)

    # ── 패스트볼 구속 페널티 (v5) ────────────────────────────────────
    # 현대 MLB에서 92 mph 미만 패스트볼은 타자가 타이밍을 잡기 쉬워 실점 리스크 상승.
    # 구위 보완용 커맨드·변화구가 있어도 구속 열세는 구조적 약점 → 하향 조정.
    #   fb_velo < 88 mph → -6pt  (극저속: 완전 커맨드/무브먼트 의존형, 고위험)
    #   fb_velo < 90 mph → -4pt  (저속: 한 경기 무너지면 대량 실점 가능)
    #   fb_velo < 92 mph → -3pt  (기준 미달: 지속적 열세, 상대 타선 적응 빠름)
    #   fb_velo is None  →  0pt  (데이터 없음, 패널티 미적용)
    if fb_velo is not None:
        if fb_velo < 88.0:
            score = max(0.0, score - 6.0)
        elif fb_velo < 90.0:
            score = max(0.0, score - 4.0)
        elif fb_velo < 92.0:
            score = max(0.0, score - 3.0)

    # 휴식일 보정
    if rest_note == "short_rest":
        score = max(0.0, score - 5.0)   # 짧은 휴식 페널티
    elif rest_note == "extra_rest":
        score = max(0.0, score - 2.0)   # 과잉 휴식 불확실성 (완화)
    # long_rest: trend가 neutral로 이미 리셋됨 → 추가 페널티 없음

    # ── QS율 기반 SP 점수 상한 캡 (v4) ─────────────────────────────
    # QS율이 낮다 = 자주 조기강판 → 경기 제어력 부족
    # hot 트렌드 등으로 점수가 높게 나왔어도 실질 기대값 반영해 캡 적용
    #   qs_rate < 33% → 최대 50pt (10경기 중 3경기 미만 QS)
    #   qs_rate < 50% → 최대 56pt (절반 미만 QS)
    if qs < 33.0:
        score = min(score, 50.0)
    elif qs < 50.0:
        score = min(score, 56.0)

    # ── W-L 승률 보너스/페널티 ──────────────────────────────────────
    # 14-2(87.5%) 투수와 13-7(65%) 투수는 유의미한 차이
    # 최소 5결정(wins+losses)이 있어야 신뢰도 있는 지표로 사용
    # v4: 서브-.500 페널티 구간 확장 (≤0.48 → -1pt, 예: 6-7, 7-8 등)
    if season_wins is not None and season_losses is not None:
        total_decisions = season_wins + season_losses
        if total_decisions >= 5:
            win_pct = season_wins / total_decisions
            if win_pct >= 0.75:
                score = min(100.0, score + 3.0)   # 에이스급 승률 (예: 12-4)
            elif win_pct >= 0.60:
                score = min(100.0, score + 1.5)   # 우수 승률 (예: 9-6)
            elif win_pct <= 0.35:
                score = max(0.0, score - 3.0)     # 부진 투수 (예: 4-8+)
            elif win_pct <= 0.40:
                score = max(0.0, score - 2.0)     # 평균 이하 (예: 6-9)
            elif win_pct <= 0.48:
                score = max(0.0, score - 1.0)     # 근소 서브-.500 (예: 6-7, 7-8)

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
