"""
LivePortrait 액션 20종 배치 — 로컬 RTX 4090 워커 (4단계, 1차 실행 경로).

사용자의 로컬 GPU 머신에서 계속 떠 있는 일반 Python 프로세스로 실행한다(Redis/Celery
등 추가 인프라 없음 — action_video_jobs 테이블을 그냥 polling). FastAPI 백엔드(Render 등)는
잡을 등록만 하고, 무거운 LivePortrait+SAM2 추론은 전부 이 워커 프로세스 안에서 돈다.

실행:
    python -m backend.workers.live_portrait_worker

  (리포 루트에서 실행. backend/env.local 또는 루트 .env에 SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY, LIVE_PORTRAIT_REPO_DIR, LIVE_PORTRAIT_PYTHON 등을
  설정해 두어야 한다 — docs/LivePortrait_설치_가이드.md 참고.)

내결함성:
  - 잡 처리 중 예외가 나면 조용히 죽지 않고 반드시 status='failed' + error 메시지를
    DB에 기록한 뒤 다음 잡으로 넘어간다(try/except로 메인 루프 자체는 절대 안 죽음).
  - 워커가 죽었다가 재시작돼도 DB 상태만 보고 이어서 동작한다(claim_next_job의
    stale 재클레임 로직) — 워커 프로세스 자체는 상태를 들고 있지 않음(stateless).
  - Ctrl+C(SIGINT)로 안전 종료 가능 — 현재 처리 중인 잡은 끝까지 마치고 종료.

환경변수:
  LIVE_PORTRAIT_WORKER_ID           기본 "worker-<pid>" (claimed_by에 기록될 이름)
  LIVE_PORTRAIT_POLL_INTERVAL_SEC   기본 "10" (큐가 비어 있을 때 재시도 간격)
  LIVE_PORTRAIT_STALE_MINUTES       기본 "30" (죽은 워커의 running 잡 재클레임 기준)
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

logging.basicConfig(
    level=os.getenv("LIVE_PORTRAIT_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("live_portrait_worker")

_SHOULD_STOP = False


def _handle_shutdown_signal(signum, frame):  # noqa: ARG001
    global _SHOULD_STOP
    logger.info("종료 신호 수신 — 현재 잡을 마치고 종료합니다.")
    _SHOULD_STOP = True


def _process_one_job(job: dict) -> None:
    from ..services import action_video_jobs
    from ..services.live_portrait_batch import run_live_portrait_batch, ActionVideoResult

    job_id = job["id"]
    dog_image_url = job["dog_image_url"]
    user_id = job.get("user_id") or "anonymous"
    content_id = job.get("content_id") or job_id

    logger.info("잡 처리 시작: id=%s user=%s dog_image_url=%s", job_id, user_id, dog_image_url)

    def _progress_cb(idx: int, total: int, result: "ActionVideoResult") -> None:
        try:
            action_video_jobs.update_progress(
                job_id, total=total, completed=idx, current_action=result.action
            )
        except Exception:
            logger.exception("진행률 업데이트 실패(잡 처리는 계속): job_id=%s", job_id)

    try:
        results = run_live_portrait_batch(
            dog_image_url,
            user_id=user_id,
            content_id=content_id,
            progress_cb=_progress_cb,
            upload_to_supabase=True,
        )
        results_dicts = [
            {
                "action": r.action,
                "driving_video": r.driving_video,
                "output_url": r.output_url,
                "duration_sec": r.duration_sec,
                "resolution": r.resolution,
                "success": r.success,
                "error": r.error,
            }
            for r in results
        ]
        action_video_jobs.mark_done(job_id, results_dicts)
        n_ok = sum(1 for r in results if r.success)
        logger.info("잡 완료: id=%s (%d/%d 성공)", job_id, n_ok, len(results))
    except Exception as e:
        logger.exception("잡 처리 중 예외 발생 — status='failed'로 기록: job_id=%s", job_id)
        action_video_jobs.mark_failed(job_id, f"{type(e).__name__}: {e}")


def main() -> None:
    # 백엔드와 동일한 .env 로딩 규칙을 재사용(루트 .env / backend/env.local 등).
    from .. import main as _backend_main  # noqa: F401  (import만으로 dotenv 로딩 트리거)
    from ..services import action_video_jobs

    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    worker_id = action_video_jobs.worker_id_from_env()
    poll_interval = float(os.getenv("LIVE_PORTRAIT_POLL_INTERVAL_SEC", "10"))
    stale_minutes = int(os.getenv("LIVE_PORTRAIT_STALE_MINUTES", "30"))

    logger.info("LivePortrait 워커 시작: worker_id=%s poll_interval=%ss", worker_id, poll_interval)

    while not _SHOULD_STOP:
        try:
            job = action_video_jobs.claim_next_job(worker_id, stale_after_minutes=stale_minutes)
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
