"""
DEV 전용 — 모션 테스트용 레퍼런스 팩 준비 (정본 1 + 키프레임 4역할 = 마스터 5장).

한 펫에 대해 **이미지 마스터만** 만든다:

  1. Canonical           (canonical_pet_service.build_canonical)
  2. NEUTRAL_IDLE  ┐
  3. STAND_READY   │     (action_keyframe_service.build_keyframe, 순차)
  4. LIE           │
  5. SLEEP         ┘

생성 로직은 기존 서비스 그대로다 — 이 스크립트는 순서와 보고만 담당한다:
  * 정본 먼저, 그다음 역할 4종 순차.
  * skip_if_unchanged=True — 유효한 기존 COMPLETE/REVIEW 최신 버전은 재사용
    (서비스의 멱등 게이트 그대로; 재사용 시 deduplicated 로 표시된다).
  * 첫 QA PASS 중단(candidate policy)도 서비스 계층의 것을 그대로 쓴다.
  * DB/스토리지 계보는 프로덕션 경로와 동일하게 남는다 — 이후의 모션 생성 런이
    KEYFRAMES 스테이지에서 이 COMPLETE 키프레임들을 역할로 그대로 재사용한다.

⚠️ 모션은 **절대** 건드리지 않는다: motion_spec 해석·영상 잡·영상 프로바이더
호출이 0건이다. 이 모듈은 모션/영상 서비스를 임포트조차 하지 않는다
(테스트가 소스 스캔 + 런타임 스파이로 강제한다). 키프레임까지 만들고 완전히
멈춘다 — 데모/프로덕션의 수요 기반 생성 정책은 바뀌지 않는다.

⚠️ 라이브 실행은 **실 결제 호출**(이미지 생성 + VLM QA)을 만든다. phase6_live_smoke
와 같은 안전장치: --confirm 없으면 preflight 만 한다.

사용:
  # 점검만 (호출 없음, 과금 없음)
  python -m backend.scripts.dev_reference_pack \
      --user-id you@example.com --pet-id pet_<cid>

  # 레퍼런스 사진 등록 + 마스터 5장 생성/재사용
  python -m backend.scripts.dev_reference_pack \
      --user-id you@example.com --pet-id pet_<cid> \
      --image ~/Desktop/dog1.jpg --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import mimetypes
import os
import sys
import time
from typing import Any, Callable, Optional, Sequence

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 준비할 키프레임 역할 — Phase 4 의 기계적 포즈 역할 4종, 정본 다음 순서대로.
REFERENCE_PACK_ROLES: tuple[str, ...] = ("NEUTRAL_IDLE", "STAND_READY", "LIE", "SLEEP")


def _load_env_cascade() -> None:
    """backend/main.py 의 .env 캐스케이드 — 반드시 런타임에만 (phase6_live_smoke 교훈)."""
    import backend.main  # noqa: F401 — import 부수효과로 dotenv 로드


def _candidate_summary(c: Any) -> dict[str, Any]:
    qa = getattr(c, "qa_result", None) or {}
    return {
        "id": getattr(c, "id", None),
        "provider": getattr(c, "provider", None),
        "decision": getattr(c, "decision", None) or qa.get("decision"),
        "raw_object_path": getattr(c, "raw_object_path", None),
        "cutout_object_path": getattr(c, "cutout_object_path", None),
    }


def _version_summary(v: Any) -> dict[str, Any]:
    selected = next(
        (c for c in (getattr(v, "candidates", None) or []) if getattr(c, "selected", False)),
        None,
    )
    return {
        "id": getattr(v, "id", None),
        "version": getattr(v, "version", None),
        "status": getattr(v, "status", None),
        "reused": bool(getattr(v, "deduplicated", False)),
        "selected_candidate_id": getattr(v, "selected_candidate_id", None),
        "qa_decision": (getattr(v, "qa_summary", None) or {}).get("decision"),
        "selected": _candidate_summary(selected) if selected else None,
    }


async def prepare_reference_pack(
    *,
    user_id: str,
    pet_id: str,
    images: Sequence[tuple[str, bytes, str]] = (),
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    providers: Optional[Sequence[Any]] = None,
    cutout_fn: Optional[Callable[[bytes], Optional[bytes]]] = None,
    poll_sec: float = 5.0,
    max_wait_sec: float = 900.0,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """
    정본 → 키프레임 4역할 순차 준비. 결과는 보고 dict — 모든 생성/재사용/QA/
    스토리지 정보는 서비스가 남긴 정상 계보에서 읽는다.

    모션/영상 관련 import 금지 — 이 함수의 계약이다 (테스트가 스캔한다).
    """
    from backend.services import (
        action_keyframe_service,
        canonical_pet_service,
        durable_provider_jobs,
        pet_reference_service,
    )

    report: dict[str, Any] = {
        "pet_id": pet_id,
        "user_id": user_id,
        "references": [],
        "canonical": None,
        "keyframes": {},
        "motion_generation": "untouched",  # 이 스크립트의 계약 — 영상 잡 0건
    }

    # ── 0) 레퍼런스 사진 등록 (선택, 바이트 멱등) ─────────────────────────
    # 원본 + 짝지은 누끼(cutout_reference) 를 함께 남긴다 — Phase 1 업로드
    # 훅(routers/assets.py)과 같은 계보다. 누끼가 없으면 신원 시그니처가
    # 계산되지 않아 정본 QA 가 전부 REVIEW 로 남는다 (라이브에서 실측).
    from backend.services import supabase_assets
    from backend.services.canonical_pet_service import _default_cutout_fn

    reference_cutout = cutout_fn or _default_cutout_fn
    cid = pet_id[4:] if pet_id.startswith("pet_") else pet_id
    for path, data, mime in images:
        ref = await pet_reference_service.record_original(
            user_id=user_id,
            content_id=cid,
            data=data,
            mime_type=mime,
            original_filename=os.path.basename(path),
        )
        entry: dict[str, Any] = {"id": ref.id, "object_path": ref.object_path}
        cut_bytes = reference_cutout(data)
        if cut_bytes:
            cut_path = f"{user_id}/{cid}/references/cutout_{ref.content_hash[:16]}.png"
            await supabase_assets.upload_asset_to_storage(cut_path, cut_bytes, "image/png")
            derived = await pet_reference_service.record_derived(
                user_id=user_id,
                content_id=cid,
                object_path=cut_path,
                derived_kind="cutout_reference",
                parent_reference_id=ref.id,
                mime_type="image/png",
            )
            entry["cutout_object_path"] = derived.object_path
        else:
            log(f"[ref] ⚠️ 누끼 실패 — 신원 QA 가 REVIEW 로 남을 수 있다: {path}")
        report["references"].append(entry)
        log(f"[ref] {os.path.basename(path)} → {ref.object_path}")

    if images:
        # 신원 프로필을 강제 재빌드한다 — 프로필 dedup 은 **원본 집합**만 보므로,
        # 나중에 등록된 누끼를 프로필이 영영 못 본다(라이브 실측: 프로필 v1 이
        # no_segmentation_available 로 남아 시그니처가 계산되지 않았고, 정본 QA 가
        # 전부 REVIEW 로 남았다). 새 프로필 버전이 생기면 레퍼런스 세트/정본의
        # 기존 dedup 게이트가 자연히 재빌드로 이어진다. (프로덕션 업로드 훅은
        # 원본+누끼를 함께 남기므로 이 순서 문제가 없다 — 스크립트 전용 보정.)
        from backend.services import pet_identity_service

        profile = await pet_identity_service.build_identity_profile(
            user_id=user_id, pet_id=pet_id, fetch_bytes=fetch_bytes,
            skip_if_unchanged=False,
        )
        log(f"[identity] profile v{profile.version} rebuilt (cutout-aware)")

    # ── 내구 프로바이더 폴링 — 잡 pending 은 실패가 아니다 ────────────────
    async def _await_durable(build: Callable[[], Any], label: str) -> Any:
        deadline = time.monotonic() + max_wait_sec
        while True:
            try:
                return await build()
            except durable_provider_jobs.ProviderWorkPending as pending:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"{label}: provider job 이 {max_wait_sec:.0f}s 안에 끝나지 않았다 "
                        f"({pending})"
                    )
                log(f"[{label}] provider job pending — {poll_sec:.0f}s 후 재시도")
                await asyncio.sleep(poll_sec)

    # ── 1) Canonical ──────────────────────────────────────────────────────
    # 재사용은 **COMPLETE 만** 유효하다 — 팩의 목적은 승인 마스터 5장이다.
    # 서비스의 dedup 게이트는 REVIEW 도 재사용 대상으로 보므로(같은 입력의
    # 재시도 낭비 방지 목적), 최신이 REVIEW/FAILED 면 여기서 새 버전 빌드를
    # 명시한다 (라이브 실측: 누끼 추가 후에도 REVIEW v1 이 재사용됐다).
    existing_canonical = await canonical_pet_service.get_canonical(
        user_id=user_id, pet_id=pet_id
    )
    canonical_skip = (
        existing_canonical is None
        or existing_canonical.status == canonical_pet_service.STATUS_COMPLETE
    )
    canonical = await _await_durable(
        lambda: canonical_pet_service.build_canonical(
            user_id=user_id,
            pet_id=pet_id,
            fetch_bytes=fetch_bytes,
            providers=providers,
            cutout_fn=cutout_fn,
            skip_if_unchanged=canonical_skip,
        ),
        "canonical",
    )
    report["canonical"] = _version_summary(canonical)
    log(
        f"[canonical] v{canonical.version} {canonical.status}"
        f"{' (reused)' if canonical.deduplicated else ''}"
        f" selected={canonical.selected_candidate_id}"
    )

    # ── 2) 키프레임 4역할 — 순차 (정본 COMPLETE 요구는 서비스가 검사한다) ──
    for role in REFERENCE_PACK_ROLES:
        existing_kf = await action_keyframe_service.get_keyframe(
            user_id=user_id, pet_id=pet_id, keyframe_role=role
        )
        kf_skip = (
            existing_kf is None
            or existing_kf.status == action_keyframe_service.STATUS_COMPLETE
        )
        keyframe = await _await_durable(
            lambda role=role, skip=kf_skip: action_keyframe_service.build_keyframe(
                user_id=user_id,
                pet_id=pet_id,
                keyframe_role=role,
                fetch_bytes=fetch_bytes,
                providers=providers,
                cutout_fn=cutout_fn,
                skip_if_unchanged=skip,
            ),
            f"keyframe:{role}",
        )
        report["keyframes"][role] = _version_summary(keyframe)
        log(
            f"[keyframe:{role}] v{keyframe.version} {keyframe.status}"
            f"{' (reused)' if keyframe.deduplicated else ''}"
            f" selected={keyframe.selected_candidate_id}"
        )

    # 여기서 완전히 멈춘다 — 모션 스펙/영상 잡/영상 프로바이더 호출 0건.
    return report


def _print_report(report: dict[str, Any]) -> None:
    print("\n══ 레퍼런스 팩 보고 ══════════════════════════════════════════")
    print(f"pet: {report['pet_id']}  (user: {report['user_id']})")
    for r in report["references"]:
        print(f"  reference: {r['object_path']}")

    def _row(label: str, s: Optional[dict[str, Any]]) -> None:
        if not s:
            print(f"  {label}: (없음)")
            return
        sel = s.get("selected") or {}
        print(
            f"  {label}: v{s['version']} {s['status']}"
            f"{' [reused]' if s.get('reused') else ''}"
        )
        print(f"      selected_candidate: {s.get('selected_candidate_id')}")
        print(f"      qa_decision:        {sel.get('decision') or s.get('qa_decision')}")
        print(f"      raw:                {sel.get('raw_object_path')}")
        if sel.get("cutout_object_path"):
            print(f"      cutout:             {sel.get('cutout_object_path')}")

    _row("CANONICAL", report.get("canonical"))
    for role in REFERENCE_PACK_ROLES:
        _row(role, (report.get("keyframes") or {}).get(role))
    print(f"  motion generation:  {report.get('motion_generation')} (영상 잡 0건)")
    print("═════════════════════════════════════════════════════════════")


def _read_images(paths: Sequence[str]) -> list[tuple[str, bytes, str]]:
    out: list[tuple[str, bytes, str]] = []
    for p in paths:
        full = os.path.expanduser(p)
        with open(full, "rb") as f:
            data = f.read()
        mime = mimetypes.guess_type(full)[0] or "image/jpeg"
        out.append((full, data, mime))
    return out


async def _preflight(user_id: str, pet_id: str) -> None:
    from backend.services import action_keyframe_service, canonical_image_providers, canonical_pet_service

    providers = [p for p in canonical_image_providers.resolve_providers() if p.available()]
    print(f"image providers: {[f'{p.name}:{p.model_name()}' for p in providers] or '없음 (라이브 불가)'}")
    print(f"VLM QA: {'enabled' if os.getenv('PET_VLM_IDENTITY_ENABLED') == '1' else 'DISABLED — 전부 REVIEW 로 남는다'}")
    canonical = await canonical_pet_service.get_canonical(user_id=user_id, pet_id=pet_id)
    print(f"canonical: {f'v{canonical.version} {canonical.status}' if canonical else '없음'}")
    for role in REFERENCE_PACK_ROLES:
        k = await action_keyframe_service.get_keyframe(
            user_id=user_id, pet_id=pet_id, keyframe_role=role
        )
        print(f"keyframe {role}: {f'v{k.version} {k.status}' if k else '없음'}")
    print("\n--confirm 없이는 아무것도 생성하지 않았다. (과금 0)")


def main() -> None:
    ap = argparse.ArgumentParser(description="DEV 레퍼런스 팩 — 정본 + 키프레임 4역할")
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--pet-id", required=True, help="pet_<content_id>")
    ap.add_argument("--image", action="append", default=[], help="등록할 레퍼런스 사진 (반복 가능)")
    ap.add_argument("--confirm", action="store_true", help="실 결제 호출 승인 (없으면 preflight)")
    ap.add_argument("--poll-sec", type=float, default=5.0)
    ap.add_argument("--max-wait-sec", type=float, default=900.0)
    args = ap.parse_args()

    _load_env_cascade()

    if not args.confirm:
        asyncio.run(_preflight(args.user_id, args.pet_id))
        return

    report = asyncio.run(
        prepare_reference_pack(
            user_id=args.user_id,
            pet_id=args.pet_id,
            images=_read_images(args.image),
            poll_sec=args.poll_sec,
            max_wait_sec=args.max_wait_sec,
        )
    )
    _print_report(report)


if __name__ == "__main__":
    main()
