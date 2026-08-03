"""
고야(Goya) 사진으로 LivePortrait 액션 파이프라인을 로컬에서 바로 시험하는 스크립트.

★ 왜 큐(action_video_jobs)를 거치지 않고 파이프라인을 직접 호출하는가
처음 한 번은 "LivePortrait 리포/가중치가 제대로 설치됐는지, 드라이빙 영상이 실제로
있는지, 출력이 800x480 블랙 배경으로 잘 나오는지"를 빠르게 눈으로 확인하는 게
목적이라, Supabase 큐 테이블/워커 프로세스까지 다 띄우지 않고 이 스크립트 하나로
`run_live_portrait_batch()`를 동기적으로 직접 호출한다(=워커가 내부적으로 하는 일과
100% 동일한 함수 호출이라, 여기서 성공하면 큐 경로도 그대로 성공한다).
파이프라인이 확인되면 실제 운영은 `backend/routers/live_portrait.py`
(POST /api/live-portrait/generate-action-set)로 잡을 큐에 넣고
`python -m backend.workers.live_portrait_worker`가 처리하는 정식 경로를 쓰면 된다.

실행(리포 루트에서, RTX 4090 등 GPU 머신 + LivePortrait 설치가 끝난 뒤):

    python -m backend.scripts.test_live_portrait_goya

    # 이미지/폴더를 바꾸고 싶으면:
    python -m backend.scripts.test_live_portrait_goya --image "누끼딴고야.png" \
        --driving-videos-dir backend/assets/driving_videos --output-dir outputs/goya_test

이 스크립트는 GPU/LivePortrait 설치가 없는 이 개발 환경(Windows, GPU 없음)에서는
실행할 수 없다 — 사용자의 로컬 RTX 4090 머신에서 실행하기 위한 것이다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE = _REPO_ROOT / "누끼딴고야.png"
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "outputs" / "goya_live_portrait_test"


def _print_stage(stage: str, action: str) -> None:
    labels = {
        "live_portrait_inference": "1/4 LivePortrait 추론",
        "sam2_black_background": "2/4 SAM2 배경 강제 블랙",
        "ffmpeg_resize_encode": "3/4 ffmpeg 800x480 리사이즈/인코딩",
        "supabase_upload": "4/4 Supabase 업로드",
    }
    print(f"  [{action}] {labels.get(stage, stage)} ...")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", type=str, default=str(DEFAULT_IMAGE),
        help=f"소스 강아지 사진(로컬 경로). 기본: {DEFAULT_IMAGE}",
    )
    parser.add_argument(
        "--driving-videos-dir", type=str, default=None,
        help="드라이빙 영상 폴더. 기본: backend/assets/driving_videos "
             "(LIVE_PORTRAIT_DRIVING_VIDEOS_DIR 환경변수로도 지정 가능)",
    )
    parser.add_argument(
        "--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR),
        help=f"결과 mp4를 로컬에 저장할 폴더. 기본: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--content-id", type=str, default="goya-test",
        help="Supabase 업로드 시 경로 구분용 content_id (기본: goya-test)",
    )
    parser.add_argument(
        "--upload", action="store_true",
        help="지정 시 Supabase Storage에도 업로드(기본은 로컬 저장만, 네트워크/Supabase 설정 불필요)",
    )
    args = parser.parse_args()

    image_path = Path(args.image).expanduser()
    if not image_path.is_file():
        print(f"[에러] 소스 이미지를 찾을 수 없습니다: {image_path}")
        print("       --image로 실제 파일 경로를 지정하세요.")
        return 1

    # 지연 import — argparse --help가 GPU/LivePortrait 의존성 없이도 동작하도록.
    from ..services.live_portrait_batch import list_driving_videos, run_live_portrait_batch

    driving_dir = Path(args.driving_videos_dir).expanduser() if args.driving_videos_dir else None
    videos = list_driving_videos(driving_dir)

    print(f"소스 이미지: {image_path}")
    print(f"드라이빙 영상 폴더: {driving_dir or '(기본값) backend/assets/driving_videos'}")
    print(f"발견된 드라이빙 영상: {len(videos)}개")

    if not videos:
        print()
        print("드라이빙 영상이 0개라 생성할 액션이 없습니다.")
        print("다음 위치에 강아지 동작 레퍼런스 mp4 파일을 넣어주세요:")
        print(f"  {(driving_dir or (_REPO_ROOT / 'backend' / 'assets' / 'driving_videos'))}")
        print("파일명 = 액션 이름(예: sit.mp4, run.mp4, sniff.mp4 ...). 자세한 형식은")
        print("  backend/assets/driving_videos/README.md 참고.")
        print("파일을 넣은 뒤 이 스크립트를 다시 실행하면 있는 만큼 자동으로 처리됩니다.")
        return 0

    print(f"출력 폴더: {args.output_dir}")
    print(f"Supabase 업로드: {'예' if args.upload else '아니오(로컬 저장만)'}")
    print()
    print(f"총 {len(videos)}개 액션 처리 시작...")

    def _progress_cb(idx, total, result):  # noqa: ANN001
        status = "성공" if result.success else f"실패({result.error})"
        print(f"  → [{idx}/{total}] '{result.action}' 완료: {status}")
        if result.output_path:
            print(f"     출력: {result.output_path}")

    results = run_live_portrait_batch(
        str(image_path),
        driving_videos_dir=driving_dir,
        user_id="goya-local-test",
        content_id=args.content_id,
        progress_cb=_progress_cb,
        stage_cb=_print_stage,
        upload_to_supabase=args.upload,
        local_output_dir=Path(args.output_dir),
    )

    n_ok = sum(1 for r in results if r.success)
    print()
    print(f"완료: {n_ok}/{len(results)} 성공.")
    if n_ok < len(results):
        print("실패한 항목:")
        for r in results:
            if not r.success:
                print(f"  - {r.action}: {r.error}")

    from ..services.live_portrait_batch import write_manifest_json

    manifest_path = Path(args.output_dir) / "manifest.json"
    write_manifest_json(results, manifest_path)
    print(f"매니페스트: {manifest_path}")

    return 0 if n_ok == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
