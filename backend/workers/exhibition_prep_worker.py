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
  EXHIBITION_POSE_GATE_MODE          clip (기본) | off — 로컬 포즈/시점 게이트 (exhibition_pose_gate)
  EXHIBITION_POSE_NORMALIZATION_MODE gpt_image | mock | off(기본) — 게이트가 NEEDS 일 때의 1회 생성
                                     (gpt_image 는 OPENAI_API_KEY 등 정본 생성과 같은 OpenAI 설정을 쓴다)

READY 가 되면 이 워커는 아무것도 보내지 않고 멈춘다 — 패키지가 준비됐다는 뜻만
남긴다. 디바이스/컨트롤러가 GET /api/exhibition/device/pet/{visitor_number} 로
원하는 시점에 직접 당겨간다 (backend/routers/exhibition_device_v1.py). 이전에는
여기서 READY 직후 exhibition_handoff_client 로 1회 자동 push 했지만 제거했다 —
exhibition_prep_service.hand_off_run()/POST /{run_id}/handoff 는 그대로 남아
있고 운영자가 수동으로 외부 HTTP 수신기나 Device Gateway 로 밀어 보낼 때 쓸 수
있지만, 이 워커는 더 이상 그것을 자동 호출하지 않는다.
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
    from ..services import (
        exhibition_pose_gate,
        exhibition_prep_service,
        exhibition_prep_store,
        exhibition_queue_service,
    )

    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    store, artifacts = exhibition_prep_store.default_stores()
    worker_id = exhibition_prep_store.worker_id_from_env()
    poll_interval = float(os.getenv("EXHIBITION_PREP_POLL_INTERVAL_SEC", "5"))
    stale_minutes = int(os.getenv("EXHIBITION_PREP_STALE_MINUTES", "20"))
    # 포즈 게이트 모델(YOLO + CLIP)을 기동 시 한 번 올린다 — 이미지마다 다시 읽지 않는다.
    try:
        exhibition_pose_gate.preload()
    except Exception:
        logger.exception("pose gate preload failed — gate will answer GOOD until it loads")
    logger.info(
        "exhibition prep worker started: worker_id=%s pose_gate=%s",
        worker_id, exhibition_pose_gate.mode_from_env(),
    )

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
        # READY 는 "패키지 준비됨"만 뜻한다 — 더 이상 여기서 자동으로 보내지 않는다.
        # 디바이스/컨트롤러가 GET /api/exhibition/device/pet/{visitor_number} 로 당겨간다.
        if run.get("queue_number") is not None and run.get("exhibition_id"):
            # 스태프 접수 건이면 비어 있는 UP_NEXT 를 채운다 (스태프 화면 폴링도 같은 일을 한다).
            try:
                exhibition_queue_service.promote_up_next(run["exhibition_id"], store=store)
            except Exception:
                logger.exception("queue promote failed: exhibition=%s", run["exhibition_id"])

    logger.info("worker stopped.")


if __name__ == "__main__":
    sys.exit(main() or 0)
