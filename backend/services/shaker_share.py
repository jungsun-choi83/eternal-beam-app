"""
QR Shaker 공유 링크 — **발급·해석·폐기만 한다.**

이 모듈이 존재하는 이유는 하나다: 공개 Shaker 가 **pet_id 로 조회하지 않게** 만드는 것.

    나쁜 설계:  GET /shaker/pet?pet_id=pet_abc   → 아무나 id 를 넣어 남의 펫을 연다
    이 설계:    GET /shaker/pet?share=<token>    → 토큰이 펫을 데려온다

pet_id 는 URL 에 남아 있지만(QR 가독성·표시용) 서버는 그것을 **조회키로 쓰지 않는다**.
넘어오면 토큰이 데려온 펫과 같은지 확인만 하고, 다르면 거절한다. 즉 pet_id 를 바꿔
넣는 것으로 얻을 수 있는 것이 없다.

── 이 모듈이 절대 하지 않는 것 ────────────────────────────────────────────────
생성하지 않는다. premium_generation 도 generation_queue 도 import 하지 않는다 —
behavior_preferences.py 와 같은 규칙이다. 그럴 수 있는 경로를 두지 않는 것이
"Shaker 는 절대 생성하지 않는다"를 지키는 가장 확실한 방법이다.

구독·지갑·주문·결제·프로바이더를 읽지 않는다. 이 모듈에는 그런 import 가 없다.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: 토큰 엔트로피(바이트). 32바이트 = 256비트 → token_urlsafe 로 43자.
#: 추측 공격이 의미를 갖지 않는 수준이며, 인쇄된 QR 에 들어가도 부담되지 않는 길이다.
TOKEN_BYTES = 32

#: 입력 토큰의 허용 길이 범위. 해시하기 **전에** 거른다 — 임의 길이 입력을
#: sha256 에 그대로 흘리지 않기 위한 값싼 방어다.
TOKEN_MIN_LEN = 20
TOKEN_MAX_LEN = 128

#: token_urlsafe 가 만들어 내는 문자 집합.
_TOKEN_ALPHABET = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


class ShareError(Exception):
    """공유 링크를 발급/해석/폐기할 수 없다. code 로 HTTP 변환을 구분한다."""

    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class ShareRecord:
    """
    해석된 공유 링크.

    ⚠️ user_id 가 들어 있다. **공개 응답에 그대로 실으면 안 된다** — READY 자산
    조회(generated_motions 는 user_id 로 키를 잡는다)와 소유권 검사에만 쓴다.
    라우터의 응답 모델이 허용 목록(allowlist)이라 실수로 새어 나가지 않는다.

    ⚠️ breathing_url / poster_url 은 **발급 시점의 값**이며 서명이 이미 만료됐을
    수 있다(인쇄된 QR 은 서명보다 오래 산다). 그대로 내보내면 안 된다 — 라우터가
    object_path 로 새 서명을 만든다. 경로가 없을 때만 URL 이 정본이다.
    """

    share_id: str
    user_id: str
    pet_id: str
    pet_name: Optional[str]
    breathing_url: str
    poster_url: Optional[str]
    created_at: Optional[str] = None
    revoked_at: Optional[str] = None
    expires_at: Optional[str] = None
    #: 스토리지 객체 경로 — **만료되지 않는 정본.** 없으면 URL 로 폴백한다.
    breathing_bucket: Optional[str] = None
    breathing_object_path: Optional[str] = None
    poster_bucket: Optional[str] = None
    poster_object_path: Optional[str] = None
    #: 이 링크를 만든 주체 (운영자 또는 고객 본인). 감사 추적용.
    created_by: Optional[str] = None
    #: CUSTOMER | OPS | LETTER | MEMORY_BOX
    purpose: Optional[str] = None
    #: Phase 12–13 주문 참조. 지금은 항상 None — 붙을 자리만 예약돼 있다.
    order_ref: Optional[str] = None


def _table() -> str:
    return os.getenv("SHAKER_SHARES_TABLE", "shaker_shares")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


#: DB 가 없을 때의 인메모리 저장 (로컬/테스트). key = token_hash
_MOCK_SHARES: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_SHARES.clear()


# ── 토큰 ──────────────────────────────────────────────────────────────────────


def mint_token() -> str:
    """새 공유 토큰(원문). 이 값은 **발급 응답 한 번**만 존재한다."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """
    저장·조회용 sha256 hex.

    salt 를 쓰지 않는 이유: 입력이 256비트 난수라 사전 공격/레인보우 테이블이
    성립하지 않는다. salt 를 넣으면 PK 조회가 불가능해져(행마다 salt 가 다르므로
    전수 스캔) 얻는 것 없이 공개 엔드포인트를 느리게 만든다.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_token(raw: str | None) -> str:
    """
    입력 토큰 검증 → 원문.

    형식이 틀린 것은 **조회하기 전에** 거른다. DB 왕복을 아끼는 것도 있지만,
    더 중요한 것은 공개 엔드포인트가 임의 입력을 저장소까지 흘려보내지 않는 것이다.
    """
    t = (raw or "").strip()
    if not t:
        raise ShareError("SHARE_TOKEN_REQUIRED", "공유 토큰이 필요합니다.", status=400)
    if len(t) < TOKEN_MIN_LEN or len(t) > TOKEN_MAX_LEN:
        # 길이가 틀린 것도 "없는 링크"와 같은 답을 준다 — 형식을 알려 주지 않는다.
        raise ShareError("SHARE_NOT_FOUND", "유효하지 않은 공유 링크입니다.", status=404)
    if not set(t) <= _TOKEN_ALPHABET:
        raise ShareError("SHARE_NOT_FOUND", "유효하지 않은 공유 링크입니다.", status=404)
    return t


