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

    # 시즌 승률 보정
    if wins is not None and losses is not None:
        total = wins + losses
        if total > 0:
            wpct = wins / total
            score += (wpct - 0.500) * 15.0

    return round(max(0.0, min(100.0, score)), 1)
