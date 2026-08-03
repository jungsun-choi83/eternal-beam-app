"""
SAM2+포즈추정+Spine2D 자동 리깅 — 로컬 RTX 4090 워커.

live_portrait_worker.py와 정확히 같은 패턴(Supabase 테이블 polling, 별도
인프라 없음, 잡 처리 예외는 항상 status='failed'로 기록하고 계속 polling).

실행:
    python -m backend.workers.auto_rigging_worker

환경변수:
  AUTO_RIGGING_WORKER_ID           기본 "worker-<pid>"
  AUTO_RIGGING_POLL_INTERVAL_SEC   기본 "10"
  AUTO_RIGGING_STALE_MINUTES       기본 "30"
  AUTO_RIGGING_OUTPUT_DIR          로컬에도 결과를 남길 폴더(기본: outputs/auto_rigging_jobs)
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=os.getenv("AUTO_RIGGING_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("auto_rigging_worker")

_SHOULD_STOP = False


def _handle_shutdown_signal(signum, frame):  # noqa: ARG001
    global _SHOULD_STOP
    logger.info("종료 신호 수신 — 현재 잡을 마치고 종료합니다.")
    _SHOULD_STOP = True


def _process_one_job(job: dict) -> None:
    from ..services import auto_rigging_jobs
    from ..services.auto_rigging_service import run_auto_rigging_pipeline

    job_id = job["id"]
    pet_image_url = job["pet_image_url"]
    user_id = job.get("user_id") or "anonymous"
    content_id = job.get("content_id") or job_id
    requested_actions = job.get("requested_actions") or []
    pose_backend = job.get("pose_backend") or "heuristic"

    logger.info(
        "잡 처리 시작: id=%s user=%s pet_image_url=%s backend=%s",
        job_id, user_id, pet_image_url, pose_backend,
    )

    def _progress_cb(stage: str, detail: str) -> None:
        try:
            auto_rigging_jobs.update_progress(job_id, stage=stage, detail=detail)
        except Exception:
            logger.exception("진행률 업데이트 실패(잡 처리는 계속): job_id=%s", job_id)

    output_dir = Path(os.getenv("AUTO_RIGGING_OUTPUT_DIR", "outputs/auto_rigging_jobs"))

    try:
        result = run_auto_rigging_pipeline(
            pet_image_url,
            actions=requested_actions or None,
            pose_backend=pose_backend,
            user_id=user_id,
            content_id=content_id,
            upload_to_supabase=True,
            local_output_dir=output_dir,
            progress_cb=_progress_cb,
        )
        auto_rigging_jobs.mark_done(job_id, result)
        logger.info("잡 완료: id=%s pose_backend_used=%s", job_id, result.get("pose_backend_used"))
    except Exception as e:
        logger.exception("잡 처리 중 예외 발생 — status='failed'로 기록: job_id=%s", job_id)
        auto_rigging_jobs.mark_failed(job_id, f"{type(e).__name__}: {e}")


def main() -> None:
    from .. import main as _backend_main  # noqa: F401  (import만으로 dotenv 로딩 트리거)
    from ..services import auto_rigging_jobs

    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    worker_id = auto_rigging_jobs.worker_id_from_env()
    poll_interval = float(os.getenv("AUTO_RIGGING_POLL_INTERVAL_SEC", "10"))
    stale_minutes = int(os.getenv("AUTO_RIGGING_STALE_MINUTES", "30"))

    logger.info("자동 리깅 워커 시작: worker_id=%s poll_interval=%ss", worker_id, poll_interval)

    while not _SHOULD_STOP:
        try:
            job = auto_rigging_jobs.claim_next_job(worker_id, stale_after_minutes=stale_minutes)
        except Exception:
            logger.exception("잡 폴링/클레임 실패 — %s초 후 재시도", poll_interval)
            time.sleep(poll_interval)
            continue

        if not job:
            time.sleep(poll_interval)
            continue

        _process_one_job(job)

    logger.info("워커 종료.")


if __name__ == "__main__":
    sys.exit(main() or 0)