# ── 발급 ──────────────────────────────────────────────────────────────────────


def _is_remote_http_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_share(
    *,
    user_id: str,
    pet_id: str,
    breathing_url: str,
    pet_name: str | None = None,
    poster_url: str | None = None,
    ttl_days: int | None = None,
    breathing_bucket: str | None = None,
    breathing_object_path: str | None = None,
    created_by: str | None = None,
    purpose: str | None = None,
    order_ref: str | None = None,
) -> tuple[str, str]:
    """
    새 공유 링크 발급 → (share_id, 원문 토큰).

    원문 토큰은 여기서 딱 한 번 반환되고 저장되지 않는다. 잃어버리면 재발급뿐이며,
    그것이 의도다 — 서버가 원문을 들고 있지 않아야 유출 시 피해가 없다.

    소유권 검사는 **호출부(라우터)의 책임**이다. 이 모듈은 저장소이지 인가 지점이
    아니다 — 그렇게 나눠야 인가 규칙이 라우터 한 곳에 모인다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise ShareError("PET_REQUIRED", "user_id 와 pet_id 가 필요합니다.", status=400)

    breathing = (breathing_url or "").strip()
    if not breathing:
        raise ShareError(
            "BREATHING_URL_REQUIRED",
            "BREATHING 영상 URL 이 필요합니다.",
            status=400,
        )
    # data: URL 은 브라우저가 재생할 수는 있지만 QR 로 나눠 줄 링크에 담기지 않는다.
    # 여기서 거르지 않으면 공유는 성공하고 재생만 조용히 실패한다.
    if not _is_remote_http_url(breathing):
        raise ShareError(
            "BREATHING_URL_NOT_REMOTE",
            "BREATHING URL 은 http(s) URL 이어야 합니다.",
            status=400,
        )
    poster = (poster_url or "").strip() or None
    if poster and not _is_remote_http_url(poster):
        raise ShareError(
            "POSTER_URL_NOT_REMOTE",
            "포스터 URL 은 http(s) URL 이어야 합니다.",
            status=400,
        )

    name = (pet_name or "").strip() or None
    token = mint_token()
    th = hash_token(token)
    share_id = f"shr_{uuid.uuid4().hex[:16]}"
    created = _now()
    expires = created + timedelta(days=int(ttl_days)) if ttl_days else None

    # 발급 시점에 객체 경로를 뽑아 둔다. URL 은 서명이 만료되지만 경로는 만료되지
    # 않으므로, 인쇄된 QR 이 서명보다 오래 살아도 해석 시 새 서명을 만들 수 있다.
    # 파싱되지 않는 URL(외부 CDN 등)은 경로가 없고, 그때는 URL 자체가 정본이다.
    from .asset_url_refresh import parse_storage_object

    b_obj = parse_storage_object(breathing)
    p_obj = parse_storage_object(poster) if poster else None

    # 호출부가 객체 위치를 이미 알고 있으면(운영 경로는 규약에서 유도해서 안다)
    # 그것을 쓴다. URL 파싱보다 정확하다 — 파싱은 URL 형식에 의존한다.
    b_bucket = (breathing_bucket or "").strip() or (b_obj.bucket if b_obj else None)
    b_path = (breathing_object_path or "").strip() or (b_obj.path if b_obj else None)

    row: dict[str, Any] = {
        "token_hash": th,
        "share_id": share_id,
        "user_id": uid,
        "pet_id": pid,
        "pet_name": name,
        "breathing_url": breathing,
        "poster_url": poster,
        "breathing_bucket": b_bucket,
        "breathing_object_path": b_path,
        "poster_bucket": p_obj.bucket if p_obj else None,
        "poster_object_path": p_obj.path if p_obj else None,
        "created_by": (created_by or "").strip() or uid,
        "purpose": (purpose or "CUSTOMER").strip().upper(),
        "order_ref": (order_ref or "").strip() or None,
        "created_at": created.isoformat(),
        "revoked_at": None,
        "expires_at": expires.isoformat() if expires else None,
    }

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).insert(row).execute()
        except Exception as e:
            logger.exception("Shaker 공유 발급 실패 (user=%s pet=%s)", uid, pid)
            raise ShareError(
                "SHARE_STORE_UNAVAILABLE",
                "공유 링크를 저장하지 못했습니다.",
                status=503,
            ) from e
        return share_id, token

    _MOCK_SHARES[th] = row
    return share_id, token


# ── 해석 ──────────────────────────────────────────────────────────────────────


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        s = str(raw).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        # 해석할 수 없는 시각은 **없는 것으로 보지 않는다** — 만료 판정에서
        # 조용히 통과시키면 만료된 링크가 영원히 열린다. 호출부가 만료로 취급한다.
        return _now()


def _to_record(row: dict[str, Any]) -> ShareRecord:
    return ShareRecord(
        share_id=str(row.get("share_id") or ""),
        user_id=str(row.get("user_id") or ""),
        pet_id=str(row.get("pet_id") or ""),
        pet_name=(row.get("pet_name") or None),
        breathing_url=str(row.get("breathing_url") or ""),
        poster_url=(row.get("poster_url") or None),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        revoked_at=(str(row["revoked_at"]) if row.get("revoked_at") else None),
        expires_at=(str(row["expires_at"]) if row.get("expires_at") else None),
        breathing_bucket=(row.get("breathing_bucket") or None),
        breathing_object_path=(row.get("breathing_object_path") or None),
        poster_bucket=(row.get("poster_bucket") or None),
        poster_object_path=(row.get("poster_object_path") or None),
        created_by=(row.get("created_by") or None),
        purpose=(row.get("purpose") or None),
        order_ref=(row.get("order_ref") or None),
    )


async def resolve_share(token: str, *, expected_pet_id: str | None = None) -> ShareRecord:
    """
    원문 토큰 → 공유 레코드. 실패는 전부 예외이며 **절대 None 을 돌려주지 않는다.**

    expected_pet_id 는 URL 의 petId 다. 조회에 쓰이지 않고 **일치 검사에만** 쓰인다.
    불일치를 404(없음)로 답하는 이유: "이 토큰은 유효하지만 다른 펫의 것"이라고
    알려 주면 토큰 하나로 pet_id 를 탐색할 수 있다는 힌트가 된다.

    폐기/만료는 404 가 아니라 410 으로 구분한다. 그 토큰을 가진 사람은 이미 링크를
    받은 사람이므로 "존재했지만 이제 닫혔다"를 알려 주는 것이 정보 유출이 아니라
    설명이다 — 추측으로는 유효 토큰에 도달할 수 없으므로 탐색 도구가 되지 않는다.
    """
    raw = normalize_token(token)
    th = hash_token(raw)

    row: Optional[dict[str, Any]] = None
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_table())
                .select(
                    "share_id, user_id, pet_id, pet_name, breathing_url, "
                    "poster_url, created_at, revoked_at, expires_at, "
                    "breathing_bucket, breathing_object_path, "
                    "poster_bucket, poster_object_path, "
                    "created_by, purpose, order_ref"
                )
                .eq("token_hash", th)
                .limit(1)
                .execute()
            )
            data = getattr(r, "data", None) or []
            row = data[0] if data else None
        except Exception as e:
            # 조회 실패를 "없음"으로 답하지 않는다 — 장애와 무효 링크는 다른 사건이고,
            # 사용자에게도 다르게 설명해야 한다(다시 시도 vs 링크를 다시 받기).
            logger.exception("Shaker 공유 조회 실패")
            raise ShareError(
                "SHARE_STORE_UNAVAILABLE",
                "공유 링크를 확인하지 못했습니다.",
                status=503,
            ) from e
    else:
        row = _MOCK_SHARES.get(th)

    if not row:
        raise ShareError("SHARE_NOT_FOUND", "유효하지 않은 공유 링크입니다.", status=404)

    rec = _to_record(row)

    if row.get("revoked_at"):
        raise ShareError("SHARE_REVOKED", "이 공유 링크는 해제되었습니다.", status=410)

    exp = _parse_ts(row.get("expires_at"))
    if exp and exp <= _now():
        raise ShareError("SHARE_EXPIRED", "이 공유 링크는 만료되었습니다.", status=410)

    want = (expected_pet_id or "").strip()
    if want and want != rec.pet_id:
        raise ShareError("SHARE_NOT_FOUND", "유효하지 않은 공유 링크입니다.", status=404)

    if not rec.breathing_url:
        # 발급 시 검증하므로 정상 경로에서는 나오지 않는다. 데이터가 손상된 경우
        # 빈 화면 대신 명시적인 상태를 준다.
        raise ShareError(
            "SHARE_ASSET_UNAVAILABLE",
            "이 펫의 BREATHING 영상을 찾을 수 없습니다.",
            status=503,
        )

    return rec


# ── 폐기 · 목록 (소유자 전용) ─────────────────────────────────────────────────


async def revoke_share(*, user_id: str, share_id: str) -> bool:
    """
    링크 폐기. 이미 폐기됐으면 False (멱등 — 두 번 눌러도 오류가 아니다).

    user_id 조건을 update 문에 **함께** 건다. 조회 후 검사하는 방식이면 그 사이에
    소유자가 바뀌는 경쟁이 이론상 가능하고, 무엇보다 검사를 빠뜨리기 쉽다.
    """
    uid = (user_id or "").strip()
    sid = (share_id or "").strip()
    if not uid or not sid:
        raise ShareError("SHARE_ID_REQUIRED", "share_id 가 필요합니다.", status=400)

    now = _now().isoformat()

    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_table())
                .update({"revoked_at": now})
                .eq("share_id", sid)
                .eq("user_id", uid)
                .is_("revoked_at", "null")
                .execute()
            )
            return bool(getattr(r, "data", None))
        except Exception as e:
            logger.exception("Shaker 공유 폐기 실패 (user=%s share=%s)", uid, sid)
            raise ShareError(
                "SHARE_STORE_UNAVAILABLE",
                "공유 링크를 해제하지 못했습니다.",
                status=503,
            ) from e

    for row in _MOCK_SHARES.values():
        if row.get("share_id") == sid and row.get("user_id") == uid:
            if row.get("revoked_at"):
                return False
            row["revoked_at"] = now
            return True
    return False


async def list_shares(*, user_id: str, pet_id: str | None = None) -> list[ShareRecord]:
    """
    소유자의 공유 링크 목록.

    ⚠️ 토큰은 들어 있지 않다 — 저장하지 않으므로 **돌려줄 수가 없다.** 이것이
    "원문을 저장하지 않는다"의 실제 결과다: 목록 화면은 링크를 다시 보여 줄 수
    없고, 잃어버렸으면 새로 발급해야 한다.
    """
    uid = (user_id or "").strip()
    if not uid:
        raise ShareError("PET_REQUIRED", "user_id 가 필요합니다.", status=400)
    pid = (pet_id or "").strip()

    if _use_db() and _supabase():
        try:
            q = (
                _supabase()
                .table(_table())
                .select(
                    "share_id, user_id, pet_id, pet_name, breathing_url, "
                    "poster_url, created_at, revoked_at, expires_at, "
                    "breathing_bucket, breathing_object_path, "
                    "poster_bucket, poster_object_path, "
                    "created_by, purpose, order_ref"
                )
                .eq("user_id", uid)
            )
            if pid:
                q = q.eq("pet_id", pid)
            r = q.execute()
            return [_to_record(row) for row in (getattr(r, "data", None) or [])]
        except Exception as e:
            logger.exception("Shaker 공유 목록 조회 실패 (user=%s)", uid)
            raise ShareError(
                "SHARE_STORE_UNAVAILABLE",
                "공유 링크 목록을 불러오지 못했습니다.",
                status=503,
            ) from e

    out = [
        _to_record(row)
        for row in _MOCK_SHARES.values()
        if row.get("user_id") == uid and (not pid or row.get("pet_id") == pid)
    ]
    return sorted(out, key=lambda r: r.created_at or "")
