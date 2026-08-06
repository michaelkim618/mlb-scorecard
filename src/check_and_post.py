#!/usr/bin/env python3
"""
MLB Scorecard — 스마트 자동 포스팅 스케줄러
check_and_post.py

매시간 실행 → 경기 시작 1시간 전 감지 → 자동 포스팅
하루 최대 3번 예측 포스팅 + 1번 결과 포스팅

Usage:
    python3 src/check_and_post.py              # 예측 포스팅 체크
    python3 src/check_and_post.py --results    # 결과 포스팅
"""

import os
import sys
import json
import argparse
import subprocess
import requests
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ── 경로 설정 ─────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"
STATE_DIR  = BASE_DIR / "output" / "post_state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

PST = ZoneInfo("America/Los_Angeles")
MLB_API = "https://statsapi.mlb.com/api/v1"

# 하루 최대 예측 포스팅 횟수
MAX_PREDICTION_POSTS = 3

# 경기 그룹핑 간격 (분) - 이 시간 내 경기는 같은 그룹으로 묶음
GROUP_WINDOW_MIN = 45

# 포스팅 트리거: 경기 시작 N분 전
TRIGGER_BEFORE_MIN = 100  # 최대 100분 전까지 감지 (매시간 실행 기준 완전 커버)
TRIGGER_AFTER_MIN  = 40   # 최소 40분 전


def get_state_file(game_date: str) -> Path:
    return STATE_DIR / f"posted_{game_date}.json"


def load_state(game_date: str) -> dict:
    f = get_state_file(game_date)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"predictions": [], "results": False}


def save_state(game_date: str, state: dict):
    get_state_file(game_date).write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_today_games(game_date: str) -> list:
    """MLB API에서 오늘 경기 목록 + 시작 시간(PST) 가져오기"""
    url = f"{MLB_API}/schedule"
    params = {"sportId": 1, "date": game_date, "hydrate": "probablePitcher,lineups"}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[오류] MLB API 호출 실패: {e}")
        return []

    games = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            if g.get("gameType") != "R":
                continue
            # UTC → PST 변환
            game_dt_str = g.get("gameDate", "")
            try:
                game_dt_utc = datetime.fromisoformat(game_dt_str.replace("Z", "+00:00"))
                game_dt_pst = game_dt_utc.astimezone(PST)
            except Exception:
                continue

            # 라인업 확정 여부
            lineups = g.get("lineups", {})
            lineup_confirmed = bool(
                lineups.get("awayPlayers") and lineups.get("homePlayers")
            )

            games.append({
                "gamePk":            g["gamePk"],
                "away":              g["teams"]["away"]["team"]["name"],
                "home":              g["teams"]["home"]["team"]["name"],
                "game_time_pst":     game_dt_pst,
                "game_time_str":     game_dt_pst.strftime("%I:%M %p PST"),
                "lineup_confirmed":  lineup_confirmed,
                "status":            g.get("status", {}).get("abstractGameState", "Preview"),
            })

    # 시작 시간순 정렬
    games.sort(key=lambda x: x["game_time_pst"])
    return games


def group_games_by_time(games: list) -> list[list]:
    """
    시간이 비슷한 경기들을 그룹으로 묶기
    GROUP_WINDOW_MIN 분 이내 = 같은 그룹
    """
    if not games:
        return []
    groups = []
    current_group = [games[0]]
    for g in games[1:]:
        diff = (g["game_time_pst"] - current_group[0]["game_time_pst"]).total_seconds() / 60
        if diff <= GROUP_WINDOW_MIN:
            current_group.append(g)
        else:
            groups.append(current_group)
            current_group = [g]
    groups.append(current_group)
    return groups


def should_post_group(group: list, now_pst: datetime) -> bool:
    """이 그룹이 지금 포스팅 타이밍인지 확인 (60~70분 전)"""
    first_game_time = group[0]["game_time_pst"]
    mins_until = (first_game_time - now_pst).total_seconds() / 60
    return TRIGGER_AFTER_MIN <= mins_until <= TRIGGER_BEFORE_MIN


def group_key(group: list) -> str:
    """그룹의 고유 키 (첫 경기 시간 기준)"""
    return group[0]["game_time_pst"].strftime("%H:%M")


def run_pipeline(game_date: str):
    """예측 파이프라인 실행"""
    print(f"  🔄 예측 파이프라인 실행 중... ({game_date})")
    result = subprocess.run(
        [sys.executable, "src/scorecard_pipeline.py", game_date],
        capture_output=True, text=True, cwd=str(BASE_DIR)
    )
    if result.returncode != 0:
        print(f"  [경고] 파이프라인 오류:\n{result.stderr[-500:]}")
    else:
        print(f"  ✅ 파이프라인 완료")


