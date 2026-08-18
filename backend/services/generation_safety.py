"""
유료 제출 전 안전장치 두 가지.

왜 필요한가 (실제로 당한 사고)
------------------------------
COME_CLOSER 첫 시도에서 fal 제출은 **성공해 과금까지 됐는데**, 바로 다음 줄의
register_generation_job 이 `attempt` 컬럼 없음(PGRST204)으로 실패했다. 결과:
  * 돈은 나갔고
  * job 행이 없어 웹훅이 매칭할 대상이 없고
  * external_id 를 아무 데도 기록하지 않아 복구조차 불가능했다

그래서 둘을 넣는다:
  1) 제출 **전에** 신뢰성 경로가 요구하는 컬럼이 전부 있는지 확인한다.
  2) 제출 **직후, DB 쓰기 전에** 복구에 필요한 정보를 먼저 로그로 남긴다.

프롬프트·프로바이더 선택·과금·재시도·검증 임계값·레거시 4종 계약은 건드리지 않는다.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

#: 현재 신뢰성 경로(후보→검증→승격→재시도→세션 확정)가 실제로 쓰는 컬럼.
#: 여기 없는 컬럼에 쓰기를 시도하면 제출 후 DB 단계에서 터진다.
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "motion_generation_jobs": ("candidate_url", "attempt", "validation", "promoted_at"),
    "credit_generation_sessions": ("refunded_at", "finalized_at"),
}

_cache: Optional[tuple[bool, tuple[str, ...]]] = None


class SchemaNotReadyError(RuntimeError):
    """필수 컬럼이 없어 제출을 막았다. **프로바이더 호출 전에** 발생한다."""

    def __init__(self, missing: tuple[str, ...]):
        self.missing = missing
        super().__init__(
            "생성 신뢰성 경로에 필요한 컬럼이 없습니다: "
            + ", ".join(missing)
            + ". 마이그레이션을 적용하기 전에는 유료 제출을 하지 않습니다."
        )


def reset_schema_cache() -> None:
    """테스트/마이그레이션 적용 직후용."""
    global _cache
    _cache = None


def verify_reliability_schema(*, use_cache: bool = True) -> tuple[bool, tuple[str, ...]]:
    """
    필수 컬럼 존재 여부를 확인한다.

    Returns: (ok, missing)  — missing 은 "table.column" 문자열들.
    DB 를 쓰지 않는 구성(인메모리 모의)에서는 확인할 대상이 없으므로 통과시킨다.
    """
    global _cache
    if use_cache and _cache is not None:
        return _cache

    from . import generated_motions_service as gms

    if not gms._use_db():
        _cache = (True, ())
        return _cache

    sb = gms._supabase()
    if sb is None:
        # Supabase 미구성 — 여기서 막으면 로컬 개발이 불가능해진다.
        _cache = (True, ())
        return _cache

    missing: list[str] = []
    for table, cols in REQUIRED_COLUMNS.items():
        for col in cols:
            try:
                sb.table(table).select(col).limit(1).execute()
            except Exception:
                missing.append(f"{table}.{col}")

    result = (not missing, tuple(missing))
    if use_cache:
        _cache = result
    return result


def ensure_reliability_schema() -> None:
    """스키마가 준비되지 않았으면 예외. 프로바이더 호출 직전에 부른다."""
    ok, missing = verify_reliability_schema()
    if not ok:
        logger.error(
            "유료 제출 차단 — 누락 컬럼 %d개: %s", len(missing), ", ".join(missing)
        )
        raise SchemaNotReadyError(missing)


def log_submission_receipt(
    *,
    provider: str,
    provider_model: Optional[str],
    external_id: str,
    session_id: str,
    action_id: str,
) -> None:
    """
    제출 성공 직후, **DB 쓰기보다 먼저** 복구 정보를 남긴다.

    이 로그가 있으면 이후 DB insert 가 실패해도 프로바이더 request_id 를 알 수 있어
    수동 조회·복구가 가능하다. 첫 COME_CLOSER 시도가 실패한 이유가 정확히 이것이
    없었기 때문이다.
    """
    logger.warning(
        "SUBMISSION RECEIPT | provider=%s model=%s external_id=%s session_id=%s action_id=%s "
        "| 프로바이더가 이미 수락(과금)했다. 이후 DB 단계가 실패하면 이 external_id 로 조회·복구할 것.",
        provider, provider_model, external_id, session_id, action_id,
    )
