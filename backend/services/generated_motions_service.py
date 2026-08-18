"""
GeneratedMotions + 크레딧 세션(Luma 4건) 저장소.

웹훅 완료 시 `generated_motions` upsert.
Unity `/device/sync` 는 완료된 4액션 세트만 반환.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Optional

from ..models.hybrid_business import (
  DeviceMotionItem,
  GeneratedMotion,
  MotionJobRow,
  MotionJobStatus,
  SessionStatus,
)
from ..scenarios.pet_scenarios import (
  ACTION_ORDER,
  PLACES,
  PREMIUM_ACTIONS,
  THEME_INDEPENDENT_PLACE_ID,
  is_theme_independent_action,
  place_public_id,
  storage_object_name,
  to_place_id,
)
from . import supabase_assets

_MOCK_MOTIONS: dict[str, GeneratedMotion] = {}  # key: user::pet::place::action
_MOCK_JOBS: dict[str, MotionJobRow] = {}  # luma_generation_id -> job
_MOCK_SESSIONS: dict[str, dict[str, Any]] = {}  # session_id -> meta
_LUMA_INDEX: dict[str, str] = {}  # luma_id -> job internal key


def _motions_table() -> str:
  return os.getenv("GENERATED_MOTIONS_TABLE", "generated_motions")


def _jobs_table() -> str:
  return os.getenv("MOTION_GENERATION_JOBS_TABLE", "motion_generation_jobs")


def _supabase():
  from ..models.content import _supabase_client

  return _supabase_client()


def _use_db() -> bool:
  return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _motion_key(user_id: str, pet_id: str, place_id: str, action_id: str) -> str:
  return f"{user_id}::{pet_id}::{place_id}::{action_id}"


def default_pet_id(user_id: str, pet_id: Optional[str] = None) -> str:
  return (pet_id or "").strip() or f"{user_id.strip()}_pet"


async def create_credit_session(
  user_id: str,
  pet_id: str,
  place_key: str,
  pet_image_url: str,
  credits_charged: int,
) -> str:
  session_id = str(uuid.uuid4())
  place_id = place_public_id(place_key)
  meta = {
    "session_id": session_id,
    "user_id": user_id,
    "pet_id": pet_id,
    "place_key": place_key,
    "place_id": place_id,
    "pet_image_url": pet_image_url,
    "credits_charged": credits_charged,
    "status": "processing",
    "created_at": datetime.utcnow().isoformat(),
  }
  _MOCK_SESSIONS[session_id] = meta

  if _use_db() and _supabase():
    sb = _supabase()
    sb.table(os.getenv("CREDIT_SESSIONS_TABLE", "credit_generation_sessions")).insert(
      {
        "session_id": session_id,
        "user_id": user_id,
        "pet_id": pet_id,
        "place_key": place_key,
        "place_id": place_id,
        "pet_image_url": pet_image_url,
        "credits_charged": credits_charged,
        "status": "processing",
      }
    ).execute()
  return session_id


async def register_generation_job(
  session_id: str,
  user_id: str,
  pet_id: str,
  place_key: str,
  action_id: str,
  external_id: str,
  *,
  provider: str = "luma",
  provider_model: str | None = None,
  attempt: int = 1,
) -> None:
  """
  제출된 작업을 기록한다.

  external_id 는 프로바이더 외부 ID (luma generation id 또는 fal request_id)이며
  기존 luma_generation_id 컬럼에 그대로 들어간다 — 웹훅 조회 경로가 바뀌지 않는다.
  """
  place_id = place_public_id(place_key)
  row = MotionJobRow(
    session_id=session_id,
    user_id=user_id,
    pet_id=pet_id,
    place_key=place_key,
    action_id=action_id,
    luma_generation_id=external_id,
    provider=provider,
    provider_model=provider_model,
    attempt=attempt,
    status=MotionJobStatus.submitted,
  )
  _LUMA_INDEX[external_id] = external_id
  _MOCK_JOBS[external_id] = row

  if _use_db() and _supabase():
    sb = _supabase()
    sb.table(_jobs_table()).insert(
      {
        "session_id": session_id,
        "user_id": user_id,
        "pet_id": pet_id,
        "place_key": place_key,
        "place_id": place_id,
        "action_id": action_id,
        "luma_generation_id": external_id,
        "provider": provider,
        "provider_model": provider_model,
        "attempt": attempt,
        "status": row.status.value,
      }
    ).execute()


async def register_luma_job(
  session_id: str,
  user_id: str,
  pet_id: str,
  place_key: str,
  action_id: str,
  luma_generation_id: str,
) -> None:
  """하위호환 별칭 — 기존 호출부(luma_batch_service 등)를 위해 남긴다."""
  await register_generation_job(
    session_id, user_id, pet_id, place_key, action_id, luma_generation_id, provider="luma"
  )


async def resolve_luma_job(luma_generation_id: str) -> Optional[MotionJobRow]:
  if luma_generation_id in _MOCK_JOBS:
    return _MOCK_JOBS[luma_generation_id]

  if _use_db() and _supabase():
    sb = _supabase()
    r = (
      sb.table(_jobs_table())
      .select("*")
      .eq("luma_generation_id", luma_generation_id)
      .limit(1)
      .execute()
    )
    if r.data:
      return _job_row_from_dict(r.data[0])
  return None


def _job_row_from_dict(d: dict) -> MotionJobRow:
  """DB 행 → MotionJobRow. place_key 가 비어 있으면 place_id 로 역매핑한다."""
  pk = d.get("place_key") or ""
  if not pk and d.get("place_id"):
    from ..scenarios.pet_scenarios import PLACES

    for key, place in PLACES.items():
      if place["theme_key"] == d["place_id"]:
        pk = key
        break
    if not pk:
      pk = d["place_id"]
  return MotionJobRow(
    session_id=d["session_id"],
    user_id=d["user_id"],
    pet_id=d["pet_id"],
    place_key=pk,
    action_id=d["action_id"],
    luma_generation_id=d.get("luma_generation_id"),
    provider=d.get("provider") or "luma",
    provider_model=d.get("provider_model"),
    status=MotionJobStatus(d.get("status", "pending")),
    video_url=d.get("video_url"),
    error=d.get("error"),
    # 재전송 방어에 반드시 필요한 필드들 — 빠지면 이미 승격된 작업을 다시 처리한다.
    candidate_url=d.get("candidate_url"),
    attempt=int(d.get("attempt") or 1),
    promoted_at=d.get("promoted_at"),
  )


MAX_ACTION_ATTEMPTS = int(os.getenv("MAX_ACTION_ATTEMPTS", "2"))

_TERMINAL_JOB_STATUSES = (
  MotionJobStatus.completed,
  MotionJobStatus.rejected,
  MotionJobStatus.failed,
)


async def find_motion_for_key(
  user_id: str, pet_id: Optional[str], place_key: str, action_id: str
) -> Optional[GeneratedMotion]:
  """
  승격된 canonical 자산 1건.

  테마 독립 액션(COME_CLOSER)은 **place 를 무시하고** 찾는다. 덕분에 두 가지가
  동시에 해결된다:
    * 새 센티널 행(place_id="any")을 찾는다
    * 예전에 장소별로 저장된 행도 그대로 찾는다 → 재생성 없이 재사용(호환성)
  """
  a = action_id.upper()
  if is_theme_independent_action(a):
    motions = await list_motions_for_pet(user_id, pet_id)
    return next((m for m in motions if (m.action_id or "").upper() == a), None)

  motions = await list_motions_for_place(user_id, to_place_id(place_key), pet_id)
  return next((m for m in motions if (m.action_id or "").upper() == a), None)


async def find_active_job_for_key(
  user_id: str, pet_id: Optional[str], place_key: str, action_id: str
) -> Optional[MotionJobRow]:
  """
  같은 키로 **아직 결과가 확정되지 않은** 작업. 있으면 다시 제출하면 안 된다.

  이것이 자동 생성의 서버측 멱등성 근거다. 클라이언트 가드(인플라이트 맵,
  StrictMode 중복 방지)는 편의일 뿐이고, 새로고침·다른 탭·다른 기기까지 막는 건
  여기뿐이다.

  완료/거절/실패(terminal)는 '진행 중'이 아니다 — 실패 후 재시도 정책은
  기존 MAX_ACTION_ATTEMPTS 경로가 그대로 담당한다.
  """
  uid = (user_id or "").strip()
  pid = default_pet_id(uid, pet_id)
  place_id = to_place_id(place_key)
  a = action_id.upper()
  # 테마 독립 액션은 place 를 키에서 뺀다 — 테마를 바꿔 가며 눌러도 재제출 0건.
  ignore_place = is_theme_independent_action(a)
  active = [s.value for s in MotionJobStatus if s not in _TERMINAL_JOB_STATUSES]

  if _use_db() and _supabase():
    q = (
      _supabase()
      .table(_jobs_table())
      .select("*")
      .eq("user_id", uid)
      .eq("pet_id", pid)
      .eq("action_id", a)
      .in_("status", active)
    )
    if not ignore_place:
      q = q.eq("place_id", place_id)
    r = q.limit(1).execute()
    if r.data:
      return _job_row_from_dict(r.data[0])

  for row in _MOCK_JOBS.values():
    if row.status in _TERMINAL_JOB_STATUSES:
      continue
    if row.user_id != uid or row.pet_id != pid:
      continue
    if (row.action_id or "").upper() != a:
      continue
    if ignore_place or to_place_id(row.place_key) == place_id:
      return row
  return None


async def list_active_action_ids_for_pet(
  user_id: str, pet_id: Optional[str]
) -> list[str]:
  """
  이 펫에서 **아직 결과가 확정되지 않은** 작업들의 action_id 목록.

  find_active_job_for_key() 와 같은 "진행 중" 정의를 쓰되, 액션 키가 아니라
  **펫 전체**로 센다. 펫당 동시 생성 수 제한(generation_queue)의 근거가 이것이다 —
  기존 검사는 전부 액션 단위라 액션 사이를 세는 곳이 없었고, 그래서 새 펫이 들어오면
  5건이 한꺼번에 나갔다.

  place 는 보지 않는다: 큐 대상(PREMIUM_ACTIONS)은 전부 테마 독립이다.

  ⚠️ **PREMIUM_ACTIONS 만 센다.** 레거시 4종(IDLE/TOUCH/VOICE/NFC)은 같은 테이블에
  같은 pet_id 로 들어오지만 **다른 파이프라인**(4코인 크레딧 경로)이고, 자기 동시성은
  자기가 관리한다. 그걸 같이 세면 4코인 세트를 만든 펫은 논터미널 작업이 4건이라
  상한(2)을 영구히 넘겨, 프리미엄/아이들 자산이 전부 at-capacity 로 막힌다.
  실제로 그 버그가 났다 — "BREATHING 에서 멈추고 아무것도 안 나온다".

  DB 가 권위다 — 다른 탭·다른 기기·새로고침에서 온 작업도 여기 잡힌다.
  """
  uid = (user_id or "").strip()
  pid = default_pet_id(uid, pet_id)
  active = [s.value for s in MotionJobStatus if s not in _TERMINAL_JOB_STATUSES]
  queued_actions = {a.upper() for a in PREMIUM_ACTIONS}
  found: list[str] = []

  if _use_db() and _supabase():
    r = (
      _supabase()
      .table(_jobs_table())
      .select("action_id")
      .eq("user_id", uid)
      .eq("pet_id", pid)
      .in_("status", active)
      .in_("action_id", sorted(queued_actions))
      .execute()
    )
    for row in r.data or []:
      a = (row.get("action_id") or "").upper()
      if a in queued_actions:
        found.append(a)
    return found

  for row in _MOCK_JOBS.values():
    if row.status in _TERMINAL_JOB_STATUSES:
      continue
    if row.user_id != uid or row.pet_id != pid:
      continue
    a = (row.action_id or "").upper()
    if a in queued_actions:
      found.append(a)
  return found


def _seamless_loop_enabled() -> bool:
  """
  IDLE 후처리(ffmpeg xfade) 는 **프로덕션 플래그가 명시적으로 켜졌을 때만** 돈다.

  예전에는 IDLE 이면 무조건 돌았다. 512MB Render 티어에서 ffmpeg 피크가
  387~952MB 로 측정돼 컨테이너를 OOM 으로 죽일 수 있어 기본 off 다.
  routers/generate.py 의 _pet_video_seamless_loop_enabled() 와 같은 변수를 본다.
  """
  return os.getenv("PET_VIDEO_SEAMLESS_LOOP", "false").strip().lower() in ("1", "true", "yes")


def candidate_object_name(place_key: str, action_id: str, attempt: int, job_id: str) -> str:
  """후보 저장 경로 세그먼트 — 시도마다 다르므로 서로 덮어쓰지 않는다."""
  base = storage_object_name(place_key, action_id)  # {PLACE}_{ACTION}.mp4
  stem = base[:-4] if base.endswith(".mp4") else base
  return f"candidates/{stem}_{attempt}_{job_id}.mp4"


async def save_candidate_motion(job: MotionJobRow, video_url: str) -> tuple[str, bytes]:
  """
  프로바이더 결과를 **후보**로 저장한다. canonical 은 건드리지 않는다.

  Returns: (candidate_url, mp4 bytes)  — 승격 시 바이트를 재사용해 재다운로드를 피한다.
  """
  from .luma_service import download_video

  local_path = await download_video(video_url)
  try:
    with open(local_path, "rb") as f:
      mp4 = f.read()
  finally:
    try:
      os.unlink(local_path)
    except Exception:
      pass

  job_id = (job.luma_generation_id or uuid.uuid4().hex)[:12]
  path = (
    f"{job.user_id}/{job.pet_id}/"
    f"{candidate_object_name(job.place_key, job.action_id, job.attempt, job_id)}"
  )
  candidate_url = await supabase_assets.upload_asset_to_storage(path, mp4, "video/mp4")

  if _use_db() and _supabase():
    _supabase().table(_jobs_table()).update(
      {"candidate_url": candidate_url, "updated_at": datetime.utcnow().isoformat()}
    ).eq("luma_generation_id", job.luma_generation_id).execute()
  if job.luma_generation_id in _MOCK_JOBS:
    _MOCK_JOBS[job.luma_generation_id].candidate_url = candidate_url
  job.candidate_url = candidate_url
  return candidate_url, mp4


def validate_candidate(job: MotionJobRow, mp4: bytes) -> tuple[bool, dict]:
  """
  후보 검증 — **현재는 비차단(diagnostic-only)**.

  드리프트 지표를 계산해 기록만 하고 항상 accepted=True 를 돌려준다.
  액션 드리프트 게이트는 아직 켜지 않는다(보정 전).
  """
  try:
    from .voice_drift_service import gate_enabled, measure_voice_drift

    m = measure_voice_drift(mp4)
    meta = m.to_dict()
    meta["gate_enforced"] = False  # 이 패치에서는 절대 차단하지 않는다
    return True, meta
  except Exception as e:  # 진단 실패가 승격을 막아서는 안 된다
    return True, {"error": f"{type(e).__name__}: {e}"[:200], "gate_enforced": False}


async def promote_candidate(job: MotionJobRow, mp4: bytes) -> GeneratedMotion:
  """검증을 통과한 후보를 canonical 로 승격한다."""
  # 테마 독립 액션은 센티널 place_id 로 접어 넣는다 — unique(user,pet,place,action)
  # 덕에 펫당 정확히 한 행이 되고, 저장 경로에도 장소가 들어가지 않는다.
  place_id = (
    THEME_INDEPENDENT_PLACE_ID
    if is_theme_independent_action(job.action_id)
    else to_place_id(job.place_key)
  )
  storage_path = f"{job.user_id}/{job.pet_id}/{storage_object_name(job.place_key, job.action_id)}"

  if (job.action_id or "").upper() == "IDLE" and _seamless_loop_enabled():
    from .seamless_loop_service import make_seamless_loop_mp4

    mp4, _loop_meta = make_seamless_loop_mp4(mp4)

  stored_url = await supabase_assets.upload_asset_to_storage(storage_path, mp4, "video/mp4")
  return await _record_promoted_motion(job, place_id, stored_url)


async def save_completed_motion(
  job: MotionJobRow,
  video_url: str,
) -> GeneratedMotion:
  """하위호환 진입점 — 후보 저장 → 검증 → 승격을 한 번에 수행한다."""
  _candidate_url, mp4 = await save_candidate_motion(job, video_url)
  accepted, meta = validate_candidate(job, mp4)
  await _record_validation(job, meta)
  if not accepted:
    await mark_job_rejected(job.luma_generation_id or "", meta)
    raise CandidateRejected(job.action_id, meta)
  return await promote_candidate(job, mp4)


class CandidateRejected(RuntimeError):
  def __init__(self, action_id: str, validation: dict):
    self.action_id = action_id
    self.validation = validation
    super().__init__(f"candidate rejected for {action_id}")


async def _record_validation(job: MotionJobRow, meta: dict) -> None:
  if _use_db() and _supabase():
    _supabase().table(_jobs_table()).update({"validation": meta}).eq(
      "luma_generation_id", job.luma_generation_id
    ).execute()
  if job.luma_generation_id in _MOCK_JOBS:
    _MOCK_JOBS[job.luma_generation_id].validation = meta
  job.validation = meta


async def mark_job_rejected(external_id: str, validation: dict) -> None:
  now = datetime.utcnow()
  if external_id in _MOCK_JOBS:
    _MOCK_JOBS[external_id].status = MotionJobStatus.rejected
    _MOCK_JOBS[external_id].validation = validation
  if _use_db() and _supabase():
    _supabase().table(_jobs_table()).update(
      {
        "status": MotionJobStatus.rejected.value,
        "validation": validation,
        "updated_at": now.isoformat(),
      }
    ).eq("luma_generation_id", external_id).execute()


async def _record_promoted_motion(
  job: MotionJobRow, place_id: str, stored_url: str
) -> GeneratedMotion:
  now = datetime.utcnow()
  motion = GeneratedMotion(
    user_id=job.user_id,
    pet_id=job.pet_id,
    place_id=place_id,
    action_id=job.action_id,
    video_url=stored_url,
    created_at=now,
  )

  mk = _motion_key(job.user_id, job.pet_id, place_id, job.action_id)
  _MOCK_MOTIONS[mk] = motion

  if _use_db() and _supabase():
    sb = _supabase()
    sb.table(_motions_table()).upsert(
      {
        "user_id": job.user_id,
        "pet_id": job.pet_id,
        "place_id": place_id,
        "action_id": job.action_id,
        "video_url": stored_url,
        "created_at": now.isoformat(),
      },
      on_conflict="user_id,pet_id,place_id,action_id",
    ).execute()
    sb.table(_jobs_table()).update(
      {
        "status": MotionJobStatus.completed.value,
        "video_url": stored_url,
        "promoted_at": now.isoformat(),
        "updated_at": now.isoformat(),
      }
    ).eq("luma_generation_id", job.luma_generation_id).execute()

  if job.luma_generation_id and job.luma_generation_id in _MOCK_JOBS:
    j = _MOCK_JOBS[job.luma_generation_id]
    j.status = MotionJobStatus.completed
    j.video_url = stored_url
    j.promoted_at = now

  return motion


# ── 세션 상태 재계산 ─────────────────────────────────────────────────────────


async def list_jobs_for_session(session_id: str) -> list[MotionJobRow]:
  if _use_db() and _supabase():
    r = _supabase().table(_jobs_table()).select("*").eq("session_id", session_id).execute()
    out: list[MotionJobRow] = []
    for d in r.data or []:
      out.append(
        MotionJobRow(
          session_id=str(d.get("session_id")),
          user_id=d.get("user_id") or "",
          pet_id=d.get("pet_id") or "",
          place_key=d.get("place_key") or "",
          action_id=d.get("action_id") or "",
          luma_generation_id=d.get("luma_generation_id"),
          provider=d.get("provider") or "luma",
          provider_model=d.get("provider_model"),
          status=MotionJobStatus(d.get("status") or "pending"),
          video_url=d.get("video_url"),
          error=d.get("error"),
          candidate_url=d.get("candidate_url"),
          attempt=int(d.get("attempt") or 1),
          promoted_at=d.get("promoted_at"),
        )
      )
    return out
  return [j for j in _MOCK_JOBS.values() if j.session_id == session_id]


def compute_session_status(jobs: list[MotionJobRow]) -> SessionStatus:
  """
  세션 상태는 작업 행들의 **순수 함수**다 — 웹훅이 재전송돼도 결과가 같다.

  기대 액션 = 이 세션이 실제로 제출한 액션 집합(DEV_ACTION_SUBSET 도 자연스럽게 처리).
  액션이 '종료'라 함은: 승격됐거나, 시도 횟수를 모두 소진했다는 뜻.
  """
  if not jobs:
    return SessionStatus.processing

  expected = {j.action_id for j in jobs}
  promoted = {j.action_id for j in jobs if j.status == MotionJobStatus.completed}
  terminal: set[str] = set(promoted)
  for a in expected:
    if a in terminal:
      continue
    tries = [j for j in jobs if j.action_id == a]
    spent = max((j.attempt for j in tries), default=0)
    if spent >= MAX_ACTION_ATTEMPTS and all(j.status in _TERMINAL_JOB_STATUSES for j in tries):
      terminal.add(a)

  if terminal != expected:
    return SessionStatus.processing
  if promoted == expected:
    return SessionStatus.completed
  if not promoted:
    return SessionStatus.failed
  return SessionStatus.partial


async def get_session(session_id: str) -> Optional[dict[str, Any]]:
  if _use_db() and _supabase():
    r = (
      _supabase()
      .table(os.getenv("CREDIT_SESSIONS_TABLE", "credit_generation_sessions"))
      .select("*")
      .eq("session_id", session_id)
      .limit(1)
      .execute()
    )
    return (r.data or [None])[0]
  return _MOCK_SESSIONS.get(session_id)


async def update_session_status(
  session_id: str,
  status: SessionStatus,
  *,
  finalized: bool = False,
) -> None:
  patch: dict[str, Any] = {"status": status.value}
  if finalized:
    patch["finalized_at"] = datetime.utcnow().isoformat()
  if session_id in _MOCK_SESSIONS:
    _MOCK_SESSIONS[session_id].update(patch)
  if _use_db() and _supabase():
    _supabase().table(
      os.getenv("CREDIT_SESSIONS_TABLE", "credit_generation_sessions")
    ).update(patch).eq("session_id", session_id).execute()


async def mark_session_refunded(session_id: str) -> bool:
  """
  환불 표시를 **한 번만** 성공시킨다. 이미 refunded_at 이 있으면 False.
  웹훅 재전송으로 인한 이중 환불을 막는 유일한 지점.
  """
  sess = await get_session(session_id)
  if not sess or sess.get("refunded_at"):
    return False
  stamp = datetime.utcnow().isoformat()

  if _use_db() and _supabase():
    # ⚠️ 조건부 UPDATE 의 **영향 행 수**로 판정한다.
    #
    # 예전에는 update 를 보내고 무조건 True 를 돌려줬다. 위의 읽기-검사는
    # TOCTOU 라서, 웹훅이 동시에 두 번 배달되면 둘 다 refunded_at=null 을 읽고
    # 둘 다 True 를 받는다 — `.is_("refunded_at","null")` 필터가 쓰기는 한 번만
    # 통과시켜도 **환불은 두 번** 나갔다. 여기가 이중 환불을 막는 유일한 지점이라고
    # 문서에 적혀 있었지만, 실제로는 막지 못하고 있었다.
    r = (
      _supabase()
      .table(os.getenv("CREDIT_SESSIONS_TABLE", "credit_generation_sessions"))
      .update({"refunded_at": stamp})
      .eq("session_id", session_id)
      .is_("refunded_at", "null")
      .execute()
    )
    won = bool(getattr(r, "data", None))
    if won and session_id in _MOCK_SESSIONS:
      _MOCK_SESSIONS[session_id]["refunded_at"] = stamp
    return won

  # 인메모리 경로 — 단일 프로세스라 이 검사-후-설정이 원자적이다.
  if session_id in _MOCK_SESSIONS:
    if _MOCK_SESSIONS[session_id].get("refunded_at"):
      return False
    _MOCK_SESSIONS[session_id]["refunded_at"] = stamp
  return True


async def mark_job_failed(luma_generation_id: str, error: str) -> None:
  if luma_generation_id in _MOCK_JOBS:
    _MOCK_JOBS[luma_generation_id].status = MotionJobStatus.failed
    _MOCK_JOBS[luma_generation_id].error = error

  if _use_db() and _supabase():
    sb = _supabase()
    sb.table(_jobs_table()).update(
      {"status": MotionJobStatus.failed.value, "error": error, "updated_at": datetime.utcnow().isoformat()}
    ).eq("luma_generation_id", luma_generation_id).execute()


async def list_motions_for_place(
  user_id: str,
  place_id: str,
  pet_id: Optional[str] = None,
) -> list[GeneratedMotion]:
  pid = default_pet_id(user_id, pet_id)
  uid = user_id.strip()
  place = place_id.strip()

  if _use_db() and _supabase():
    sb = _supabase()
    r = (
      sb.table(_motions_table())
      .select("*")
      .eq("user_id", uid)
      .eq("pet_id", pid)
      .eq("place_id", place)
      .execute()
    )
    return [
      GeneratedMotion(
        user_id=d["user_id"],
        pet_id=d["pet_id"],
        place_id=d["place_id"],
        action_id=d["action_id"],
        video_url=d["video_url"],
        created_at=d.get("created_at"),
      )
      for d in (r.data or [])
    ]

  # ACTION_ORDER 로 훑지 않는다 — 그러면 COME_CLOSER 같은 프리미엄 액션이 보이지
  # 않는다. DB 경로도 액션 필터 없이 user/pet/place 로만 조회하므로 동작이 일치한다.
  # /device/sync 는 반환값을 다시 ACTION_ORDER 로 걸러 쓰므로 영향이 없다.
  prefix = f"{uid}::{pid}::{place}::"
  return [m for k, m in _MOCK_MOTIONS.items() if k.startswith(prefix)]


async def list_motions_for_pet(
  user_id: str,
  pet_id: Optional[str] = None,
) -> list[GeneratedMotion]:
  """
  place 를 무시하고 이 펫의 모든 모션. 테마 독립 액션 조회에만 쓴다.

  /device/sync 는 여전히 list_motions_for_place 를 쓴다 — 레거시 4종은 장소별
  자산이고 그 전제를 바꾸면 안 된다.
  """
  pid = default_pet_id(user_id, pet_id)
  uid = user_id.strip()

  if _use_db() and _supabase():
    r = (
      _supabase()
      .table(_motions_table())
      .select("*")
      .eq("user_id", uid)
      .eq("pet_id", pid)
      .execute()
    )
    return [
      GeneratedMotion(
        user_id=d["user_id"],
        pet_id=d["pet_id"],
        place_id=d["place_id"],
        action_id=d["action_id"],
        video_url=d["video_url"],
        created_at=d.get("created_at"),
      )
      for d in (r.data or [])
    ]

  prefix = f"{uid}::{pid}::"
  return [m for k, m in _MOCK_MOTIONS.items() if k.startswith(prefix)]


async def get_device_sync_payload(
  user_id: str,
  place_id: str,
  pet_id: Optional[str] = None,
) -> Optional[list[DeviceMotionItem]]:
  """
  4액션 모두 있으면 JSON 배열, 하나라도 없으면 None (→ 404).
  """
  from ..scenarios.pet_scenarios import resolve_place_id

  try:
    place_key = resolve_place_id(place_id)
    public_place = place_public_id(place_key)
  except ValueError:
    public_place = place_id.strip()

  motions = await list_motions_for_place(user_id, public_place, pet_id)
  by_action = {m.action_id: m for m in motions}

  items: list[DeviceMotionItem] = []
  for action in ACTION_ORDER:
    m = by_action.get(action)
    if not m or not (m.video_url or "").strip():
      return None
    created = m.created_at.isoformat() if m.created_at else None
    items.append(
      DeviceMotionItem(action_id=action, video_url=m.video_url, created_at=created)
    )
  return items
