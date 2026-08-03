"""
배경 애니메이션("내 사진으로 나만의 배경 만들기") 로컬 GPU 워커 — live_portrait_worker.py
와 동일한 폴링 루프 패턴이지만 완전히 별도의 프로세스/테이블을 쓴다(두 워커를 같은
머신에서 동시에 상시 실행해도 서로 간섭하지 않음 — 각자 다른 테이블만 polling).

실행:
    python -m backend.workers.background_video_worker

  (리포 루트에서 실행. backend/env.local 또는 루트 .env에 SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY, LUMA_API_KEY 등을 설정해 두어야 한다.)

내결함성: live_portrait_worker.py와 동일한 정책 —
  - 잡 처리 중 예외가 나면 status='failed' + error를 DB에 기록하고 다음 잡으로.
  - claim_next_job()의 stale 재클레임으로 죽은 워커의 잡도 복구.
  - SIGINT/SIGTERM으로 안전 종료(현재 잡은 마치고 종료).

환경변수:
  BACKGROUND_VIDEO_WORKER_ID           기본 "bgworker-<pid>"
  BACKGROUND_VIDEO_POLL_INTERVAL_SEC   기본 "10"
  BACKGROUND_VIDEO_STALE_MINUTES       기본 "30"
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

logging.basicConfig(
    level=os.getenv("BACKGROUND_VIDEO_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("background_video_worker")

_SHOULD_STOP = False


def _handle_shutdown_signal(signum, frame):  # noqa: ARG001
    global _SHOULD_STOP
    logger.info("종료 신호 수신 — 현재 잡을 마치고 종료합니다.")
    _SHOULD_STOP = True


def _process_one_job(job: dict) -> None:
    from ..services import background_video_jobs
    from ..services.background_video_pipeline import run_background_video_pipeline

    job_id = job["id"]
    source_image_url = job["source_image_url"]
    user_id = job.get("user_id") or "anonymous"
    content_id = job.get("content_id") or job_id
    target_fps = job.get("target_fps")
    target_duration_sec = job.get("target_duration_sec")

    logger.info(
        "잡 처리 시작: id=%s user=%s source_image_url=%s", job_id, user_id, source_image_url
    )

    def _stage_cb(stage: str, detail: str | None) -> None:
        try:
            background_video_jobs.update_progress(job_id, stage=stage, detail=detail)
        except Exception:
            logger.exception("진행률 업데이트 실패(잡 처리는 계속): job_id=%s", job_id)

    try:
        result = run_background_video_pipeline(
            source_image_url,
            user_id=user_id,
            content_id=content_id,
            target_fps=target_fps,
            target_duration_sec=target_duration_sec,
            upload_to_supabase=True,
            stage_cb=_stage_cb,
        )
        background_video_jobs.mark_done(
            job_id, result_video_url=result.result_video_url, result_meta=result.result_meta
        )
        logger.info("잡 완료: id=%s url=%s", job_id, result.result_video_url)
    except Exception as e:
        logger.exception("잡 처리 중 예외 발생 — status='failed'로 기록: job_id=%s", job_id)
        background_video_jobs.mark_failed(job_id, f"{type(e).__name__}: {e}")


def main() -> None:
    # 백엔드와 동일한 .env 로딩 규칙을 재사용(루트 .env / backend/env.local 등).
    from .. import main as _backend_main  # noqa: F401  (import만으로 dotenv 로딩 트리거)
    from ..services import background_video_jobs

    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    worker_id = background_video_jobs.worker_id_from_env()
    poll_interval = float(os.getenv("BACKGROUND_VIDEO_POLL_INTERVAL_SEC", "10"))
    stale_minutes = int(os.getenv("BACKGROUND_VIDEO_STALE_MINUTES", "30"))

    logger.info(
        "배경 애니메이션 워커 시작: worker_id=%s poll_interval=%ss", worker_id, poll_interval
    )

    while not _SHOULD_STOP:
        try:
            job = background_video_jobs.claim_next_job(worker_id, stale_after_minutes=stale_minutes)
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
