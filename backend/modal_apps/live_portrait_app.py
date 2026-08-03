"""
[선택/보조 경로] Modal GPU에서 LivePortrait 액션 배치를 돌리는 앱.

★ 1차 경로가 아님을 명확히
이 프로젝트의 1차 실행 경로는 사용자의 로컬 RTX 4090 머신에서 도는
`backend/workers/live_portrait_worker.py`다(Supabase 테이블 polling). 이 파일은
"나중에 로컬 GPU 대신/추가로 Modal 클라우드 GPU를 쓰고 싶을 때"를 위한 선택적
보조 경로로만 남겨둔다. 기존 backend/services/modal_cutout_client.py +
modal_cutout_app.py(누끼 처리)와 동일한 구조/네이밍 패턴을 그대로 따랐다.

★ 검증 안 됨(중요)
이 개발 환경에는 GPU가 없어 `modal deploy`/실제 추론을 이 세션에서 실행해보지
못했다. LivePortrait는 pip 패키지가 아니라 (1) 깃 클론 + (2) 커스텀 CUDA 연산
(X-Pose의 MultiScaleDeformableAttention) 빌드가 필요해서, Modal 이미지 빌드 단계가
로컬 설치 가이드(docs/LivePortrait_설치_가이드.md)와 동일하게 꽤 무겁고 실패
가능성이 있다 — 실제로 쓰려면 `modal deploy` 후 로그를 보며 이미지 빌드 단계를
직접 디버깅해야 할 가능성이 높다.

배포:
    modal deploy backend/modal_apps/live_portrait_app.py

호출(백엔드/워커에서, 선택):
    fn = modal.Function.from_name("eternal-beam-live-portrait", "run_live_portrait_batch_modal")
    fn.spawn(dog_image_url=..., ...)  # 비동기(FunctionCall) — 20개 GPU 추론이라 spawn 권장
"""

from __future__ import annotations

import os
from pathlib import Path

import modal

_ROOT = Path(__file__).resolve().parents[2]

app = modal.App("eternal-beam-live-portrait")

# LivePortrait 리포를 이미지 빌드 시점에 클론 + Animals 모드 의존성(X-Pose 커스텀 op)까지
# 빌드해 넣는다. 가중치(pretrained_weights/)는 이미지에 굽지 않고 Modal Volume에 캐시
# (별도 다운로드 스텝 필요 — 아래 LIVE_PORTRAIT_WEIGHTS_VOLUME 설명 참고).
image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "ffmpeg", "build-essential")
    .run_commands(
        "git clone --depth 1 https://github.com/KwaiVGI/LivePortrait.git /root/LivePortrait",
    )
    .pip_install(
        "torch>=2.1.0",
        "torchvision",
        gpu="T4",  # torch를 GPU 빌드로 설치 (CUDA 휠)
    )
    .run_commands(
        "cd /root/LivePortrait && pip install -r requirements_base.txt",
        # X-Pose(Animals 모드 keypoint 검출기) 커스텀 op 빌드 — Linux+NVIDIA GPU 전용.
        "cd /root/LivePortrait/src/utils/dependencies/XPose/models/UniPose/ops "
        "&& python setup.py build install",
    )
    .pip_install("opencv-python-headless>=4.8.0", "numpy>=1.24.0", "Pillow>=10.0.0")
    .add_local_dir(str(_ROOT / "backend"), remote_path="/root/backend")
)

# 가중치(수 GB)는 이미지에 굽지 않고 Modal Volume에 1회 다운로드해 재사용 — 매 배포마다
# 이미지 재빌드/재다운로드하지 않도록. 최초 1회는 별도 스크립트/셀로
# huggingface_hub.snapshot_download(...) 등을 이 Volume에 채워둬야 한다(가이드 문서 참고).
weights_volume = modal.Volume.from_name("live-portrait-weights", create_if_missing=True)


@app.function(
    image=image,
    gpu="A10G",
    timeout=3600,
    memory=16384,
    volumes={"/root/LivePortrait/pretrained_weights": weights_volume},
)
def run_live_portrait_batch_modal(
    dog_image_url: str,
    user_id: str = "anonymous",
    content_id: str = "batch",
) -> list[dict]:
    """live_portrait_batch.run_live_portrait_batch()를 Modal GPU 컨테이너 안에서 그대로 호출.

    driving_videos_dir는 add_local_dir로 함께 올라간 backend/assets/driving_videos를
    그대로 쓴다 — 로컬 워커와 동일한 배치 로직을 재사용(코드 중복 없음)."""
    import sys

    sys.path.insert(0, "/root/backend")
    os.environ.setdefault("LIVE_PORTRAIT_REPO_DIR", "/root/LivePortrait")
    os.environ.setdefault("LIVE_PORTRAIT_PYTHON", "python")

    from dataclasses import asdict

    from services.live_portrait_batch import run_live_portrait_batch

    results = run_live_portrait_batch(
        dog_image_url,
        user_id=user_id,
        content_id=content_id,
        upload_to_supabase=True,
    )
    return [asdict(r) for r in results]
