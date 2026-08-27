"""
MLB 예측 파이프라인 진입점

사용법:
  python3 main.py                             # 오늘 예측 (스코어카드 모델, 기본)
  python3 main.py --date 2026-06-23           # 특정 날짜 예측
  python3 main.py --mode blend                # 기존 ML+MC 블렌드 모드
  python3 main.py --mode scorecard            # 스코어카드 모드 (기본)
  python3 main.py --train                     # 과거 데이터로 ML 모델 (재)학습
  python3 main.py --train --years 2024 2025   # 특정 시즌만 학습
"""
import sys
import argparse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))


def cmd_train(years):
    from ml_trainer import train
    train(years=years)


def cmd_predict_blend(game_date):
    from predict_pipeline import run
    import ml_predictor

    results = run(game_date)
    if not results:
        return

    value_bets = [r for r in results if r["value_bet"].startswith("✅")]
    avoid_bets = [r for r in results if r["value_bet"].startswith("⚠️")]
    no_market  = [r for r in results if r["value_bet"] == "마켓 없음"]
    completed  = [r for r in results if r["model_correct"] is not None]
    hits       = [r for r in completed if r["model_correct"]]

    mode = "ML 모델" if ml_predictor.is_available() else "Monte Carlo"
    print(f"\n{'─'*50}")
    print(f"결과 요약 ({game_date})  [{mode}]")
    print(f"{'─'*50}")
    print(f"  Value Bet 후보 : {len(value_bets)}경기")
    for r in value_bets:
        print(f"    → {r['away']} @ {r['home']}  edge={r['edge']}%p")
    print(f"  회피 권장      : {len(avoid_bets)}경기")
    print(f"  Kalshi 마켓 없음: {len(no_market)}경기")
    if completed:
        pct = len(hits) / len(completed) * 100
        print(f"  모델 적중률    : {len(hits)}/{len(completed)} ({pct:.0f}%)")
    print(f"\n→ dashboard.html 을 브라우저에서 열어 확인하세요.")


def cmd_update_results(verbose: bool = True):
    """과거 미완료 날짜의 경기 결과를 MLB API에서 가져와 업데이트"""
    from update_results import update_all_pending
    print("📊 과거 경기 결과 업데이트 중...")
    n = update_all_pending(days_back=60, verbose=verbose)
    if n == 0:
        print("  ✅ 모든 결과 최신 상태")
    else:
        print(f"  ✅ {n}개 날짜 결과 업데이트 완료")


def cmd_deploy_web(game_date: str):
    """
    웹사이트 자동 배포:
    mlb-scorecard-web 의 public/predictions.json → GitHub push
    scorecard_pipeline.run() 이 이미 파일을 동기화한 뒤 이 함수를 호출.
    """
    import subprocess
    from pathlib import Path

    WEB_DIR = Path(__file__).parent.parent / "mlb-scorecard-web"
    if not WEB_DIR.exists():
        print(f"  [🌐 배포] {WEB_DIR} 없음 — 스킵")
        return

    # git 상태 확인
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=WEB_DIR, capture_output=True, text=True
    )
    if not status.stdout.strip():
        print("  [🌐 배포] 변경사항 없음 — 스킵")
        return

    print(f"  [🌐 배포] GitHub push 시작...")

    # unstaged 변경사항 stash → pull → stash pop → add → commit → push
    cmds = [
        ["git", "stash"],
        ["git", "pull", "--rebase", "origin", "main"],
        ["git", "stash", "pop"],
        ["git", "add", "public/predictions.json"],
        ["git", "commit", "-m", f"📊 Auto-update: {game_date} (prediction)"],
        ["git", "push", "origin", "main"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, cwd=WEB_DIR, capture_output=True, text=True)
        out = r.stdout + r.stderr
        # "nothing to commit" / "No local changes" 는 정상 케이스
        if r.returncode != 0 and not any(s in out for s in [
            "nothing to commit", "No local changes", "No stash entries"
        ]):
            print(f"  [🌐 배포] ⚠️ {' '.join(cmd)} 실패:\n{r.stderr.strip()}")
            return
    print(f"  [🌐 배포] ✅ GitHub push 완료 — 사이트 자동 업데이트 중")


def cmd_predict_scorecard(game_date):
    from scorecard_pipeline import run

    # 예측 전에 과거 결과 자동 업데이트
    cmd_update_results(verbose=True)

    results = run(game_date)  # 내부에서 public/predictions.json 자동 동기화
    if not results:
        return

    value_bets = [r for r in results if r["value_bet"].startswith("✅")]
    avoid_bets = [r for r in results if r["value_bet"].startswith("⚠️")]
    no_market  = [r for r in results if r["value_bet"] == "마켓 없음"]
    completed  = [r for r in results if r["model_correct"] is not None]
    hits       = [r for r in completed if r["model_correct"]]

    print(f"\n{'─'*50}")
    print(f"결과 요약 ({game_date}) [스코어카드 모델]")
    print(f"{'─'*50}")
    print(f"  Value Bet 후보 : {len(value_bets)}경기")
    for r in value_bets:
        print(f"    → {r['away']} @ {r['home']}  edge={r['edge']}%p")
    print(f"  회피 권장      : {len(avoid_bets)}경기")
    print(f"  Kalshi 마켓 없음: {len(no_market)}경기")
    if completed:
        pct = len(hits) / len(completed) * 100
        print(f"  모델 적중률    : {len(hits)}/{len(completed)} ({pct:.0f}%)")
    print(f"\n→ dashboard.html 을 브라우저에서 열어 확인하세요.")

    # 웹사이트 자동 배포 (GitHub push)
    cmd_deploy_web(game_date)


def main():
    parser = argparse.ArgumentParser(description="MLB 예측 대시보드")
    parser.add_argument("--date", "-d", type=str,
                        default=date.today().isoformat(),
                        help="예측 날짜 (YYYY-MM-DD)")
    parser.add_argument("--mode", choices=["blend", "scorecard"], default="scorecard",
                        help="예측 모드: blend=기존 ML+MC, scorecard=스코어카드 (기본)")
    parser.add_argument("--train", action="store_true",
                        help="과거 데이터로 ML 모델 학습/재학습")
    parser.add_argument("--years", type=int, nargs="+",
                        default=[2023, 2024, 2025],
                        help="학습에 사용할 시즌 (기본: 2023 2024 2025)")
    parser.add_argument("--force-fetch", action="store_true",
                        help="캐시 무시하고 과거 데이터 재수집")
    parser.add_argument("--update-results", action="store_true",
                        help="과거 예측 파일에 실제 경기 결과만 업데이트 (예측 재실행 없음)")
    args = parser.parse_args()

    if args.update_results:
        cmd_update_results(verbose=True)
        return

    if args.train:
        if args.force_fetch:
            from historical_fetcher import fetch_all_seasons, enrich_with_era
            fetch_all_seasons(args.years, force=True)
        cmd_train(args.years)
    elif args.mode == "scorecard":
        cmd_predict_scorecard(args.date)
    else:
        cmd_predict_blend(args.date)


if __name__ == "__main__":
    main()