def run_slides(game_date: str, post_num: int):
    """슬라이드 생성"""
    print(f"  🎨 슬라이드 생성 중... (#{post_num}차 포스팅)")
    result = subprocess.run(
        [sys.executable, "instagram/generate_slides_v2.py", "--date", game_date, "--post-num", str(post_num)],
        capture_output=True, text=True, cwd=str(BASE_DIR)
    )
    if result.returncode != 0:
        print(f"  [경고] 슬라이드 오류:\n{result.stderr[-300:]}")


def run_instagram_post(game_date: str):
    """인스타그램 포스팅"""
    print(f"  📸 인스타그램 포스팅 중...")
    env = {**os.environ}
    result = subprocess.run(
        [sys.executable, "instagram/post_to_instagram.py", "--date", game_date],
        capture_output=True, text=True, cwd=str(BASE_DIR), env=env
    )
    if result.returncode != 0:
        print(f"  [경고] 인스타 오류:\n{result.stderr[-300:]}")
    else:
        print(f"  ✅ 인스타그램 포스팅 완료")


def run_twitter_post(game_date: str, post_type: str = "prediction"):
    """트위터 포스팅"""
    print(f"  🐦 트위터 포스팅 중... (type={post_type})")
    env = {**os.environ}
    result = subprocess.run(
        [sys.executable, "src/post_to_twitter.py", "--date", game_date, "--type", post_type],
        capture_output=True, text=True, cwd=str(BASE_DIR), env=env
    )
    if result.returncode != 0:
        print(f"  [경고] 트위터 오류:\n{result.stderr[-300:]}")
    else:
        print(f"  ✅ 트위터 포스팅 완료")


def copy_predictions_to_web(game_date: str):
    """predictions.json → 웹 레포에 복사 (라인업 업데이트 반영)"""
    src = OUTPUT_DIR / "predictions.json"
    # GitHub Actions 환경: mlb-scorecard-web 이 체크아웃되어 있음
    # 로컬 환경: 상위 폴더에 있음
    web_candidates = [
        BASE_DIR / "mlb-scorecard-web",          # GitHub Actions
        BASE_DIR.parent / "mlb-scorecard-web",   # 로컬
    ]
    for web_repo in web_candidates:
        dest = web_repo / "public" / "predictions.json"
        if web_repo.exists() and src.exists():
            import shutil
            shutil.copy2(src, dest)
            print(f"  📋 predictions.json → {dest} 복사 완료")
            return True
    print(f"  ⚠️  웹 레포 없음 — predictions.json 복사 스킵")
    return False


def needs_lineup_refresh(game_date: str, games: list) -> bool:
    """포스팅된 그룹 중 라인업이 새로 확정된 경기가 있는지 체크"""
    state = load_state(game_date)
    posted_groups = state.get("predictions", [])
    if not posted_groups:
        return False

    # 포스팅된 경기 중 라인업이 확정된 게 있고
    # 이전에 미확정 상태였다면 갱신 필요
    refreshed_groups = state.get("lineup_refreshed", [])
    for g in games:
        game_hour = g["game_time_pst"].strftime("%H:%M")
        if game_hour in posted_groups and g["lineup_confirmed"] and game_hour not in refreshed_groups:
            return True
    return False


