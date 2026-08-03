"""
ML 모델 학습
- 과거 시즌 데이터로 HistGradientBoostingClassifier 학습
- 피처: 팀 승률, 선발 ERA, 파생 피처
- 출력: models/win_predictor.pkl + models/model_meta.json
"""
import json
import pickle
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import cross_val_score, StratifiedKFold
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

sys.path.insert(0, str(Path(__file__).parent))
from historical_fetcher import (fetch_all_seasons, enrich_with_era, enrich_with_run_diff,
                                 enrich_with_pitcher_wl, enrich_with_streak, apply_era_correction)

MODELS_DIR = Path(__file__).parent.parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

MODEL_PATH = MODELS_DIR / "win_predictor.pkl"
META_PATH  = MODELS_DIR / "model_meta.json"

# 학습에 사용할 피처 목록
FEATURE_NAMES = [
    "away_win_pct",           # 원정팀 승률
    "home_win_pct",           # 홈팀 승률
    "win_pct_diff",           # 홈 - 원정 승률 차이
    "away_starter_era",       # 원정 선발 ERA
    "home_starter_era",       # 홈 선발 ERA
    "era_diff",               # 원정ERA - 홈ERA (양수=홈 선발 유리)
    "era_abs_diff",           # abs(원정ERA - 홈ERA)
    "away_era_bad",           # 1 if 원정ERA >= 6.5
    "home_era_bad",           # 1 if 홈ERA >= 6.5
    "away_games_played",      # 원정팀 경기수 (시즌 초반 불확실성 반영)
    "home_games_played",      # 홈팀 경기수
    "away_run_diff_per_game", # 원정팀 누적 득실차/경기 (경기 전)
    "home_run_diff_per_game", # 홈팀 누적 득실차/경기 (경기 전)
    "run_diff_gap",           # home_run_diff - away_run_diff
    "away_pitcher_win_pct",   # 원정 선발 투수 시즌 승률 (wins/(wins+losses), 0.5 if unknown)
    "home_pitcher_win_pct",   # 홈 선발 투수 시즌 승률
    "pitcher_win_pct_diff",   # home - away 투수 승률 차이
    "away_pitcher_ip",        # 원정 선발 투수 시즌 이닝 (소수 등판 보정용)
    "home_pitcher_ip",        # 홈 선발 투수 시즌 이닝
    "away_streak",            # 원정팀 연승(+)/연패(-) 스트릭
    "home_streak",            # 홈팀 연승(+)/연패(-) 스트릭
    "streak_gap",             # home_streak - away_streak
]


def build_features(games: list) -> tuple:
    """
    경기 리스트 → (X: ndarray, y: ndarray)
    조건 미충족 경기(팀 기록 없음 등) 자동 제외
    """
    rows, labels = [], []

    for g in games:
        aw = g.get("away_wins", 0)
        al = g.get("away_losses", 0)
        hw = g.get("home_wins", 0)
        hl = g.get("home_losses", 0)

        # 첫 경기(둘 다 0-0)는 승률 의미 없으므로 제외
        if (aw + al) < 5 or (hw + hl) < 5:
            continue

        away_wp = aw / (aw + al)
        home_wp = hw / (hw + hl)

        # ERA: 이미 enrich_with_era()에서 보정됐지만, 만약 ip 정보가 있으면 재보정
        away_era_raw = g.get("away_starter_era", 4.50)
        home_era_raw = g.get("home_starter_era", 4.50)
        away_ip = g.get("away_starter_ip", g.get("away_pitcher_ip", 20.0))
        home_ip = g.get("home_starter_ip", g.get("home_pitcher_ip", 20.0))
        away_era = apply_era_correction(away_era_raw, away_ip)
        home_era = apply_era_correction(home_era_raw, home_ip)
        # ERA 극단값 클리핑 (아웃라이어 영향 방지, max 7.0)
        away_era = min(max(away_era, 0.5), 7.0)
        home_era = min(max(home_era, 0.5), 7.0)

        era_abs_diff = abs(away_era - home_era)
        away_era_bad = 1.0 if away_era >= 6.5 else 0.0
        home_era_bad = 1.0 if home_era >= 6.5 else 0.0
        away_rdpg = g.get("away_run_diff_per_game", 0.0)
        home_rdpg = g.get("home_run_diff_per_game", 0.0)
        run_diff_gap = home_rdpg - away_rdpg

        # 투수 W/L: enrich_with_pitcher_wl()로 채워진 실제 데이터 우선 사용
        a_pw = g.get("away_pitcher_wins")
        a_pl = g.get("away_pitcher_losses")
        h_pw = g.get("home_pitcher_wins")
        h_pl = g.get("home_pitcher_losses")
        if a_pw is not None and a_pl is not None and (a_pw + a_pl) > 0:
            away_pwp = a_pw / (a_pw + a_pl)
        else:
            away_pwp = 0.5
        if h_pw is not None and h_pl is not None and (h_pw + h_pl) > 0:
            home_pwp = h_pw / (h_pw + h_pl)
        else:
            home_pwp = 0.5
        pitcher_wpdiff = home_pwp - away_pwp

        # 투수 이닝 피처 (학습용)
        away_p_ip = g.get("away_pitcher_ip", away_ip)
        home_p_ip = g.get("home_pitcher_ip", home_ip)

        # 스트릭: -10~+10 클리핑 (극단값 방지)
        away_streak = max(min(g.get("away_streak", 0), 10), -10)
        home_streak  = max(min(g.get("home_streak", 0), 10), -10)
        streak_gap   = home_streak - away_streak

        row = [
            away_wp,
            home_wp,
            home_wp - away_wp,
            away_era,
            home_era,
            away_era - home_era,
            era_abs_diff,
            away_era_bad,
            home_era_bad,
            aw + al,
            hw + hl,
            away_rdpg,
            home_rdpg,
            run_diff_gap,
            away_pwp,
            home_pwp,
            pitcher_wpdiff,
            away_p_ip,
            home_p_ip,
            away_streak,
            home_streak,
            streak_gap,
        ]
        rows.append(row)
        labels.append(g["home_win"])

    return np.array(rows, dtype=np.float32), np.array(labels, dtype=np.int8)


