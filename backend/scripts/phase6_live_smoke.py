"""
Phase 6.5 — 단일 펫 라이브 스모크 테스트 러너.

⚠️ 이 스크립트는 **실 결제 호출**을 만든다. 3중 안전장치:
  1. --confirm 없으면 절대 생성하지 않는다 (preflight 만).
  2. 프로바이더 키가 없으면 refuse — 자격 증명 없이 라이브 호출은 없다.
  3. PHASE6_LIVE_MODE (off/allowlist/all) 게이트는 서비스 계층이 다시 검사한다.

사용:
  # 설정 점검만 (호출 없음, 과금 없음)
  python -m backend.scripts.phase6_live_smoke --preflight

  # 단일 펫 스모크 — BREATHING(Seedance), LIE_DOWN(Kling, 두 키프레임 PASS 시),
  # PET_HEAD(Kling)
  PHASE6_LIVE_MODE=allowlist PHASE6_LIVE_ALLOWLIST=pet_<cid> \
    python -m backend.scripts.phase6_live_smoke \
      --user-id you@example.com --pet-id pet_<cid> --confirm

  # 저하 모드 로코모션 (모션 레퍼런스 라이브러리 없음 — 상업 준비 증거 아님)
  ... --degraded-locomotion

검증 항목(호출당): 요청 수락 / 모델 id / job id / 영상 디코드 / 종횡비 / 해상도 /
길이 / 오디오 없음 / 스토리지 경로 / QA 실행·판정 / 후보 persist / 근거 사슬.
결과는 pet_motion_* 테이블 + 대장에 그대로 남아 사람 평가(evaluations API)로 이어진다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# backend/main.py 의 .env 캐스케이드(.env → env.local → .env.local → backend/*)를
# 그대로 로드한다 — 셸에 없는 키가 .env 파일에만 있어도 preflight 가 본다.
import backend.main  # noqa: E402,F401 — import 부수효과로 dotenv 로드

SMOKE_MOTIONS = ("BREATHING", "LIE_DOWN", "PET_HEAD")
DEGRADED_LOCOMOTION = ("COME_CLOSER",)


def preflight() -> bool:
    from backend.services import video_motion_providers as vp
    from backend.services import vlm_identity

    def flag(name: str) -> str:
        return "SET" if (os.getenv(name) or "").strip() else "absent"

    seedance = vp.get_provider("seedance")
    kling = vp.get_provider("kling")
    rows = [
        ("SEEDANCE_API_KEY / ARK_API_KEY", flag("SEEDANCE_API_KEY") if os.getenv("SEEDANCE_API_KEY") else flag("ARK_API_KEY")),
        ("  seedance model", seedance.model_name()),
        ("  seedance base", os.getenv("SEEDANCE_API_BASE", "https://ark.ap-southeast.bytepluses.com/api/v3")),
        ("KLING_ACCESS_KEY", flag("KLING_ACCESS_KEY")),
        ("KLING_SECRET_KEY", flag("KLING_SECRET_KEY")),
        ("  kling model", kling.model_name()),
        ("  kling base", os.getenv("KLING_API_BASE", "https://api-singapore.klingai.com")),
        ("PHASE6_LIVE_MODE", os.getenv("PHASE6_LIVE_MODE", "off")),
        ("PHASE6_LIVE_ALLOWLIST", os.getenv("PHASE6_LIVE_ALLOWLIST", "(empty)")),
        ("PHASE6_ASPECT_RATIO", os.getenv("PHASE6_ASPECT_RATIO", "9:16 (펫 전용 자산 정본)")),
        ("PHASE6_RESOLUTION", os.getenv("PHASE6_RESOLUTION", "720p")),
        ("PHASE6_MAX_PRIMARY/FALLBACK/STOP", f"{os.getenv('PHASE6_MAX_PRIMARY', '3')}/{os.getenv('PHASE6_MAX_FALLBACK', '2')}/{os.getenv('PHASE6_STOP_AFTER_PASSES', '1')}"),
        ("audio", "항상 off (출력 규격 검증이 오디오 스트림 존재 시 FAIL)"),
        ("VIDEO_GENERATION_MOCK", os.getenv("VIDEO_GENERATION_MOCK", "0")),
        ("PET_VLM_IDENTITY_ENABLED (QA 확언)", "on" if vlm_identity.is_enabled() else "off — 자동 QA 는 REVIEW 상한"),
        ("SUPABASE_URL / SERVICE_ROLE_KEY", f"{flag('SUPABASE_URL')} / {flag('SUPABASE_SERVICE_ROLE_KEY')}"),
    ]
    print("── Phase 6.5 preflight ──────────────────────────────────────")
    for k, v in rows:
        print(f"  {k:<40} {v}")

    ok = seedance.available() and kling.available()
    if not ok:
        print("\n  ✗ 프로바이더 자격 증명이 없다 — 라이브 호출 불가.")
    return ok


async def run_motion(user_id: str, pet_id: str, motion_id: str, *, degraded: bool = False):
    from backend.services import motion_video_service as mv
    from backend.services import motion_video_qa as qa

    label = " [DEGRADED_NO_MOTION_REFERENCE]" if degraded else ""
    print(f"\n── {motion_id}{label} ─────────────────────────────────────")
    try:
        v = await mv.build_motion_video(user_id=user_id, pet_id=pet_id, motion_id=motion_id)
    except mv.MotionVideoError as e:
        print(f"  ✗ {e.code}: {e.message}")
        return None

    print(f"  version={v.version} status={v.status} strategy={v.video_strategy}")
    if degraded:
        print("  ⚠️ 로코모션은 모션 레퍼런스 없이 생성됐다 — 상업 준비 증거로 쓰지 말 것.")
    for w in v.warnings:
        print(f"  ⚠ {w}")
    for c in v.candidates:
        line = f"  cand a{c.attempt} {c.provider}:{c.model} decision={c.decision}"
        if c.provider_job_id:
            line += f" job={c.provider_job_id}"
        if c.error:
            line += f" error={c.error}"
        print(line)
        conf = (c.qa_result or {}).get("output_conformance") or {}
        probe = conf.get("probe") or {}
        if probe:
            print(
                f"      decode {probe.get('width')}x{probe.get('height')} "
                f"{probe.get('duration')}s audio={probe.get('has_audio')} "
                f"conformance={conf.get('status')}"
            )
        if c.qa_result:
            print(
                f"      qa {c.qa_result.get('qa_version')} identity={c.qa_result.get('identity_similarity')} "
                f"reasons={c.qa_result.get('reasons')}"
            )
        if c.raw_video_path:
            print(f"      raw={c.raw_video_path}")
        if c.selected:
            print("      ★ selected")
    print(
        "  provenance: motion_spec="
        f"{v.motion_spec_version} start_kf={v.start_keyframe_id} "
        f"target_kf={v.target_keyframe_id} canonical={v.canonical_version_id}"
    )
    return v


async def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 6.5 single-pet live smoke test")
    ap.add_argument("--user-id")
    ap.add_argument("--pet-id")
    ap.add_argument("--preflight", action="store_true", help="설정 점검만 (과금 없음)")
    ap.add_argument("--confirm", action="store_true", help="실 결제 호출 승인")
    ap.add_argument("--degraded-locomotion", action="store_true",
                    help="COME_CLOSER 를 저하 모드(레퍼런스 없음)로 추가 실행")
    args = ap.parse_args()

    creds_ok = preflight()
    if args.preflight or not args.confirm:
        if not args.preflight:
            print("\n  --confirm 이 없어 생성하지 않았다 (preflight 만 수행).")
        return 0

    if not creds_ok:
        print("\n  ✗ 자격 증명 없이 라이브 호출은 하지 않는다. 종료.")
        return 1
    if not args.user_id or not args.pet_id:
        print("  ✗ --user-id 와 --pet-id 가 필요하다.")
        return 1

    results = {}
    for motion in SMOKE_MOTIONS:
        results[motion] = await run_motion(args.user_id, args.pet_id, motion)
    if args.degraded_locomotion:
        for motion in DEGRADED_LOCOMOTION:
            results[f"{motion} (degraded)"] = await run_motion(
                args.user_id, args.pet_id, motion, degraded=True
            )

    print("\n── 요약 ─────────────────────────────────────────────────────")
    for k, v in results.items():
        print(f"  {k:<28} {'-' if v is None else v.status}")
    print(
        "\n  다음 단계: POST /api/v1/pet/motions/{pet}/{motion}/evaluations 로 사람 평가를"
        "\n  기록하고, GET /api/v1/pet/motions/calibration/report 로 자동 QA 와 비교한다."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
