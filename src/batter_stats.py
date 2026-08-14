"""
개인 타자 시즌 + 최근 성적 수집 및 점수화
"""
import requests
from typing import Optional

BASE = "https://statsapi.mlb.com/api/v1"


def get_batter_season(player_id: int) -> dict:
    """타자 시즌 전체 성적"""
    try:
        r = requests.get(f"{BASE}/people/{player_id}/stats", params={
            "stats": "season", "season": "2026", "group": "hitting",
        }, timeout=10)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        return splits[0].get("stat", {}) if splits else {}
    except Exception:
        return {}


def get_batter_recent(player_id: int, n: int = 10) -> list:
    """타자 최근 n경기 게임로그 (최신순)"""
    try:
        r = requests.get(f"{BASE}/people/{player_id}/stats", params={
            "stats": "gameLog", "season": "2026", "group": "hitting",
        }, timeout=10)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        # splits는 오래된 순 → 뒤집어서 최신 n개
        return [s.get("stat", {}) for s in reversed(splits)][:n]
    except Exception:
        return []


def analyze_batter(player_id: int) -> dict:
    """
    개인 타자 종합 분석
    시즌 OPS 50% + 최근 10경기 OPS 50% 블렌딩
    """
    season    = get_batter_season(player_id)
    recent_gs = get_batter_recent(player_id, 10)

    # ── 시즌 스탯 ──────────────────────────────────────────────────────
    try:
        season_ops = float(season.get("ops", 0.700) or 0.700)
    except Exception:
        season_ops = 0.700
    try:
        raw_avg = season.get("avg", "0.250") or "0.250"
        season_avg = float(raw_avg)
    except Exception:
        season_avg = 0.250
    try:
        season_slg = float(season.get("slg", 0.400) or 0.400)
    except Exception:
        season_slg = 0.400

    # ── 최근 10경기 OPS 계산 ──────────────────────────────────────────
    total_ab = total_h = total_bb = total_tb = total_pa = 0
    for s in recent_gs:
        ab = int(s.get("atBats",       0) or 0)
        h  = int(s.get("hits",         0) or 0)
        bb = int(s.get("baseOnBalls",  0) or 0)
        tb = int(s.get("totalBases",   0) or 0)
        total_ab += ab
        total_h  += h
        total_bb += bb
        total_tb += tb
        total_pa += ab + bb

    # OBP = (H+BB) / PA,  SLG = TB / AB
    recent_obp = (total_h + total_bb) / total_pa if total_pa > 0 else 0.320
    recent_slg = total_tb / total_ab              if total_ab > 0 else 0.380
    recent_ops = round(recent_obp + recent_slg, 3)
    recent_avg = round(total_h / total_ab, 3)     if total_ab > 0 else 0.250

    # ── 블렌딩: 시즌 50% + 최근 50% ──────────────────────────────────
    blended_ops = round(season_ops * 0.50 + recent_ops * 0.50, 3)

    return {
        "player_id":   player_id,
        "season_ops":  round(season_ops, 3),
        "season_avg":  round(season_avg, 3),
        "season_slg":  round(season_slg, 3),
        "recent_ops":  recent_ops,
        "recent_avg":  recent_avg,
        "blended_ops": blended_ops,
        "n_recent":    len(recent_gs),
    }


def batter_score(stats: dict) -> float:
    """
    개인 타자 blended_ops → 0~100 점수
    OPS 0.500=0점, 1.000=100점
    """
    ops = stats.get("blended_ops", 0.700)
    return round(max(0.0, min(100.0, (ops - 0.500) / 0.500 * 100.0)), 1)


def get_batter_splits(player_id: int, handedness: str) -> Optional[dict]:
    """
    타자의 vs LHP / vs RHP 상대 시즌 성적 조회
    handedness: 'L' (상대가 좌투수) or 'R' (상대가 우투수)

    Returns: {"ops": float, "avg": float, "obp": float, "slg": float}
             or None (데이터 없음 → fallback 필요)
    """
    split_code = "vl" if handedness == "L" else "vr"

    # 2026 → 2025 순으로 시도
    for season in ("2026", "2025"):
        try:
            r = requests.get(f"{BASE}/people/{player_id}/stats", params={
                "stats":  "statSplits",
                "season": season,
                "group":  "hitting",
            }, timeout=10)
            r.raise_for_status()
            splits_data = r.json().get("stats", [])

            for stat_group in splits_data:
                for split in stat_group.get("splits", []):
                    sc = split.get("split", {}).get("code", "")
                    if sc == split_code:
                        s = split.get("stat", {})
                        try:
                            ops  = float(s.get("ops", 0) or 0)
                            avg  = float(s.get("avg", "0") or 0)
                            obp  = float(s.get("obp", "0") or 0)
                            slg  = float(s.get("slg", "0") or 0)
                            if ops > 0:   # 유효한 데이터
                                return {"ops": ops, "avg": avg, "obp": obp, "slg": slg,
                                        "season": season}
                        except Exception:
                            pass
        except Exception:
            pass
    return None   # 데이터 없음


def analyze_batter_with_splits(player_id: int, handedness: str) -> dict:
    """
    개인 타자 종합 분석 — 상대 투수 투구 방향 스플릿 반영
    - 스플릿 데이터 있음: split_ops 50% + recent_ops 50%
    - 스플릿 데이터 없음: season_ops 50% + recent_ops 50% (일반 분석과 동일)

    handedness: 상대 선발투수의 투구 방향 ('L' or 'R')
    """
    splits    = get_batter_splits(player_id, handedness)  # None or dict
    season    = get_batter_season(player_id)
    recent_gs = get_batter_recent(player_id, 10)

    # 시즌 OPS (splits 없을 때 fallback)
    try:
        season_ops = float(season.get("ops", 0.700) or 0.700)
    except Exception:
        season_ops = 0.700
    try:
        raw_avg = season.get("avg", "0.250") or "0.250"
        season_avg = float(raw_avg)
    except Exception:
        season_avg = 0.250

    # 최근 10경기 OPS
    total_ab = total_h = total_bb = total_tb = total_pa = 0
    for s in recent_gs:
        ab = int(s.get("atBats",      0) or 0)
        h  = int(s.get("hits",        0) or 0)
        bb = int(s.get("baseOnBalls", 0) or 0)
        tb = int(s.get("totalBases",  0) or 0)
        total_ab += ab
        total_h  += h
        total_bb += bb
        total_tb += tb
        total_pa += ab + bb

    recent_obp  = (total_h + total_bb) / total_pa if total_pa > 0 else 0.320
    recent_slg  = total_tb / total_ab              if total_ab > 0 else 0.380
    recent_ops  = round(recent_obp + recent_slg, 3)
    recent_avg  = round(total_h / total_ab, 3)    if total_ab > 0 else 0.250

    if splits:
        # ✅ 스플릿 데이터 사용: 상대 손 방향 반영
        base_ops     = splits["ops"]
        split_source = splits.get("season", "hist")
        blended_ops  = round(base_ops * 0.50 + recent_ops * 0.50, 3)
    else:
        # ⏳ 스플릿 없음: 일반 시즌OPS 사용
        base_ops     = season_ops
        split_source = None
        blended_ops  = round(season_ops * 0.50 + recent_ops * 0.50, 3)

    return {
        "player_id":    player_id,
        "base_ops":     round(base_ops, 3),
        "split_source": split_source,       # '2026'/'2025'/None
        "handedness":   handedness,
        "recent_ops":   recent_ops,
        "recent_avg":   recent_avg,
        "blended_ops":  blended_ops,
        "n_recent":     len(recent_gs),
    }