def check_and_post_predictions(game_date: str):
    """예측 포스팅 체크 & 실행"""
    now_pst = datetime.now(PST)
    print(f"\n🕐 체크 시작: {now_pst.strftime('%Y-%m-%d %H:%M PST')}")

    state = load_state(game_date)
    posted_groups = state.get("predictions", [])

    if len(posted_groups) >= MAX_PREDICTION_POSTS:
        print(f"✅ 오늘 예측 포스팅 {MAX_PREDICTION_POSTS}회 완료. 추가 포스팅 없음.")
        return

    # 오늘 경기 목록 가져오기
    games = get_today_games(game_date)
    if not games:
        print("⚠️  오늘 경기 없음.")
        return

    print(f"📋 오늘 경기 {len(games)}게임:")
    for g in games:
        lineup_str = "✅ 라인업 확정" if g["lineup_confirmed"] else "⏳ 미확정"
        print(f"   {g['away']} @ {g['home']}  {g['game_time_str']}  {lineup_str}")

    # 게임 그룹핑
    groups = group_games_by_time(games)
    print(f"\n📦 경기 그룹 {len(groups)}개:")
    for i, group in enumerate(groups):
        key = group_key(group)
        first_time = group[0]["game_time_str"]
        print(f"   그룹 {i+1}: {len(group)}경기  첫 경기 {first_time}  key={key}")

    # ── 라인업 갱신 체크 (이미 포스팅된 그룹 중 새로 확정된 경우) ──────────────
    refreshed_groups = state.get("lineup_refreshed", [])
    lineup_refreshed = False
    for group in groups:
        key = group_key(group)
        if key not in posted_groups:
            continue  # 아직 포스팅 안 된 그룹은 나중에 처리
        if key in refreshed_groups:
            continue  # 이미 라인업 갱신됨
        new_confirmations = [g for g in group if g["lineup_confirmed"]]
        if new_confirmations:
            print(f"\n🔄 그룹 {key} 라인업 신규 확정 ({len(new_confirmations)}팀) → predictions.json 갱신")
            run_pipeline(game_date)
            copy_predictions_to_web(game_date)
            refreshed_groups.append(key)
            state["lineup_refreshed"] = refreshed_groups
            save_state(game_date, state)
            lineup_refreshed = True
            break  # 한 번에 하나씩

    # ── 신규 그룹 포스팅 찾기 ────────────────────────────────────────────────
    for group in groups:
        key = group_key(group)

        # 이미 포스팅한 그룹이면 스킵
        if key in posted_groups:
            if not lineup_refreshed:
                print(f"\n⏭  그룹 {key} 이미 포스팅됨. 스킵.")
            continue

        # 타이밍 체크 (60~70분 전)
        if not should_post_group(group, now_pst):
            first_time = group[0]["game_time_pst"]
            mins_until = (first_time - now_pst).total_seconds() / 60
            print(f"\n⏱  그룹 {key}: {mins_until:.0f}분 후 시작. 아직 포스팅 타이밍 아님.")
            continue

        # 포스팅 실행!
        post_num = len(posted_groups) + 1
        confirmed_count = sum(1 for g in group if g["lineup_confirmed"])
        total_count = len(group)
        print(f"\n🚀 포스팅 실행! 그룹 {key} | #{post_num}차 | 라인업 확정: {confirmed_count}/{total_count}")

        run_pipeline(game_date)
        copy_predictions_to_web(game_date)
        run_instagram_post(game_date)
        run_twitter_post(game_date, post_type="prediction")

        # 상태 저장
        posted_groups.append(key)
        state["predictions"] = posted_groups
        save_state(game_date, state)

        print(f"\n✅ {post_num}차 포스팅 완료! (오늘 총 {len(posted_groups)}/{MAX_PREDICTION_POSTS}회)")

        # 1개 그룹만 처리 후 종료 (다음 실행에서 다음 그룹 처리)
        break
    else:
        if not lineup_refreshed:
            print("\n✅ 지금 포스팅할 그룹 없음.")


def check_and_post_results(game_date: str):
    """결과 비교 포스팅"""
    print(f"\n📊 결과 포스팅 시작: {game_date}")

    state = load_state(game_date)
    if state.get("results"):
        print("✅ 오늘 결과 포스팅 이미 완료.")
        return

    # update_results.py 실행 (JS 파일 업데이트)
    print("  🔄 경기 결과 업데이트 중...")
    result = subprocess.run(
        [sys.executable, "src/update_results.py"],
        capture_output=True, text=True, cwd=str(BASE_DIR)
    )
    print(result.stdout[-300:] if result.stdout else "")

    # JS 파일 → predictions.json 동기화 (results 포스팅이 올바른 데이터 읽도록)
    import re as _re
    js_path = OUTPUT_DIR / f"predictions_{game_date}.js"
    if js_path.exists():
        try:
            content = js_path.read_text(encoding="utf-8")
            match = _re.search(r'=\s*(\[.*\])', content, re.DOTALL)
            if match:
                import json as _json
                games_data = _json.loads(match.group(1))
                pred_path = OUTPUT_DIR / "predictions.json"
                pred_path.write_text(_json.dumps(games_data, indent=2, ensure_ascii=False), encoding="utf-8")
                has_result = sum(1 for g in games_data if g.get("actual_winner"))
                print(f"  ✅ predictions.json 동기화 완료 (결과 있음: {has_result}/{len(games_data)}경기)")
        except Exception as e:
            print(f"  ⚠️  predictions.json 동기화 실패: {e}")

    # season_results.json 업데이트
    web_repo = BASE_DIR.parent / "mlb-scorecard-web"
    if web_repo.exists():
        subprocess.run(
            [sys.executable, "src/update_season_results.py", str(web_repo)],
            capture_output=True, text=True, cwd=str(BASE_DIR)
        )

    # 결과 포스팅 (텍스트 스타일)
    run_instagram_post(game_date)
    run_twitter_post(game_date, post_type="results")

    # 상태 저장
    state["results"] = True
    save_state(game_date, state)
    print("✅ 결과 포스팅 완료!")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="날짜 YYYY-MM-DD (기본: 오늘 PST)")
    parser.add_argument("--results", action="store_true", help="결과 포스팅 모드")
    args = parser.parse_args()

    if args.date:
        game_date = args.date
    else:
        game_date = datetime.now(PST).strftime("%Y-%m-%d")

    if args.results:
        # 결과 포스팅: 전날 경기 결과
        yesterday = (datetime.now(PST) - timedelta(days=1)).strftime("%Y-%m-%d")
        check_and_post_results(args.date or yesterday)
    else:
        check_and_post_predictions(game_date)


if __name__ == "__main__":
    main()
