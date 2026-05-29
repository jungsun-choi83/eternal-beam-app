"""
배치·시나리오 상태 저장소.

1) Supabase PostgreSQL (`pet_generation_batches`, `pet_scenario_videos`) — 설정 시
2) 프로세스 메모리 MOCK — Supabase 없을 때 (개발·테스트)
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from ..models.pet_generation import BatchStatus, PetBatchRecord, ScenarioRow, ScenarioStatus
from ..scenarios.pet_scenarios import all_scenario_keys, scenario_count, storage_object_name
from . import supabase_assets

# ── MOCK in-memory (단일 워커 기준; 멀티 워커면 Supabase 사용 권장) ─────────────
_MOCK_BATCHES: dict[str, PetBatchRecord] = {}
_MOCK_GEN_INDEX: dict[str, str] = {}  # luma_generation_id -> batch scenario_key "batch::place::action"


def _use_supabase_db() -> bool:
  return os.getenv("PET_BATCH_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
  from ..models.content import _supabase_client

  return _supabase_client()


def _batch_table() -> str:
  return os.getenv("PET_BATCH_TABLE", "pet_generation_batches")


def _scenario_table() -> str:
  return os.getenv("PET_SCENARIO_TABLE", "pet_scenario_videos")


async def create_batch(
  batch_id: str,
  user_id: str,
  pet_id: str,
  image_url: str,
) -> PetBatchRecord:
  """40개 시나리오 행을 pending 으로 초기화."""
  scenarios: dict[str, ScenarioRow] = {}
  for place_key, action_key in all_scenario_keys():
    sk = f"{place_key}::{action_key}"
    scenarios[sk] = ScenarioRow(
      place_key=place_key,
      action_key=action_key,
      storage_path=f"{user_id}/{pet_id}/{storage_object_name(place_key, action_key)}",
    )

  record = PetBatchRecord(
    batch_id=batch_id,
    user_id=user_id,
    pet_id=pet_id,
    image_url=image_url,
    status=BatchStatus.queued,
    total=scenario_count(),
    scenarios=scenarios,
  )

  if _use_supabase_db() and _supabase():
    sb = _supabase()
    sb.table(_batch_table()).insert(
      {
        "batch_id": batch_id,
        "user_id": user_id,
        "pet_id": pet_id,
        "image_url": image_url,
        "status": record.status.value,
        "total": record.total,
        "completed_count": 0,
        "failed_count": 0,
      }
    ).execute()
    rows = []
    for sk, sc in scenarios.items():
      rows.append(
        {
          "batch_id": batch_id,
          "user_id": user_id,
          "pet_id": pet_id,
          "place_key": sc.place_key,
          "action_key": sc.action_key,
          "status": sc.status.value,
          "storage_path": sc.storage_path,
        }
      )
    sb.table(_scenario_table()).insert(rows).execute()
  else:
    _MOCK_BATCHES[batch_id] = record

  return record


async def register_luma_job(
  batch_id: str,
  place_key: str,
  action_key: str,
  luma_generation_id: str,
) -> None:
  sk = f"{place_key}::{action_key}"
  now = datetime.utcnow()

  if _use_supabase_db() and _supabase():
    sb = _supabase()
    sb.table(_scenario_table()).update(
      {
        "luma_generation_id": luma_generation_id,
        "status": ScenarioStatus.submitted.value,
        "updated_at": now.isoformat(),
      }
    ).eq("batch_id", batch_id).eq("place_key", place_key).eq("action_key", action_key).execute()
    _MOCK_GEN_INDEX[luma_generation_id] = f"{batch_id}::{sk}"
    return

  batch = _MOCK_BATCHES.get(batch_id)
  if not batch:
    return
  row = batch.scenarios.get(sk)
  if row:
    row.luma_generation_id = luma_generation_id
    row.status = ScenarioStatus.submitted
    row.updated_at = now
  _MOCK_GEN_INDEX[luma_generation_id] = f"{batch_id}::{sk}"
  batch.status = BatchStatus.processing


async def resolve_generation_id(luma_generation_id: str) -> Optional[tuple[str, str, str]]:
  """(batch_id, place_key, action_key)"""
  indexed = _MOCK_GEN_INDEX.get(luma_generation_id)
  if indexed:
    batch_id, sk = indexed.split("::", 1)
    place_key, action_key = sk.split("::", 1)
    return batch_id, place_key, action_key

  if _use_supabase_db() and _supabase():
    sb = _supabase()
    r = (
      sb.table(_scenario_table())
      .select("batch_id, place_key, action_key")
      .eq("luma_generation_id", luma_generation_id)
      .limit(1)
      .execute()
    )
    if r.data:
      row = r.data[0]
      return row["batch_id"], row["place_key"], row["action_key"]
  return None


async def get_batch(batch_id: str) -> Optional[PetBatchRecord]:
  if batch_id in _MOCK_BATCHES:
    return _MOCK_BATCHES[batch_id]

  if _use_supabase_db() and _supabase():
    sb = _supabase()
    br = sb.table(_batch_table()).select("*").eq("batch_id", batch_id).limit(1).execute()
    if not br.data:
      return None
    b = br.data[0]
    sr = sb.table(_scenario_table()).select("*").eq("batch_id", batch_id).execute()
    scenarios: dict[str, ScenarioRow] = {}
    for row in sr.data or []:
      sk = f"{row['place_key']}::{row['action_key']}"
      scenarios[sk] = ScenarioRow(
        place_key=row["place_key"],
        action_key=row["action_key"],
        status=ScenarioStatus(row.get("status", "pending")),
        luma_generation_id=row.get("luma_generation_id"),
        storage_path=row.get("storage_path"),
        video_url=row.get("video_url"),
        error=row.get("error"),
      )
    return PetBatchRecord(
      batch_id=b["batch_id"],
      user_id=b["user_id"],
      pet_id=b["pet_id"],
      image_url=b["image_url"],
      status=BatchStatus(b.get("status", "processing")),
      total=int(b.get("total", 40)),
      completed_count=int(b.get("completed_count", 0)),
      failed_count=int(b.get("failed_count", 0)),
      scenarios=scenarios,
    )
  return None


async def update_scenario_from_luma(
  luma_generation_id: str,
  state: str,
  video_url: Optional[str] = None,
  error: Optional[str] = None,
) -> Optional[dict[str, Any]]:
  """
  웹훅에서 호출: 상태 반영. completed + video_url 이면 Storage 업로드까지 수행.
  Returns summary dict for logging/response.
  """
  resolved = await resolve_generation_id(luma_generation_id)
  if not resolved:
    return None

  batch_id, place_key, action_key = resolved
  batch = await get_batch(batch_id)
  if not batch:
    return None

  sk = f"{place_key}::{action_key}"
  row = batch.scenarios.get(sk)
  if not row:
    return None

  # 같은 generation 에 대해 Luma 가 여러 번 POST 할 수 있음 → 중복 카운트 방지
  if row.status in (ScenarioStatus.completed, ScenarioStatus.failed):
    return {
      "batch_id": batch_id,
      "place_key": place_key,
      "action_key": action_key,
      "status": row.status.value,
      "skipped": True,
    }

  now = datetime.utcnow()
  state_l = (state or "").lower()

  if state_l in ("dreaming", "queued", "pending"):
    row.status = ScenarioStatus.dreaming
  elif state_l in ("failed", "error"):
    row.status = ScenarioStatus.failed
    row.error = error or "Luma failed"
    batch.failed_count += 1
  elif state_l == "completed" and video_url:
    row.status = ScenarioStatus.completed
    row.video_url = video_url
    # Supabase Storage: USER_ID/PET_ID/PLACE_ACTION.mp4
    try:
      from .luma_service import download_video

      local_path = await download_video(video_url)
      try:
        with open(local_path, "rb") as f:
          mp4_bytes = f.read()
        if (action_key or "").upper() == "IDLE":
          from .seamless_loop_service import make_seamless_loop_mp4

          mp4_bytes, _loop_meta = make_seamless_loop_mp4(mp4_bytes)
        stored_url = await supabase_assets.upload_asset_to_storage(
          row.storage_path or f"{batch.user_id}/{batch.pet_id}/{place_key}_{action_key}.mp4",
          mp4_bytes,
          "video/mp4",
        )
        row.video_url = stored_url
        await supabase_assets.ensure_user_asset_row(
          batch.user_id,
          batch.pet_id,
          asset_type=f"scenario_{place_key}_{action_key}",
          url=stored_url,
          theme_id=place_key,
        )
      finally:
        try:
          os.unlink(local_path)
        except Exception:
          pass
      batch.completed_count += 1
    except Exception as e:
      row.status = ScenarioStatus.failed
      row.error = str(e)
      batch.failed_count += 1
  else:
    return {"batch_id": batch_id, "ignored": True, "state": state_l}

  # 배치 전체 상태
  done = batch.completed_count + batch.failed_count
  if done >= batch.total:
    if batch.failed_count == 0:
      batch.status = BatchStatus.completed
    elif batch.completed_count == 0:
      batch.status = BatchStatus.failed
    else:
      batch.status = BatchStatus.partial
  else:
    batch.status = BatchStatus.processing

  row.updated_at = now

  if _use_supabase_db() and _supabase():
    sb = _supabase()
    sb.table(_scenario_table()).update(
      {
        "status": row.status.value,
        "video_url": row.video_url,
        "error": row.error,
        "updated_at": now.isoformat(),
      }
    ).eq("batch_id", batch_id).eq("place_key", place_key).eq("action_key", action_key).execute()
    sb.table(_batch_table()).update(
      {
        "status": batch.status.value,
        "completed_count": batch.completed_count,
        "failed_count": batch.failed_count,
      }
    ).eq("batch_id", batch_id).execute()
  else:
    _MOCK_BATCHES[batch_id] = batch

  return {
    "batch_id": batch_id,
    "place_key": place_key,
    "action_key": action_key,
    "status": row.status.value,
    "storage_path": row.storage_path,
    "video_url": row.video_url,
    "batch_status": batch.status.value,
    "completed": batch.completed_count,
    "failed": batch.failed_count,
    "total": batch.total,
  }
