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

from ..models.hybrid_business import DeviceMotionItem, GeneratedMotion, MotionJobRow, MotionJobStatus
from ..scenarios.pet_scenarios import ACTION_ORDER, PLACES, place_public_id, storage_object_name
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


async def register_luma_job(
  session_id: str,
  user_id: str,
  pet_id: str,
  place_key: str,
  action_id: str,
  luma_generation_id: str,
) -> None:
  place_id = place_public_id(place_key)
  row = MotionJobRow(
    session_id=session_id,
    user_id=user_id,
    pet_id=pet_id,
    place_key=place_key,
    action_id=action_id,
    luma_generation_id=luma_generation_id,
    status=MotionJobStatus.submitted,
  )
  _LUMA_INDEX[luma_generation_id] = luma_generation_id
  _MOCK_JOBS[luma_generation_id] = row

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
        "luma_generation_id": luma_generation_id,
        "status": row.status.value,
      }
    ).execute()


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
      d = r.data[0]
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
        status=MotionJobStatus(d.get("status", "pending")),
        video_url=d.get("video_url"),
        error=d.get("error"),
      )
  return None


async def save_completed_motion(
  job: MotionJobRow,
  video_url: str,
) -> GeneratedMotion:
  place_id = place_public_id(job.place_key) if job.place_key in PLACES else job.place_key
  storage_path = f"{job.user_id}/{job.pet_id}/{storage_object_name(job.place_key, job.action_id)}"

  stored_url = video_url
  if video_url.startswith("http"):
    from .luma_service import download_video

    local_path = await download_video(video_url)
    try:
      with open(local_path, "rb") as f:
        mp4 = f.read()
      if (job.action_id or "").upper() == "IDLE":
        from .seamless_loop_service import make_seamless_loop_mp4

        mp4, _loop_meta = make_seamless_loop_mp4(mp4)
      stored_url = await supabase_assets.upload_asset_to_storage(
        storage_path, mp4, "video/mp4"
      )
    finally:
      try:
        os.unlink(local_path)
      except Exception:
        pass

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
        "updated_at": now.isoformat(),
      }
    ).eq("luma_generation_id", job.luma_generation_id).execute()

  if job.luma_generation_id and job.luma_generation_id in _MOCK_JOBS:
    j = _MOCK_JOBS[job.luma_generation_id]
    j.status = MotionJobStatus.completed
    j.video_url = stored_url

  return motion


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

  out: list[GeneratedMotion] = []
  for action in ACTION_ORDER:
    mk = _motion_key(uid, pid, place, action)
    if mk in _MOCK_MOTIONS:
      out.append(_MOCK_MOTIONS[mk])
  return out


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
