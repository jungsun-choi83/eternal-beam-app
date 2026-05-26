"""Pydantic models for 40-scenario pet batch pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ScenarioStatus(str, Enum):
  pending = "pending"
  submitted = "submitted"
  dreaming = "dreaming"
  completed = "completed"
  failed = "failed"


class BatchStatus(str, Enum):
  queued = "queued"
  processing = "processing"
  completed = "completed"
  partial = "partial"
  failed = "failed"


class ScenarioRow(BaseModel):
  place_key: str
  action_key: str
  status: ScenarioStatus = ScenarioStatus.pending
  luma_generation_id: Optional[str] = None
  storage_path: Optional[str] = None
  video_url: Optional[str] = None
  error: Optional[str] = None
  updated_at: Optional[datetime] = None


class PetBatchRecord(BaseModel):
  batch_id: str
  user_id: str
  pet_id: str
  image_url: str
  status: BatchStatus = BatchStatus.queued
  total: int = 40
  completed_count: int = 0
  failed_count: int = 0
  scenarios: dict[str, ScenarioRow] = Field(default_factory=dict)
  created_at: datetime = Field(default_factory=datetime.utcnow)

  def scenario_key(self, place_key: str, action_key: str) -> str:
    return f"{place_key}::{action_key}"


class GenerateAllResponse(BaseModel):
  batch_id: str
  pet_id: str
  user_id: str
  image_url: str
  total_scenarios: int
  submitted: int
  submit_errors: list[dict[str, Any]]
  status: str
  webhook_path: str


class LumaWebhookPayload(BaseModel):
  """Luma POST body (subset — 전체 JSON도 허용)."""

  id: Optional[str] = None
  state: Optional[str] = None
  failure_reason: Optional[str] = None
  assets: Optional[dict[str, Any]] = None

  class Config:
    extra = "allow"
