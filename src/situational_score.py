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
) -> float:
    """
    상황적 요소 → 0~100 점수
    streak: 양수=연승, 음수=연패
    """
    score = 50.0

    # 홈 어드밴티지
    if is_home:
        score += 5.0

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
