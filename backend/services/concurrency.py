"""
무료(no-credit) 동시성 유틸리티.

여기서 하는 일은 **레이턴시 숨기기**뿐이다 — 유료 생성/VLM 호출 횟수를 늘리지
않는다. 늘어나는 것은 동시에 떠 있는 스레드/커넥션 수뿐이므로, Render 512MB
관례에 따라 기본 동시성 상한을 작게 둔다(환경변수로 조정 가능).
"""

from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Sequence, TypeVar

T = TypeVar("T")

#: 인테이크/레퍼런스 분석 단계의 기본 동시성 상한. 사진 장수(≤MAX_ORIGINALS_PER_PET=3)
#: 보다 살짝 넉넉하게 잡되, 무한정 풀지 않는다 — 저사양 배포에서 스레드/커넥션이
#: 한꺼번에 튀는 것을 막는다.
DEFAULT_INTAKE_CONCURRENCY = int(os.getenv("INTAKE_CONCURRENCY_LIMIT", "4"))


async def gather_bounded(
    factories: Sequence[Callable[[], Awaitable[T]]],
    *,
    limit: int = DEFAULT_INTAKE_CONCURRENCY,
    return_exceptions: bool = False,
) -> list[T]:
    """
    최대 `limit` 개까지 동시에 실행하고 **입력 순서 그대로** 결과를 돌려준다.

    `factories` 는 코루틴이 아니라 코루틴을 만드는 0-인자 콜러블이어야 한다 —
    코루틴 객체를 미리 만들면 세마포어를 얻기 전에 이미 실행이 시작될 수 있는
    이벤트 루프 구현이 있어, 동시성 상한이 이름뿐이 될 수 있기 때문이다.

    호출자가 순서에 의존하는 로직(결정론적 primary 선택 등)을 그대로 쓸 수
    있도록, 완료 순서가 아니라 **제출 순서**로 결과 리스트를 채운다.

    `return_exceptions=True` 면 한 항목의 실패가 나머지를 취소하지 않는다 —
    결과 리스트에서 실패한 자리는 예외 객체 그대로 들어온다(호출자가 사진별로
    성공/실패를 나눠 처리할 수 있게, 즉 한 사진의 실패가 다른 사진을 막지
    않게 하려는 목적이다).
    """
    if not factories:
        return []
    sem = asyncio.Semaphore(max(1, limit))

    async def _run(factory: Callable[[], Awaitable[T]]) -> T:
        async with sem:
            return await factory()

    return list(
        await asyncio.gather(*(_run(f) for f in factories), return_exceptions=return_exceptions)
    )
