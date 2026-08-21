"""
공개 Shaker 엔드포인트의 남용 방어 — **프로세스 로컬 고정 창(fixed window)**.

이것이 막는 것과 막지 못하는 것을 분명히 해 둔다. 과대평가하면 다음 사람이
"레이트 리밋이 있으니 괜찮다"고 믿고 진짜 방어를 빼먹는다.

막는 것:
  * 한 IP 에서의 단순 반복 조회 — 공유된 링크를 스크래핑하듯 긁어 가는 것
  * 실수로 무한 폴링하는 클라이언트가 DB 를 두드리는 것

막지 못하는 것:
  * 토큰 추측 — 애초에 256비트라 리밋이 없어도 성립하지 않는다.
    **리밋은 추측 방어가 아니다.** 추측 방어는 엔트로피가 한다.
  * 분산 IP 공격 — 프로세스 로컬이라 워커가 여러 개면 워커 수만큼 곱해진다.
    진짜 방어가 필요해지면 여기가 아니라 엣지(Vercel/Cloudflare)에서 해야 한다.

고정 창을 고른 이유: 경계에서 최대 2배까지 통과하는 알려진 약점이 있지만,
의존성 없이 O(1) 이고 이 용도에는 정밀도가 남는다. 슬라이딩 로그를 쓰면 IP 마다
타임스탬프 배열이 쌓여, 방어하려던 바로 그 남용이 메모리 증가로 바뀐다.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

#: 창 하나의 길이(초).
WINDOW_SECONDS = 60

#: 창당 허용 요청 수. QR 을 찍은 사람은 보통 1~3회면 충분하고, 재시도·새로고침을
#: 감안해도 여유가 크다.
DEFAULT_LIMIT = 60

#: 추적 IP 상한. 넘으면 통째로 비운다 — 남용자가 IP 를 돌려 가며 메모리를 늘리는
#: 것을 막는다. 비우면 잠깐 관대해지지만, 메모리가 무한히 늘어나는 것보다 낫다.
_MAX_TRACKED = 10_000

_lock = threading.Lock()
_counters: dict[str, tuple[int, int]] = {}  # key -> (window_index, count)


def __reset_for_tests() -> None:
    with _lock:
        _counters.clear()


def _limit() -> int:
    raw = (os.getenv("SHAKER_PUBLIC_RATE_LIMIT") or "").strip()
    if not raw:
        return DEFAULT_LIMIT
    try:
        v = int(raw)
    except ValueError:
        return DEFAULT_LIMIT
    # 0 이하는 "무제한"이 아니라 설정 실수로 본다. 무제한을 원하면 끄는 스위치가
    # 따로 있어야지, 0 이 우연히 그 뜻이 되면 안 된다.
    return v if v > 0 else DEFAULT_LIMIT


def _enabled() -> bool:
    return (os.getenv("SHAKER_RATE_LIMIT_ENABLED", "1").strip().lower()
            not in ("0", "false", "no"))


@dataclass(frozen=True)
class RateVerdict:
    allowed: bool
    #: 남은 허용량 (표시·헤더용).
    remaining: int
    #: 창이 새로 열릴 때까지 남은 초. 429 의 Retry-After 에 그대로 쓴다.
    retry_after: int


def check(client_key: str, *, now: float | None = None) -> RateVerdict:
    """
    이 클라이언트가 지금 한 번 더 호출해도 되는가. **호출 자체가 카운트다.**

    client_key 는 보통 IP 다. 프록시 뒤라면 라우터가 X-Forwarded-For 의 첫 항목을
    넘긴다 — 위조 가능하지만, 위조하는 쪽은 어차피 자기 카운터만 흩뜨릴 뿐이라
    남에게 피해를 주지 못한다.
    """
    if not _enabled():
        return RateVerdict(allowed=True, remaining=_limit(), retry_after=0)

    t = time.time() if now is None else now
    window = int(t // WINDOW_SECONDS)
    reset_in = int(WINDOW_SECONDS - (t % WINDOW_SECONDS)) or WINDOW_SECONDS
    limit = _limit()
    key = (client_key or "unknown").strip() or "unknown"

    with _lock:
        if len(_counters) > _MAX_TRACKED:
            _counters.clear()
        w, count = _counters.get(key, (window, 0))
        if w != window:
            w, count = window, 0
        count += 1
        _counters[key] = (w, count)

    if count > limit:
        return RateVerdict(allowed=False, remaining=0, retry_after=reset_in)
    return RateVerdict(allowed=True, remaining=max(0, limit - count), retry_after=reset_in)
