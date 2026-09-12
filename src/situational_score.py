"""
상황적 요소 점수 (홈 어드밴티지, 연승/연패, 리그 순위, 시즌 승률)
"""
from typing import Optional


def situational_score(
    is_home: bool,
    streak: int,
    div_rank: Optional[int] = None,
    wins: Optional[int] = None,
    losses: Optional[int] = None,
    home_wpct: Optional[float] = None,   # 팀 실제 홈 승률 (0.0~1.0)
    away_wpct: Optional[float] = None,   # 팀 실제 원정 승률 (원정팀에 적용)
) -> float:
    """
    상황적 요소 → 0~100 점수
    streak: 양수=연승, 음수=연패
    home_wpct: 해당 팀의 시즌 홈 승률 → 홈 어드밴티지 동적 조정
    away_wpct: 해당 팀의 시즌 원정 승률 → 원정 강팀 보정
    """
    score = 50.0

    # ── 홈 어드밴티지 (팀 실제 홈/최근 성적 기반 동적 조정) ─────────
    # MLB 실제 홈 승률 ≈ 52.3%. 상한선 +3pt로 제한 (과대평가 방지)
    # 근거: 이틀간 홈팀 실제 승률 30% — 시즌 홈승률 과신이 핵심 오류
    if is_home:
        wpct = home_wpct if home_wpct is not None else 0.523  # MLB 2026 평균
        if wpct >= 0.620:
            score += 3.0    # 홈 강팀 (62%+): 기존 5pt → 3pt (상한선 적용)
        elif wpct >= 0.560:
            score += 2.0    # 홈 평균 이상 (56~62%): 기존 3.5pt → 2pt
        elif wpct >= 0.500:
            score += 1.0    # 홈 평균 이하 (50~56%): 기존 2pt → 1pt
        else:
            score += 0.0    # 홈 약팀 (50% 미만): 어드밴티지 없음

    # ── 원정 강팀 보정 ────────────────────────────────────────────────
    elif not is_home and away_wpct is not None:
        if away_wpct >= 0.600:
            score += 1.5    # 원정 강팀: 소폭 보정 (기존 2pt → 1.5pt)
        elif away_wpct < 0.400:
            score -= 1.5    # 원정 약팀: 페널티

    # 연승/연패 모멘텀
    if streak >= 7:
        score += 12.0
    elif streak >= 5:
        score += 8.0
    elif streak >= 3:
        score += 5.0
    elif streak >= 1:
        score += 2.0
    elif streak <= -7:
        score -= 12.0
    elif streak <= -5:
        score -= 8.0
    elif streak <= -3:
        score -= 5.0
    elif streak <= -1:
        score -= 2.0

    # 지구 순위
    if div_rank is not None:
        if div_rank == 1:
            score += 8.0
        elif div_rank == 2:
            score += 4.0
        elif div_rank >= 4:
            score -= 4.0

    # 시즌 승률 보정 (v2: 비선형 강화 — 격차 클수록 더 강하게 반영)
    # ★ 2순위 개선: 승률 격차 10%↑ 팀(60%+ 또는 40%-) 은 더 강한 계수 적용
    #   기존: 고정 계수 15.0  →  개선: 격차에 따라 15~25 동적 계수
    #   예) SD(53.4%) vs SF(42.2%) → SF gap=-7.8% → 계수 20 → 기존 -1.17pt에서 -1.56pt로 강화
    #   예) 팀 승률 60%↑ vs 40%- 대결: 계수 25 → 더 강한 격차 반영
    if wins is not None and losses is not None:
        total = wins + losses
        if total > 0:
            wpct = wins / total
            gap = wpct - 0.500
            abs_gap = abs(gap)
            if abs_gap >= 0.100:      # 60%↑ 또는 40%↓ — 확연한 강팀/약팀
                coeff = 25.0
            elif abs_gap >= 0.060:    # 56~60% 또는 40~44% — 뚜렷한 차이
                coeff = 20.0
            else:                     # ±6% 이내 — 중간 팀 (기존 동일)
                coeff = 15.0
            score += gap * coeff

    return round(max(0.0, min(100.0, score)), 1)
