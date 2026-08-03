"""
학습된 ML 모델로 오늘 경기 승률 예측
- win_predictor.pkl 로드
- 팀 승률 + 선발 ERA → 홈팀 승률 반환
"""
import pickle
import json
from pathlib import Path
from typing import Optional
import numpy as np

from mlb_stats_fetcher import get_pitcher_season, safe_float, get_standings_map
from historical_fetcher import get_pitcher_era, _load_era_cache, apply_era_correction

MODELS_DIR = Path(__file__).parent.parent / "models"
MODEL_PATH = MODELS_DIR / "win_predictor.pkl"
META_PATH  = MODELS_DIR / "model_meta.json"

_model = None   # lazy load


def is_available() -> bool:
    return MODEL_PATH.exists()


def _load_model():
    global _model
    if _model is None:
        with open(MODEL_PATH, "rb") as f:
            _model = pickle.load(f)
    return _model


def _get_starter_era(pitcher_id: Optional[int], season: int = 2025) -> float:
    """선발투수 ERA: 캐시 → 시즌 스탯 순서로 조회."""
    if not pitcher_id:
        return 4.50
    # 캐시 우선 (훈련 때 이미 수집됐을 가능성 높음)
    cache = _load_era_cache()
    key = f"{pitcher_id}_{season}"
    if key in cache:
        return cache[key]
    # 없으면 API 조회
    stats = get_pitcher_season(pitcher_id)
    era_str = stats.get("era", "")
    return float(era_str) if era_str and era_str not in ("-.--", "∞", "") else 4.50


def predict(
    away_wins: int, away_losses: int,
    home_wins: int, home_losses: int,
    away_pitcher_id: Optional[int] = None,
    home_pitcher_id: Optional[int] = None,
    season: int = 2025,
    away_run_diff_per_game: float = 0.0,
    home_run_diff_per_game: float = 0.0,
    away_pitcher_wins=None,
    away_pitcher_losses=None,
    home_pitcher_wins=None,
    home_pitcher_losses=None,
    away_pitcher_ip=None,
    home_pitcher_ip=None,
    away_streak: float = 0.0,
    home_streak: float = 0.0,
) -> dict:
    """
    ML 모델로 승률 예측.

    Returns:
        away_win_pct : float (0~100)
        home_win_pct : float (0~100)
        model        : "ml"
    """
    model = _load_model()

    away_wp = away_wins / (away_wins + away_losses) if (away_wins + away_losses) > 0 else 0.5
    home_wp = home_wins / (home_wins + home_losses) if (home_wins + home_losses) > 0 else 0.5

    away_era_raw = _get_starter_era(away_pitcher_id, season)
    home_era_raw = _get_starter_era(home_pitcher_id, season)
    # ERA 보정: 이닝 수가 적을수록 리그 평균에 가깝게
    away_ip = away_pitcher_ip if away_pitcher_ip is not None else 20.0
    home_ip = home_pitcher_ip if home_pitcher_ip is not None else 20.0
    away_era = apply_era_correction(away_era_raw, away_ip)
    home_era = apply_era_correction(home_era_raw, home_ip)
    # 극단값 클리핑 (학습 범위 맞추기, max 7.0)
    away_era = min(max(away_era, 0.5), 7.0)
    home_era = min(max(home_era, 0.5), 7.0)

    era_abs_diff = abs(away_era - home_era)
    away_era_bad = 1.0 if away_era >= 6.5 else 0.0
    home_era_bad = 1.0 if home_era >= 6.5 else 0.0
    run_diff_gap = home_run_diff_per_game - away_run_diff_per_game

    # Pitcher win pct (0.5 if unknown or no decisions)
    if away_pitcher_wins is not None and away_pitcher_losses is not None and (away_pitcher_wins + away_pitcher_losses) > 0:
        away_pwp = away_pitcher_wins / (away_pitcher_wins + away_pitcher_losses)
    else:
        away_pwp = 0.5

    if home_pitcher_wins is not None and home_pitcher_losses is not None and (home_pitcher_wins + home_pitcher_losses) > 0:
        home_pwp = home_pitcher_wins / (home_pitcher_wins + home_pitcher_losses)
    else:
        home_pwp = 0.5

    pitcher_wpdiff = home_pwp - away_pwp

    # 스트릭 클리핑
    away_streak_c = max(min(away_streak, 10), -10)
    home_streak_c = max(min(home_streak, 10), -10)
    streak_gap    = home_streak_c - away_streak_c

    X = np.array([[
        away_wp, home_wp, home_wp - away_wp,
        away_era, home_era, away_era - home_era,
        era_abs_diff, away_era_bad, home_era_bad,
        away_wins + away_losses, home_wins + home_losses,
        away_run_diff_per_game, home_run_diff_per_game, run_diff_gap,
        away_pwp, home_pwp, pitcher_wpdiff,
        away_ip, home_ip,
        away_streak_c, home_streak_c, streak_gap,
    ]], dtype=np.float32)

    prob = model.predict_proba(X)[0]   # [P(away_win), P(home_win)]
    home_p = float(prob[1]) * 100
    away_p = float(prob[0]) * 100

    return {
        "away_win_pct": round(away_p, 1),
        "home_win_pct": round(home_p, 1),
        "model": "ml",
    }


def get_model_meta() -> dict:
    if META_PATH.exists():
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    return {}


if __name__ == "__main__":
    if not is_available():
        print("모델 없음 — python3 main.py --train 먼저 실행")
    else:
        meta = get_model_meta()
        print(f"모델 로드 완료 (학습 경기: {meta.get('n_games')}, CV 정확도: {meta.get('cv_accuracy', 0):.1%})")

        # 테스트: NYY(62승31패, Rodón ERA 3.5) @ DET(50승43패, Mize ERA 3.8)
        result = predict(
            away_wins=62, away_losses=31,
            home_wins=50, home_losses=43,
            away_pitcher_id=None,
            home_pitcher_id=None,
        )
        print(f"NYY @ DET 예측: 원정 {result['away_win_pct']}% / 홈 {result['home_win_pct']}%")
