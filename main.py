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


def cmd_update_season_results():
    """season_results.json 업데이트 (예측 결과 → 누적 정확도)"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from update_season_results import update
    print("📈 Season Accuracy 업데이트 중...")
    try:
        update()
        print("  ✅ season_results.json 업데이트 완료")
    except Exception as e:
        print(f"  ⚠️ season_results 업데이트 실패: {e}")


def cmd_generate_blog(game_date: str):
    """어제 경기 결과를 바탕으로 블로그 포스트 자동 생성"""
    import sys
    from pathlib import Path
    from datetime import datetime, timedelta
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    WEB_REPO = Path(__file__).parent.parent / "mlb-scorecard-web"
    if not WEB_REPO.exists():
        print(f"  [📝 블로그] {WEB_REPO} 없음 — 스킵")
        return

    # 블로그 포스트는 "어제" 경기 결과로 생성 (결과 확정 후)
    from datetime import date
    today = datetime.strptime(game_date, "%Y-%m-%d").date()
    yesterday = today - timedelta(days=1)
    blog_date = yesterday.isoformat()

    # 이미 생성된 포스트인지 확인
    blog_file = WEB_REPO / "public" / "blog" / f"{blog_date}.json"
    if blog_file.exists():
        print(f"  [📝 블로그] {blog_date} 포스트 이미 존재 — 스킵")
        return

    print(f"  [📝 블로그] {blog_date} 포스트 생성 중...")
    try:
        from generate_blog_post import generate_daily_post, update_index
        generate_daily_post(blog_date, WEB_REPO)
        update_index(WEB_REPO)
        print(f"  [📝 블로그] ✅ {blog_date} 포스트 생성 완료")
    except Exception as e:
        print(f"  [📝 블로그] ⚠️ 생성 실패: {e}")


def cmd_deploy_web(game_date: str):
    """
    웹사이트 자동 배포:
    mlb-scorecard-web 의 public/ 변경사항 → GitHub push
    predictions.json, season_results.json, blog/ 모두 포함
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

    # fetch + merge -X ours 방식 (rebase 대신):
    # rebase는 staged 변경사항 있을 때 "cannot pull with rebase" 오류 발생
    # merge -X ours: remote 변경 우선(news.json 등) + 우리 파일 충돌 시 우리 것 유지

    def run_git(args, check=False):
        r = subprocess.run(["git"] + args, cwd=WEB_DIR, capture_output=True, text=True)
        return r

    # 1. 우리가 변경한 파일만 stage
    run_git(["add", "public/predictions.json", "public/season_results.json", "public/blog/"])

    # 2. commit (nothing to commit이면 스킵)
    r = run_git(["commit", "-m", f"📊 Auto-update: {game_date} (predictions + season + blog)"])
    if r.returncode != 0 and "nothing to commit" not in r.stdout + r.stderr:
        print(f"  [🌐 배포] ⚠️ commit 실패:\n{r.stderr.strip()}")
        return

    # 3. fetch → merge -X ours (충돌 시 우리 것 우선, rebase 오류 없음)
    run_git(["fetch", "origin", "main"])
    r = run_git(["merge", "origin/main", "--no-edit", "-X", "ours"])
    if r.returncode != 0:
        print(f"  [🌐 배포] ⚠️ merge 실패:\n{r.stderr.strip()}")
        return

    # 4. push
    r = run_git(["push", "origin", "main"])
    if r.returncode != 0:
        print(f"  [🌐 배포] ⚠️ push 실패:\n{r.stderr.strip()}")
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

    # Season Accuracy 업데이트
    cmd_update_season_results()

    # 어제 블로그 포스트 자동 생성
    cmd_generate_blog(game_date)

    # 웹사이트 자동 배포 (GitHub push) — predictions + season + blog 한번에
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