def train(years: list = None, verbose: bool = True) -> dict:
    """
    전체 학습 파이프라인. 완료 후 모델 저장.
    Returns: 평가 지표 dict
    """
    if years is None:
        years = [2023, 2024, 2025]

    # ── 1. 데이터 수집 ────────────────────────────────────────────
    if verbose:
        print(f"\n{'='*55}")
        print("MLB ML 모델 학습")
        print(f"{'='*55}")
        print(f"\n[1/4] 과거 데이터 수집: {years}")

    games = fetch_all_seasons(years)
    games = enrich_with_streak(games)
    games = enrich_with_run_diff(games)
    games = enrich_with_era(games, verbose=verbose)
    games = enrich_with_pitcher_wl(games, verbose=verbose)

    # ── 2. 피처 생성 ──────────────────────────────────────────────
    if verbose:
        print(f"\n[2/4] 피처 엔지니어링…")

    X, y = build_features(games)

    if verbose:
        print(f"  유효 경기: {len(X)} / {len(games)}")
        print(f"  피처 수: {X.shape[1]}")
        print(f"  홈팀 승률 (실제): {y.mean():.3f}")

    # ── 3. 모델 학습 ──────────────────────────────────────────────
    if verbose:
        print(f"\n[3/4] 모델 학습 (HistGradientBoosting + 확률 보정)…")

    # HistGradientBoosting: sklearn 내장 gradient boosting (XGBoost급)
    base = HistGradientBoostingClassifier(
        max_iter=500,
        max_depth=4,
        learning_rate=0.05,
        min_samples_leaf=40,
        l2_regularization=3.0,
        random_state=42,
    )
    # CalibratedClassifierCV: 확률 출력을 더 정확하게 보정
    model = CalibratedClassifierCV(base, cv=5, method="isotonic")
    model.fit(X, y)

    # ── 4. 교차검증 평가 ──────────────────────────────────────────
    if verbose:
        print(f"\n[4/4] 성능 평가…")

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    # 정확도 CV
    acc_scores = cross_val_score(
        HistGradientBoostingClassifier(
            max_iter=500, max_depth=4, learning_rate=0.05,
            min_samples_leaf=40, l2_regularization=3.0, random_state=42
        ),
        X, y, cv=cv, scoring="accuracy"
    )

    # 전체 데이터 예측
    y_prob = model.predict_proba(X)[:, 1]
    y_pred = (y_prob >= 0.5).astype(int)

    metrics = {
        "n_games":       int(len(X)),
        "years":         years,
        "cv_accuracy":   float(acc_scores.mean()),
        "cv_accuracy_std": float(acc_scores.std()),
        "train_accuracy": float(accuracy_score(y, y_pred)),
        "log_loss":      float(log_loss(y, y_prob)),
        "brier_score":   float(brier_score_loss(y, y_prob)),
        "home_win_rate": float(y.mean()),
        "feature_names": FEATURE_NAMES,
    }

    if verbose:
        print(f"\n  ╔══════════════════════════════════════╗")
        print(f"  ║  학습 경기 수    : {metrics['n_games']:>6}경기          ║")
        print(f"  ║  CV 정확도 (5폴드): {metrics['cv_accuracy']:.1%} ± {metrics['cv_accuracy_std']:.1%}   ║")
        print(f"  ║  훈련 정확도     : {metrics['train_accuracy']:.1%}              ║")
        print(f"  ║  Log-Loss        : {metrics['log_loss']:.4f}             ║")
        print(f"  ║  Brier Score     : {metrics['brier_score']:.4f}             ║")
        print(f"  ╚══════════════════════════════════════╝")
        print(f"\n  ※ MLB 경기 예측 학계 벤치마크: 약 55~58% (홈팀 승률 편향 포함)")

    # ── 5. 저장 ───────────────────────────────────────────────────
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)
    META_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    if verbose:
        print(f"\n✅ 모델 저장: {MODEL_PATH}")
        print(f"✅ 메타 저장: {META_PATH}")

    return metrics


if __name__ == "__main__":
    import sys
    years = [int(y) for y in sys.argv[1:]] if len(sys.argv) > 1 else [2023, 2024, 2025]
    train(years)
