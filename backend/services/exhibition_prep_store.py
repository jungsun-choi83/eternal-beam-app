"""
exhibition_prep_runs 테이블 + exhibition/{run_id}/ 저장소 — 전시 준비 실행 전용.

일반 펫 생성 상태(pet_generation_runs, pet_references, user_assets, 크레딧
원장)와 **테이블도 저장 경로도 공유하지 않는다**. 이 모듈이 건드리는 테이블은
`exhibition_prep_runs` 하나, 저장 경로는 `{EXHIBITION_STORAGE_PREFIX}/{run_id}/`
뿐이다 (test_exhibition_prep_run.py 가 이를 강제한다).

큐 패턴은 auto_rigging_jobs 와 같다 (Supabase 테이블 polling + 낙관적 클레임).
차이: 오래된 RUNNING 을 다시 잡을 때 claimed_at 까지 맞춰 두 워커가 동시에
가져가지 못하게 하고, 진행/결과 쓰기는 claimed_by 로 펜싱한다.

테이블 스키마: supabase/migrations/20261105000000_exhibition_prep_runs.sql
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

TABLE = "exhibition_prep_runs"

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_READY = "READY"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_FAILED = "FAILED"
TERMINAL_STATUSES = (STATUS_READY, STATUS_NEEDS_REVIEW, STATUS_FAILED)

STAGE_CUTOUT = "CUTOUT"
STAGE_MAPS = "MAPS"
STAGE_PACKAGE = "PACKAGE"

# 핸드오프 상태는 prep status 와 별도 컬럼(handoff_status)이다. prep status 는
# READY(= PACKAGE_READY) 로 남으므로 핸드오프 실패가 실행을 CUTOUT/MAPS 큐
# (QUEUED/RUNNING)로 되돌릴 수 없다. NULL = 아직 보내지 않음.
HANDOFF_SENDING = "HANDOFF_SENDING"
HANDOFF_CONFIRMED = "HANDOFF_CONFIRMED"
HANDOFF_FAILED = "HANDOFF_FAILED"

# 전시 대기열 표시 상태 (display_status) — 처리 상태와 별도 컬럼. NULL = 대기열 밖 실행.
DISPLAY_WAITING = "WAITING"
DISPLAY_UP_NEXT = "UP_NEXT"
DISPLAY_NOW_SHOWING = "NOW_SHOWING"
DISPLAY_COMPLETE = "COMPLETE"

#: 대기열 화면에 필요한 컬럼만 (manifest_json 같은 큰 JSON 은 읽지 않는다).
QUEUE_COLUMNS = (
    "id,exhibition_id,queue_number,pet_name,display_status,display_changed_at,status,stage,"
    "review_reasons,error_code,handoff_status,handoff_error_code,created_at"
)

#: 같은 실행을 이만큼 잡았는데도 끝나지 않으면 FAILED (워커 크래시 반복 방지).
MAX_ATTEMPTS = 3


def storage_prefix() -> str:
    return (os.getenv("EXHIBITION_STORAGE_PREFIX") or "exhibition").strip().strip("/") or "exhibition"


def storage_bucket() -> str:
    return (
        os.getenv("EXHIBITION_STORAGE_BUCKET")
        or os.getenv("SUPABASE_STORAGE_BUCKET")
        or "user-assets"
    )


def run_object_path(run_id: str, name: str) -> str:
    """exhibition/{run_id}/{name} — 실행 산출물의 유일한 경로 규칙."""
    rid = str(uuid.UUID(str(run_id)))  # 경로 조작 방지: UUID 만 허용
    if "/" in name or name.startswith("."):
        raise ValueError(f"invalid object name: {name!r}")
    return f"{storage_prefix()}/{rid}/{name}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore(Protocol):
    def insert(self, row: dict[str, Any]) -> dict[str, Any]: ...
    def get(self, run_id: str) -> Optional[dict[str, Any]]: ...
    def claim_next(self, worker_id: str, *, stale_after_minutes: int) -> Optional[dict[str, Any]]: ...
    def update(self, run_id: str, fields: dict[str, Any], *, claimed_by: Optional[str] = None) -> bool: ...
    def update_if(self, run_id: str, fields: dict[str, Any], *, match: dict[str, Any]) -> bool: ...
    def next_queue_number(self, exhibition_id: str) -> int: ...
    def list_queue(
        self, exhibition_id: str, display_statuses: list[str], *, limit: int, newest_first: bool = False
    ) -> list[dict[str, Any]]: ...


class ArtifactStore(Protocol):
    def put(self, path: str, data: bytes, content_type: str) -> None: ...
    def get(self, path: str) -> bytes: ...
    def signed_url(self, path: str, ttl_seconds: int = 3600) -> Optional[str]: ...


# ── Supabase ─────────────────────────────────────────────────────────────────


class SupabaseRunStore:
    def __init__(self, client) -> None:
        self._c = client

    def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        res = self._c.table(TABLE).insert(row).execute()
        if not res.data:
            raise RuntimeError(f"exhibition run insert failed: {res}")
        return res.data[0]

    def get(self, run_id: str) -> Optional[dict[str, Any]]:
        res = self._c.table(TABLE).select("*").eq("id", run_id).limit(1).execute()
        return res.data[0] if res.data else None

    def claim_next(self, worker_id: str, *, stale_after_minutes: int) -> Optional[dict[str, Any]]:
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)).isoformat()
        res = (
            self._c.table(TABLE)
            .select("*")
            .in_("status", [STATUS_QUEUED, STATUS_RUNNING])
            .order("created_at")
            .limit(20)
            .execute()
        )
        for row in res.data or []:
            if row["status"] == STATUS_RUNNING and (row.get("claimed_at") or "") >= stale_cutoff:
                continue
            attempts = int(row.get("attempts") or 0) + 1
            q = self._c.table(TABLE).update(
                {"status": STATUS_RUNNING, "claimed_by": worker_id, "claimed_at": _now_iso(), "attempts": attempts}
            ).eq("id", row["id"]).eq("status", row["status"])
            if row["status"] == STATUS_RUNNING:
                q = q.eq("claimed_at", row.get("claimed_at"))
            upd = q.execute()
            if upd.data:
                return upd.data[0]
        return None

    def update(self, run_id: str, fields: dict[str, Any], *, claimed_by: Optional[str] = None) -> bool:
        q = self._c.table(TABLE).update(fields).eq("id", run_id)
        if claimed_by is not None:
            q = q.eq("claimed_by", claimed_by)
        return bool(q.execute().data)

    def update_if(self, run_id: str, fields: dict[str, Any], *, match: dict[str, Any]) -> bool:
        """조건부 갱신(compare-and-set): match 의 모든 컬럼이 현재 값과 같을 때만. None = IS NULL."""
        q = self._c.table(TABLE).update(fields).eq("id", run_id)
        for col, val in match.items():
            q = q.is_(col, "null") if val is None else q.eq(col, val)
        return bool(q.execute().data)

    def next_queue_number(self, exhibition_id: str) -> int:
        """전시별 순차 번호 — exhibition_next_queue_number() 의 원자적 upsert."""
        res = self._c.rpc("exhibition_next_queue_number", {"p_exhibition_id": exhibition_id}).execute()
        data = res.data
        if isinstance(data, list):
            data = data[0] if data else None
        if isinstance(data, dict):
            data = next(iter(data.values()), None)
        if data is None:
            raise RuntimeError(f"exhibition queue number allocation failed: {res}")
        return int(data)

    def list_queue(
        self, exhibition_id: str, display_statuses: list[str], *, limit: int, newest_first: bool = False
    ) -> list[dict[str, Any]]:
        res = (
            self._c.table(TABLE)
            .select(QUEUE_COLUMNS)
            .eq("exhibition_id", exhibition_id)
            .in_("display_status", list(display_statuses))
            .order("queue_number", desc=newest_first)
            .limit(limit)
            .execute()
        )
        return list(res.data or [])


class SupabaseArtifactStore:
    def __init__(self, client, bucket: Optional[str] = None) -> None:
        self._c = client
        self._bucket = bucket or storage_bucket()

    def _check(self, path: str) -> None:
        if not path.startswith(storage_prefix() + "/"):
            raise ValueError(f"exhibition artifacts must live under {storage_prefix()}/: {path}")

    def put(self, path: str, data: bytes, content_type: str) -> None:
        self._check(path)
        self._c.storage.from_(self._bucket).upload(
            path, data, {"content-type": content_type, "upsert": "true"}
        )

    def get(self, path: str) -> bytes:
        self._check(path)
        return self._c.storage.from_(self._bucket).download(path)

    def signed_url(self, path: str, ttl_seconds: int = 3600) -> Optional[str]:
        self._check(path)
        try:
            res = self._c.storage.from_(self._bucket).create_signed_url(path, ttl_seconds)
        except Exception:
            return None
        if isinstance(res, dict):
            for src in (res, res.get("data") or {}):
                if isinstance(src, dict):
                    for k in ("signedURL", "signedUrl", "signed_url", "url"):
                        v = src.get(k)
                        if isinstance(v, str) and v:
                            return v
        return None


# ── 메모리 (테스트·로컬 CLI) ──────────────────────────────────────────────────


class InMemoryRunStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            r = {"attempts": 0, "created_at": _now_iso(), **row}
            r.setdefault("id", str(uuid.uuid4()))
            self.rows[r["id"]] = r
            return dict(r)

    def get(self, run_id: str) -> Optional[dict[str, Any]]:
        r = self.rows.get(run_id)
        return dict(r) if r else None

    def claim_next(self, worker_id: str, *, stale_after_minutes: int) -> Optional[dict[str, Any]]:
        with self._lock:
            for r in sorted(self.rows.values(), key=lambda x: x["created_at"]):
                if r["status"] == STATUS_QUEUED:
                    r.update(status=STATUS_RUNNING, claimed_by=worker_id, claimed_at=_now_iso(),
                             attempts=int(r.get("attempts") or 0) + 1)
                    return dict(r)
        return None

    def update(self, run_id: str, fields: dict[str, Any], *, claimed_by: Optional[str] = None) -> bool:
        with self._lock:
            r = self.rows.get(run_id)
            if not r or (claimed_by is not None and r.get("claimed_by") != claimed_by):
                return False
            r.update(fields)
            return True

    def update_if(self, run_id: str, fields: dict[str, Any], *, match: dict[str, Any]) -> bool:
        with self._lock:
            r = self.rows.get(run_id)
            if not r or any(r.get(col) != val for col, val in match.items()):
                return False
            r.update(fields)
            return True

    def next_queue_number(self, exhibition_id: str) -> int:
        with self._lock:
            n = self.counters.get(exhibition_id, 0) + 1
            self.counters[exhibition_id] = n
            return n

    def list_queue(
        self, exhibition_id: str, display_statuses: list[str], *, limit: int, newest_first: bool = False
    ) -> list[dict[str, Any]]:
        rows = [
            dict(r) for r in self.rows.values()
            if r.get("exhibition_id") == exhibition_id and r.get("display_status") in display_statuses
        ]
        rows.sort(key=lambda r: r.get("queue_number") or 0, reverse=newest_first)
        return rows[:limit]


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, path: str, data: bytes, content_type: str) -> None:
        if not path.startswith(storage_prefix() + "/"):
            raise ValueError(path)
        self.objects[path] = (data, content_type)

    def get(self, path: str) -> bytes:
        return self.objects[path][0]

    def signed_url(self, path: str, ttl_seconds: int = 3600) -> Optional[str]:
        return f"memory://{path}" if path in self.objects else None


# ── 기본 인스턴스 ─────────────────────────────────────────────────────────────


def default_stores() -> tuple[RunStore, ArtifactStore]:
    """Supabase 가 설정돼 있어야 한다 — 없으면 조용히 메모리로 가지 않고 실패한다."""
    from .supabase_assets import get_client

    client = get_client()
    if client is None:
        raise RuntimeError("Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY).")
    return SupabaseRunStore(client), SupabaseArtifactStore(client)


def worker_id_from_env() -> str:
    return os.getenv("EXHIBITION_PREP_WORKER_ID") or f"exhibition-worker-{os.getpid()}"
