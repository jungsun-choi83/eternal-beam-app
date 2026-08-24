"""
Soul Trace 편지 **참조** — Eternal Beam 은 편지를 만들지 않는다.

    Soul Trace 가 생성한다  →  Eternal Beam 이 가리킨다(import/link)

이 모듈에 **생성 경로가 없다.** LLM 도, 프롬프트도, 템플릿도, 문장을 만들어 내는
어떤 코드도 없다. 본문은 항상 밖에서 들어오고, 들어오지 않으면 편지는 없다.
그것이 "중복 편지를 만들지 않는다"의 실제 보장이다 — 만들 수단 자체를 두지 않는다.

── 왜 스냅샷을 함께 보관하는가 ───────────────────────────────────────────────
인쇄는 되돌릴 수 없다. 주문 시점의 본문이 남아 있어야 "고객이 받은 종이"와
"지금 화면의 편지"가 달라졌을 때 무엇이 인쇄됐는지 알 수 있다. Soul Trace 가
나중에 편지를 고쳐도 이미 인쇄된 주문은 흔들리지 않는다.

── 현재 상태 (솔직하게) ──────────────────────────────────────────────────────
⚠️ Soul Trace 는 아직 **백엔드가 없다.** 프론트에 결과 화면(soul-trace-result-screen)
   하나가 있고 하드코딩된 데모 본문을 쓰며, 앱 어디에도 마운트돼 있지 않다.
   그래서 지금 이 모듈은 **통합 지점**으로 존재한다: 호출부가 편지 본문을 넘겨
   등록하고, Soul Trace 가 실제 API 를 갖게 되면 그쪽에서 받아 같은 자리에 넣으면
   된다. 주문 스키마는 다시 손대지 않아도 된다.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

SOURCE_SOUL_TRACE = "soul_trace"


class LetterError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("SOUL_TRACE_LETTERS_TABLE", "soul_trace_letters")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_LETTERS: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_LETTERS.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SoulTraceLetter:
    letter_id: str
    user_id: str
    pet_id: Optional[str]
    source_letter_id: Optional[str]
    source: str
    child_name: Optional[str]
    letter_kicker: Optional[str]
    letter_body: Optional[str]
    letter_excerpt: Optional[str]
    partner_id: Optional[str] = None
    partner_type: Optional[str] = None
    partner_name: Optional[str] = None
    #: 가져온 시각. 목록 정렬(최신 우선)의 근거이므로 select 에 반드시 포함한다.
    imported_at: Optional[str] = None


_SELECT = (
    "letter_id, user_id, pet_id, source_letter_id, source, child_name, "
    "letter_kicker, letter_body, letter_excerpt, partner_id, partner_type, partner_name, "
    "imported_at"
)


def _to_letter(row: dict[str, Any]) -> SoulTraceLetter:
    return SoulTraceLetter(
        letter_id=str(row.get("letter_id") or ""),
        user_id=str(row.get("user_id") or ""),
        pet_id=(row.get("pet_id") or None),
        source_letter_id=(row.get("source_letter_id") or None),
        source=str(row.get("source") or SOURCE_SOUL_TRACE),
        child_name=(row.get("child_name") or None),
        letter_kicker=(row.get("letter_kicker") or None),
        letter_body=(row.get("letter_body") or None),
        letter_excerpt=(row.get("letter_excerpt") or None),
        partner_id=(row.get("partner_id") or None),
        partner_type=(row.get("partner_type") or None),
        partner_name=(row.get("partner_name") or None),
        imported_at=(row.get("imported_at") or None),
    )


def derive_letter_id(user_id: str, source_letter_id: str) -> str:
    """
    (사용자, Soul Trace 편지 id) → 우리 쪽 letter_id. **결정적이다.**

    같은 편지를 두 번 들여와도 같은 id 가 나오므로 행이 하나로 수렴한다.
    무작위 id 를 쓰면 import 를 두 번 부른 것만으로 편지가 복제된다 — 요구사항이
    금지한 바로 그것이다.
    """
    digest = hashlib.sha256(f"{user_id}|{source_letter_id}".encode("utf-8")).hexdigest()
    return f"stl_{digest[:24]}"


async def link_letter(
    *,
    user_id: str,
    source_letter_id: str,
    pet_id: str | None = None,
    child_name: str | None = None,
    letter_kicker: str | None = None,
    letter_body: str | None = None,
    letter_excerpt: str | None = None,
    partner_id: str | None = None,
    partner_type: str | None = None,
    partner_name: str | None = None,
) -> SoulTraceLetter:
    """
    Soul Trace 편지를 등록/갱신한다. **만들지 않는다 — 받는다.**

    본문이 비어 있으면 거절한다. 여기서 기본 문구를 채워 넣으면 그 순간
    Eternal Beam 이 편지를 "생성"한 것이 되고, 고객은 Soul Trace 가 쓴 적 없는
    문장이 인쇄된 종이를 받는다.

    멱등: 같은 (user, source_letter_id) 면 같은 letter_id 로 수렴한다.
    """
    uid = (user_id or "").strip()
    src = (source_letter_id or "").strip()
    if not uid or not src:
        raise LetterError("LETTER_SOURCE_REQUIRED", "source_letter_id 가 필요합니다.")

    body = (letter_body or "").strip()
    if not body:
        raise LetterError(
            "LETTER_BODY_REQUIRED",
            "편지 본문이 필요합니다. Eternal Beam 은 편지를 생성하지 않습니다.",
        )

    letter_id = derive_letter_id(uid, src)
    row: dict[str, Any] = {
        "letter_id": letter_id,
        "user_id": uid,
        "pet_id": (pet_id or "").strip() or None,
        "source_letter_id": src,
        "source": SOURCE_SOUL_TRACE,
        "child_name": (child_name or "").strip() or None,
        "letter_kicker": (letter_kicker or "").strip() or None,
        "letter_body": body,
        "letter_excerpt": (letter_excerpt or "").strip() or None,
        # 귀속은 Soul Trace 가 확정한 값이다. 여기서 만들어 내지 않는다.
        "partner_id": (partner_id or "").strip() or None,
        "partner_type": (partner_type or "").strip() or None,
        "partner_name": (partner_name or "").strip() or None,
        "imported_at": _now().isoformat(),
    }

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).upsert(row, on_conflict="letter_id").execute()
        except Exception as e:
            logger.exception("Soul Trace 편지 등록 실패 (user=%s src=%s)", uid, src)
            raise LetterError(
                "LETTER_STORE_UNAVAILABLE", "편지를 저장하지 못했습니다.", status=503
            ) from e
        return _to_letter(row)

    _MOCK_LETTERS[letter_id] = row
    return _to_letter(row)


async def link_pet(
    *, user_id: str, letter_id: str, pet_id: str
) -> SoulTraceLetter:
    """
    이미 가져온 편지에 canonical petId 를 붙인다. **편지도 펫도 만들지 않는다.**

    왜 별도 단계인가: Soul Trace 만 마친 사용자는 아직 펫이 없다. 클레임 시점에
    펫을 요구하면 편지를 받을 수 없고, 여기서 펫을 만들어 주면 pet_${content_id}
    규약 밖의 두 번째 펫 신원이 생긴다. 그래서 편지는 pet_id = NULL 로 먼저
    들어오고, 펫이 생긴 뒤 이 함수가 연결한다.

    두 소유권을 **모두** 확인한다 — 내 편지인가, 그리고 내 펫인가.
    """
    uid = (user_id or "").strip()
    lid = (letter_id or "").strip()
    pid = (pet_id or "").strip()
    if not pid:
        raise LetterError("PET_REQUIRED", "pet_id 가 필요합니다.")

    # 내 편지인가. 아니면 404 (존재 여부를 알려 주지 않는다).
    letter = await assert_owned(uid, lid)

    # 내 펫인가. 검사가 없으면 남의 펫에 내 편지를 붙여 주문할 수 있다.
    from . import pet_ownership

    try:
        await pet_ownership.assert_owned(uid, pid)
    except pet_ownership.PetOwnershipError as e:
        raise LetterError(
            getattr(e, "code", "PET_NOT_OWNED"),
            getattr(e, "message", "이 펫에 접근할 권한이 없습니다."),
            status=getattr(e, "status", 403),
        ) from e

    if letter.pet_id == pid:
        return letter  # 이미 연결돼 있다 — 멱등.

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).update({"pet_id": pid}).eq(
                "letter_id", lid
            ).eq("user_id", uid).execute()
        except Exception as e:
            logger.exception("편지-펫 연결 실패 (letter=%s pet=%s)", lid, pid)
            raise LetterError(
                "LETTER_STORE_UNAVAILABLE", "편지를 저장하지 못했습니다.", status=503
            ) from e
    else:
        row = _MOCK_LETTERS.get(lid)
        if row is not None:
            row["pet_id"] = pid

    # ⚠️ 필드를 하나씩 옮겨 적으면 **새로 생긴 컬럼이 조용히 사라진다.** 실제로
    # partner_id/type/name 이 여기서 빠져 있었고, 그래서 /letter/link-pet 응답만
    # 귀속이 없는 편지를 돌려주고 있었다(DB 행은 멀쩡했다). replace 로 바꾼 값만
    # 갈아 끼운다 — 앞으로 컬럼이 늘어도 여기서 다시 빠지지 않는다.
    return replace(letter, pet_id=pid)


async def get_letter(letter_id: str) -> Optional[SoulTraceLetter]:
    lid = (letter_id or "").strip()
    if not lid:
        return None

    if _use_db() and _supabase():
        try:
            r = _supabase().table(_table()).select(_SELECT).eq("letter_id", lid).limit(1).execute()
            data = getattr(r, "data", None) or []
            return _to_letter(data[0]) if data else None
        except Exception as e:
            logger.exception("Soul Trace 편지 조회 실패 (letter=%s)", lid)
            raise LetterError(
                "LETTER_STORE_UNAVAILABLE", "편지를 확인하지 못했습니다.", status=503
            ) from e

    row = _MOCK_LETTERS.get(lid)
    return _to_letter(row) if row else None


async def list_letters(user_id: str) -> list[SoulTraceLetter]:
    """
    내 편지들 — **최신 가져오기 순.**

    ⚠️ 정렬은 방어선이지 선택 근거가 아니다. 올바른 선택 근거는 pet_id 다
    (편지 ↔ 펫이 1:1 로 묶여 있어야 한다). 예전에는 여기에 order 절이 아예 없었고,
    호출부가 그 순서 없는 목록의 `rows[0]` 을 집었다. Postgres 는 ORDER BY 가
    없으면 힙 순서를 돌려주므로 실제로는 **가장 오래된 편지**가 먼저 나왔고,
    새 편지를 몇 번을 가져오든 결제에는 늘 옛날 편지가 실렸다.

    이제 최신이 먼저 온다. 그래도 호출부는 pet_id 로 고른다 — 정렬에만 기대면
    "펫 A 를 주문하는데 방금 가져온 편지 B 가 붙는" 반대 방향 사고가 난다.
    """
    uid = (user_id or "").strip()
    if not uid:
        return []

    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_table())
                .select(_SELECT)
                .eq("user_id", uid)
                .order("imported_at", desc=True)
                .execute()
            )
            return [_to_letter(row) for row in (getattr(r, "data", None) or [])]
        except Exception as e:
            logger.exception("Soul Trace 편지 목록 조회 실패 (user=%s)", uid)
            raise LetterError(
                "LETTER_STORE_UNAVAILABLE", "편지 목록을 불러오지 못했습니다.", status=503
            ) from e

    rows = [r for r in _MOCK_LETTERS.values() if r.get("user_id") == uid]
    rows.sort(key=lambda r: str(r.get("imported_at") or ""), reverse=True)
    return [_to_letter(r) for r in rows]


async def assert_owned(user_id: str, letter_id: str) -> SoulTraceLetter:
    """
    이 편지가 이 사용자의 것인가.

    주문이 letter_id 를 받으므로, 검사가 없으면 남의 편지를 자기 주문에 붙여
    **남의 편지를 인쇄해 배송**받을 수 있다. 실물이라 되돌릴 수 없다.
    """
    letter = await get_letter(letter_id)
    if not letter or letter.user_id != (user_id or "").strip():
        # 남의 편지도 "없음"과 같은 답을 준다 — 존재 여부를 알려 주지 않는다.
        raise LetterError("LETTER_NOT_FOUND", "편지를 찾을 수 없습니다.", status=404)
    return letter
