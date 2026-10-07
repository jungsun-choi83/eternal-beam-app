"""
전시 준비 실행 워커 — exhibition_prep_runs 를 폴링해 패키지를 만든다.

auto_rigging_worker 와 같은 패턴(Supabase 테이블 polling, 한 번에 한 건).
pet_generation_worker 와는 프로세스·테이블·큐 모두 별개다.

실행:
    python -m backend.workers.exhibition_prep_worker

환경변수:
  EXHIBITION_PREP_WORKER_ID          기본 "exhibition-worker-<pid>"
  EXHIBITION_PREP_POLL_INTERVAL_SEC  기본 "5"
  EXHIBITION_PREP_STALE_MINUTES      기본 "20"
  EXHIBITION_MAX_INPUT_SIDE          기본 "2048" (전시 실행에서만 입력 긴 변 상한)
  EXHIBITION_HANDOFF_MODE / _URL     READY 직후 외부 전시 시스템에 1회 자동 핸드오프
                                     (exhibition_handoff_client 참고). off 면 보내지 않는다.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

logging.basicConfig(
    level=os.getenv("EXHIBITION_PREP_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("exhibition_prep_worker")

_SHOULD_STOP = False


def _handle_shutdown_signal(signum, frame):  # noqa: ARG001
    global _SHOULD_STOP
    logger.info("shutdown signal received — finishing current run")
    _SHOULD_STOP = True


def main() -> None:
    from .. import main as _backend_main  # noqa: F401  (import만으로 dotenv 로딩 트리거)
    from ..services import exhibition_prep_service, exhibition_prep_store, exhibition_queue_service
    from ..services.exhibition_handoff_client import ExhibitionHandoffClient

    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    store, artifacts = exhibition_prep_store.default_stores()
    worker_id = exhibition_prep_store.worker_id_from_env()
    poll_interval = float(os.getenv("EXHIBITION_PREP_POLL_INTERVAL_SEC", "5"))
    stale_minutes = int(os.getenv("EXHIBITION_PREP_STALE_MINUTES", "20"))
    handoff_client = ExhibitionHandoffClient()
    logger.info("exhibition prep worker started: worker_id=%s handoff=%s", worker_id, handoff_client.config.mode)

    while not _SHOULD_STOP:
        try:
            run = store.claim_next(worker_id, stale_after_minutes=stale_minutes)
        except Exception:
            logger.exception("claim failed — retrying in %ss", poll_interval)
            time.sleep(poll_interval)
            continue
        if not run:
            time.sleep(poll_interval)
            continue
        logger.info("run claimed: id=%s attempts=%s", run["id"], run.get("attempts"))
        final = exhibition_prep_service.process_run(
            run, store=store, artifacts=artifacts, worker_id=worker_id
        )
        logger.info("run finished: id=%s status=%s reasons=%s error=%s",
                    run["id"], final.get("status"), final.get("review_reasons"), final.get("error_code"))
        if final.get("status") == exhibition_prep_store.STATUS_READY and handoff_client.config.enabled:
            # 자동 1회. 실패는 HANDOFF_FAILED 로 남고 운영자가 POST /{run_id}/handoff 로 재시도한다.
            try:
                row = exhibition_prep_service.hand_off_run(
                    run["id"], store=store, artifacts=artifacts, client=handoff_client
                )
                logger.info("handoff: id=%s status=%s error=%s",
                            run["id"], row.get("handoff_status"), row.get("handoff_error_code"))
            except exhibition_prep_service.HandoffStateError as e:
                logger.warning("handoff skipped: id=%s %s", run["id"], e.code)
        if run.get("queue_number") is not None and run.get("exhibition_id"):
            # 스태프 접수 건이면 비어 있는 UP_NEXT 를 채운다 (스태프 화면 폴링도 같은 일을 한다).
            try:
                exhibition_queue_service.promote_up_next(run["exhibition_id"], store=store)
            except Exception:
                logger.exception("queue promote failed: exhibition=%s", run["exhibition_id"])

    logger.info("worker stopped.")


if __name__ == "__main__":
    sys.exit(main() or 0)
