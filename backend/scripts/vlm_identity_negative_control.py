"""
Negative control for the combined BREATHING identity+anatomy VLM call.

Frames of pet A's stored BREATHING video are judged against (1) pet A's own
reference keyframe — must answer same_pet=yes — and (2) pet B's reference
keyframe — must answer same_pet=no. This is the only hard video-level identity
check, so it must be shown to say "no" on a real mismatch.

    # plan only (no calls, no spend)
    python -m backend.scripts.vlm_identity_negative_control --video 417b93ad --other 3dc92b56
    # real client, capped
    python -m backend.scripts.vlm_identity_negative_control --video 417b93ad --other 3dc92b56 \
        --execute --max-calls 2

Read-only: SELECTs and storage downloads of stored artifacts. No generation, no
DB writes; the VLM cache is bypassed (off) so nothing is stored. Each call sends
the sampled frames plus one reference image to the configured VLM model.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: Rough per-call cost bounds used only for the printed estimate (see report).
EST_COST_PER_CALL_USD = (0.09, 0.16)


def plan_cases(video_ref: tuple[bytes, str], other_ref: tuple[bytes, str]) -> list[dict[str, Any]]:
    return [
        {"name": "positive_control_same_pet", "reference": video_ref, "expect": "yes"},
        {"name": "negative_control_other_pet", "reference": other_ref, "expect": "no"},
    ]


def run_cases(
    cases: list[dict[str, Any]],
    frames: list[tuple[bytes, str]],
    *,
    ask: Callable[..., Optional[dict[str, Any]]],
    max_calls: int,
) -> list[dict[str, Any]]:
    """Run each case through ``ask`` under a hard call cap. Pure of I/O."""

    if len(cases) > max_calls:
        raise SystemExit(f"{len(cases)} calls needed but --max-calls is {max_calls}; nothing was sent.")
    results = []
    for case in cases:
        answer = ask(frames, reference_image=case["reference"])
        got = (answer or {}).get("same_pet_all_frames")
        results.append({
            "name": case["name"],
            "expect": case["expect"],
            "same_pet_all_frames": got,
            "anatomy_plausible_all_frames": (answer or {}).get("anatomy_plausible_all_frames"),
            "returned_nothing": answer is None,
            "ok": got == case["expect"],
            "notes": str((answer or {}).get("notes") or "")[:300],
        })
    return results


def _load_env() -> None:
    from backend.scripts.replay_motion_qa_rules import _load_env_file

    _load_env_file(_ROOT / ".env.local")
    _load_env_file(_ROOT / "backend" / "env.local")


def _candidate(client: Any, prefix: str) -> dict[str, Any]:
    rows = (
        client.table(os.getenv("PET_MOTION_CANDIDATES_TABLE", "pet_motion_candidates"))
        .select("id,pet_id,motion_id,raw_bucket,raw_video_path,generation_metadata")
        .eq("motion_id", "BREATHING").execute().data
    )
    matches = [r for r in rows if str(r["id"]).startswith(prefix)]
    if len(matches) != 1:
        raise SystemExit(f"candidate prefix {prefix!r} matched {len(matches)} rows")
    return matches[0]


def _reference_path(candidate: dict[str, Any]) -> str:
    anchor = ((candidate.get("generation_metadata") or {}).get("video_anchor") or {}).get("start") or {}
    path = str(anchor.get("object_path") or "")
    if not path:
        raise SystemExit(f"candidate {str(candidate['id'])[:8]} has no stored start reference")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="candidate id prefix whose video frames are judged")
    ap.add_argument("--other", required=True, help="candidate id prefix of a DIFFERENT pet (reference)")
    ap.add_argument("--max-calls", type=int, default=2)
    ap.add_argument("--execute", action="store_true", help="actually call the VLM (spends money)")
    args = ap.parse_args()

    _load_env()
    from supabase import create_client

    client = create_client(
        os.environ.get("SUPABASE_URL") or os.environ["VITE_SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    video_c, other_c = _candidate(client, args.video), _candidate(client, args.other)
    if video_c["pet_id"] == other_c["pet_id"]:
        raise SystemExit("--video and --other belong to the same pet; pick two different pets.")
    low, high = EST_COST_PER_CALL_USD
    print(f"video: {str(video_c['id'])[:8]}  other-pet reference: {str(other_c['id'])[:8]}")
    print(f"planned calls: 2 (cap {args.max_calls}); estimated spend ${2 * low:.2f}-${2 * high:.2f}")
    if not args.execute:
        print("plan only — pass --execute to call the VLM.")
        return

    from backend.services import motion_video_qa, motion_video_service, vlm_escalation, vlm_identity

    def download(bucket: str, path: str) -> bytes:
        return client.storage.from_(bucket or "user-assets").download(path)

    bucket = video_c.get("raw_bucket") or "user-assets"
    frames = motion_video_service._frames_to_jpeg(
        motion_video_qa.sample_frames(download(bucket, video_c["raw_video_path"]))
    )
    cases = plan_cases(
        (download(bucket, _reference_path(video_c)), "image/png"),
        (download(other_c.get("raw_bucket") or "user-assets", _reference_path(other_c)), "image/png"),
    )

    def ask(frame_images, *, reference_image):
        return vlm_identity.qa_motion_video(
            frame_images,
            motion_description="calm breathing while holding the pose",
            motion_class="MICRO",
            sample_fractions=tuple(motion_video_qa.SAMPLE_FRACTIONS),
            reference_image=reference_image,
            cache_mode=vlm_identity.QA_CACHE_OFF,
            tasks=[vlm_escalation.IDENTITY_ANATOMY_VLM],
            unresolved_questions=["identity_anatomy"],
        )

    with vlm_identity.capture_call_failures() as failures:
        results = run_cases(cases, frames, ask=ask, max_calls=args.max_calls)
    for row in results:
        print(f"{'PASS' if row['ok'] else 'FAIL'} {row['name']}: same_pet={row['same_pet_all_frames']} "
              f"(expected {row['expect']}) anatomy={row['anatomy_plausible_all_frames']} "
              f"returned_nothing={row['returned_nothing']}")
        if row["notes"]:
            print(f"     notes: {row['notes']}")
    for failure in failures:
        print(f"     vlm failure: {failure}")
    print(f"call stats: {vlm_identity.call_stats()}")
    raise SystemExit(0 if all(row["ok"] for row in results) else 1)


if __name__ == "__main__":
    main()
