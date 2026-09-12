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
TRIGGER_BEFORE_MIN = 240  # 최대 240분(4시간) 전까지 감지 (GitHub Actions 크론 최대 3h 지연 대응)
TRIGGER_AFTER_MIN  = 40   # 최소 40분 전 (게임 직전 포스팅 방지)

# 라인업 확정 최소 비율: 이 비율 이상 확정돼야 포스팅 허용
# 0.0 = 라인업 없어도 포스팅, 1.0 = 전부 확정 시만 포스팅
# ★ 0.0으로 설정 시 예측 모드 유지 (라인업 없이도 포스팅)
#   경기 당일 AM에는 라인업이 늦게 발표되므로 0.0이 현실적
LINEUP_CONFIRM_THRESHOLD = 0.0


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
            detailed_state = g.get("status", {}).get("detailedState", "")
            abstract_state = g.get("status", {}).get("abstractGameState", "Preview")

            # Schedule API hydrate=lineups 버그 대응:
            # Live/Warmup/Final 상태이면 라인업이 이미 확정된 것으로 간주
            # ★ 수정: Pre-Game 제거 — 경기 시작 직전이지만 실제 라인업 데이터 없을 수 있음
            #   Pre-Game(30~60분 전) 단계에서 lineup_confirmed=True 로 과대평가하면
            #   파이프라인이 라인업 없는 상태로 재실행 → 개선 효과 없음
            game_started = abstract_state in ("Live", "Final") or \
                           detailed_state in ("Warmup", "In Progress", "Final")

            lineup_confirmed = game_started or bool(
                lineups.get("awayPlayers") and lineups.get("homePlayers")
            )

            games.append({
                "gamePk":            g["gamePk"],
                "away":              g["teams"]["away"]["team"]["name"],
                "home":              g["teams"]["home"]["team"]["name"],
                "game_time_pst":     game_dt_pst,
                "game_time_str":     game_dt_pst.strftime("%I:%M %p PST"),
                "lineup_confirmed":  lineup_confirmed,
                "status":            abstract_state,
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


def run_slides(game_date: str, post_num: int) -> bool:
    """슬라이드 생성 — 성공 여부 반환"""
    print(f"  🎨 슬라이드 생성 중... (#{post_num}차 포스팅)")
    result = subprocess.run(
        [sys.executable, "instagram/generate_slides_v2.py", "--date", game_date, "--post-num", str(post_num)],
        capture_output=True, text=True, cwd=str(BASE_DIR)
    )
    if result.returncode != 0:
        print(f"  ❌ 슬라이드 생성 실패 — 인스타 포스팅 차단:\n{result.stderr[-500:]}")
        return False
    print(f"  ✅ 슬라이드 생성 완료")
    return True


def run_instagram_post(game_date: str, post_type: str = "prediction") -> bool:
    """인스타그램 포스팅 — 성공 여부 반환"""
    # prediction 포스팅: 슬라이드 먼저 생성, 실패 시 포스팅 차단
    if post_type == "prediction":
        post_num = 1  # 기본값 (호출부에서 덮어씌울 수 없으므로 슬라이드는 별도 호출)
    print(f"  📸 인스타그램 포스팅 중... (type={post_type})")
    env = {**os.environ}
    result = subprocess.run(
        [sys.executable, "instagram/post_to_instagram.py", "--date", game_date, "--type", post_type],
        capture_output=True, text=True, cwd=str(BASE_DIR), env=env
    )
    if result.returncode != 0:
        print(f"  ❌ 인스타 포스팅 실패:\n{result.stderr[-500:]}")
        return False
    print(f"  ✅ 인스타그램 포스팅 완료")
    return True


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


# ── frozen 예측값 저장/복원 ────────────────────────────────────────────────────

FROZEN_FIELDS = [
    "win_prob", "model_winner", "value_bet", "edge",
    "sp_bat_conflict", "sp_bat_conflict_detail",
    "low_confidence", "low_confidence_reason", "extreme_edge",
]

def save_frozen_predictions(game_date: str, state: dict, frozen_group_key: str, group_games: list):
    """
    그룹이 frozen될 때 해당 그룹 경기들의 핵심 예측값을 state에 스냅샷 저장.
    이후 다른 그룹 파이프라인 실행 시 이 값으로 복원한다.
    """
    src = OUTPUT_DIR / "predictions.json"
    if not src.exists():
        return

    try:
        preds = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return
    if isinstance(preds, dict):
        preds = preds.get("games", [])

    # game_pk → prediction 맵
    pred_by_pk = {str(g.get("game_pk", "")): g for g in preds}

    frozen_preds = state.get("frozen_predictions", {})
    if frozen_group_key in frozen_preds:
        return  # 이미 저장됨

    saved = []
    for game in group_games:
        pk = str(game.get("gamePk", ""))
        if pk and pk in pred_by_pk:
            p = pred_by_pk[pk]
            snapshot = {"game_pk": pk}
            for f in FROZEN_FIELDS:
                snapshot[f] = p.get(f)
            saved.append(snapshot)
            print(f"   💾 frozen 스냅샷 저장: {game.get('away')} @ {game.get('home')} → {p.get('win_prob')}")

    if saved:
        frozen_preds[frozen_group_key] = saved
        state["frozen_predictions"] = frozen_preds
        print(f"   ✅ 그룹 {frozen_group_key} 예측값 {len(saved)}경기 스냅샷 완료")


def apply_frozen_predictions(game_date: str, state: dict):
    """
    파이프라인 재실행 후, frozen 그룹의 예측값을 저장된 스냅샷으로 복원.
    다른 그룹 처리 중 frozen 그룹 데이터가 덮어씌워지는 것을 방지한다.
    """
    frozen_preds = state.get("frozen_predictions", {})
    if not frozen_preds:
        return

    src = OUTPUT_DIR / "predictions.json"
    if not src.exists():
        return

    try:
        preds = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return
    if isinstance(preds, dict):
        preds = preds.get("games", [])

    pred_by_pk = {str(g.get("game_pk", "")): g for g in preds}

    restored = 0
    for key, saved_games in frozen_preds.items():
        for saved in saved_games:
            pk = str(saved.get("game_pk", ""))
            if pk and pk in pred_by_pk:
                pred = pred_by_pk[pk]
                for f in FROZEN_FIELDS:
                    if f in saved:
                        pred[f] = saved[f]
                restored += 1

    if restored:
        src.write_text(json.dumps(preds, indent=2, ensure_ascii=False), encoding="utf-8")
        copy_predictions_to_web(game_date)
        print(f"   🔒 frozen 예측값 복원 완료 ({restored}경기) — 이미 고정된 그룹 보호")


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

    # ── 라인업 갱신 & 예측 고정 체크 ──────────────────────────────────────────
    # 흐름:
    #   1) 라인업 일부 확정  → 파이프라인 재실행 (refreshed)
    #   2) 라인업 전체 확정  → 파이프라인 재실행 후 예측 고정 (frozen)
    #   3) frozen 그룹       → 어떤 경우에도 예측 변경 불가
    #   4) 경기 시작 60분 이내 미확정 잔류 시 → 1회 강제 재실행 후 고정
    refreshed_groups = state.get("lineup_refreshed", [])
    frozen_groups    = state.get("predictions_frozen", [])
    lineup_refreshed = False

    FORCE_REFRESH_MINS = 60  # 경기 시작 60분 전 이내 미확정 → 강제 1회 재갱신

    for group in groups:
        key = group_key(group)

        # ① 이미 고정된 그룹 → 완전 스킵
        if key in frozen_groups:
            print(f"   🔒 그룹 {key} 예측 고정됨 — 변경 불가")
            continue

        all_confirmed  = all(g["lineup_confirmed"] for g in group)
        any_confirmed  = any(g["lineup_confirmed"] for g in group)

        # ② 경기 시작 60분 이내 미확정 경기 → refreshed여도 강제 1회 재갱신 허용
        unconfirmed_near_start = [
            g for g in group
            if not g["lineup_confirmed"]
            and 0 <= (g["game_time_pst"] - now_pst).total_seconds() / 60 <= FORCE_REFRESH_MINS
        ]
        if unconfirmed_near_start and key in refreshed_groups:
            print(f"\n⚠️ 그룹 {key}: 경기 {FORCE_REFRESH_MINS}분 이내 미확정 {len(unconfirmed_near_start)}경기 → 강제 재갱신 후 고정")
            refreshed_groups.remove(key)  # 재갱신 허용 (이후 바로 고정)

        # ③ 이미 갱신 완료 → 고정 여부만 판단
        if key in refreshed_groups:
            if all_confirmed:
                # 전 경기가 Live/Final 전환 → 파이프라인 1회 더 실행하여
                # lineup_confirmed / status 를 최신 값으로 갱신한 뒤 고정
                print(f"\n🔄 그룹 {key} 전 경기 Live/Final 확인 → status/lineup_confirmed 최종 갱신 후 고정")
                run_pipeline(game_date)
                apply_frozen_predictions(game_date, state)
                copy_predictions_to_web(game_date)
                frozen_groups.append(key)
                state["predictions_frozen"] = frozen_groups
                # ★ frozen 시점의 예측값 스냅샷 저장
                save_frozen_predictions(game_date, state, key, group)
                save_state(game_date, state)
                print(f"   🔒 그룹 {key} 전 경기 라인업 확정 → 예측 고정 완료")
            continue

        # ④ 라인업이 하나라도 확정됐으면 파이프라인 재실행
        if any_confirmed:
            action = "포스팅 전" if key not in posted_groups else "포스팅 후"
            confirmed_n = sum(1 for g in group if g["lineup_confirmed"])
            total_n = len(group)
            print(f"\n🔄 그룹 {key} 라인업 확정 ({confirmed_n}/{total_n}, {action}) → predictions.json 갱신")
            run_pipeline(game_date)
            # ★ 파이프라인 실행 후 frozen 그룹 예측값 즉시 복원 (덮어쓰기 방지)
            apply_frozen_predictions(game_date, state)
            copy_predictions_to_web(game_date)
            refreshed_groups.append(key)
            state["lineup_refreshed"] = refreshed_groups

            if all_confirmed:
                # 전체 확정이면 즉시 고정 + 스냅샷 저장
                frozen_groups.append(key)
                state["predictions_frozen"] = frozen_groups
                save_frozen_predictions(game_date, state, key, group)
                print(f"   🔒 그룹 {key} 전 경기 라인업 확정 → 즉시 예측 고정")

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

        # 이미 고정된 그룹은 파이프라인 재실행 없이 포스팅만 (예측값 보존)
        if key not in frozen_groups:
            run_pipeline(game_date)
            # ★ 파이프라인 실행 후 frozen 그룹 예측값 즉시 복원 (덮어쓰기 방지)
            apply_frozen_predictions(game_date, state)
            copy_predictions_to_web(game_date)
            # 포스팅 시점에 전체 확정 → 고정 + 스냅샷 저장
            if all(g["lineup_confirmed"] for g in group):
                frozen_groups.append(key)
                state["predictions_frozen"] = frozen_groups
                save_frozen_predictions(game_date, state, key, group)
                print(f"   🔒 포스팅 완료 시점에 예측 고정")
        else:
            print(f"   🔒 예측 고정 상태 — 파이프라인 재실행 없이 현재 예측값으로 포스팅")

        # 라인업 확정 상태 로깅
        if confirmed_count == 0:
            print(f"  ⚠️  라인업 미확정 상태로 포스팅 (경기 전 예측 기반)")
        else:
            print(f"  ✅ 라인업 확정 {confirmed_count}/{total_count} 상태로 포스팅")

        # 슬라이드 생성 먼저 → 성공 시에만 인스타 포스팅
        slides_ok = run_slides(game_date, post_num)
        if slides_ok:
            run_instagram_post(game_date)
        else:
            print(f"  ⚠️  슬라이드 실패로 인스타 포스팅 스킵")

        # 트위터 예측 트윗: 하루 1번만 (첫 포스팅 그룹에만 전송)
        if not posted_groups:  # 오늘 첫 포스팅일 때만
            run_twitter_post(game_date, post_type="prediction")
        else:
            print(f"  🐦 트위터 예측 트윗 스킵 (오늘 {len(posted_groups)}차 이미 발송)")

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

    # ── Live/Final 경기 lineup_confirmed 자동 동기화 ──────────────────────────
    # 포스팅·갱신이 없더라도 경기가 Live/Final로 전환됐으면 predictions.json 갱신
    # (포스팅 완료·고정 후에도 30분마다 status 동기화 보장)
    if not lineup_refreshed:
        pred_path = OUTPUT_DIR / "predictions.json"
        if pred_path.exists():
            try:
                preds_data = json.loads(pred_path.read_text(encoding="utf-8"))
                preds_list = preds_data.get("games", []) if isinstance(preds_data, dict) else preds_data
                pred_map = {str(g.get("game_pk", "")): g for g in preds_list}

                # Live API 기준 confirmed인데 predictions.json은 아직 False인 경기 탐색
                needs_sync = any(
                    g.get("lineup_confirmed")
                    and not pred_map.get(str(g["gamePk"]), {}).get("lineup_confirmed")
                    for g in games
                )
                if needs_sync:
                    unsynced = [
                        f"{g['away']} @ {g['home']}"
                        for g in games
                        if g.get("lineup_confirmed")
                        and not pred_map.get(str(g["gamePk"]), {}).get("lineup_confirmed")
                    ]
                    print(f"\n🔄 Live/Final 경기 lineup_confirmed 미동기화 {len(unsynced)}건 감지 → 파이프라인 재실행")
                    for u in unsynced:
                        print(f"   - {u}")
                    run_pipeline(game_date)
                    apply_frozen_predictions(game_date, state)
                    copy_predictions_to_web(game_date)
                    print("   ✅ lineup_confirmed 동기화 완료")
            except Exception as _sync_e:
                print(f"  ⚠️ 동기화 체크 오류: {_sync_e}")


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
            match = _re.search(r'=\s*(\[.*\])', content, _re.DOTALL)
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

    # 결과 포스팅
    run_instagram_post(game_date, post_type="results")  # 인스타그램 결과 포스팅
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
