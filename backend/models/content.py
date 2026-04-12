"""
Content 관리 모델 — Content_ID 기반 레이어 시스템
"""

import os
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ContentMetadata(BaseModel):
    """콘텐츠 메타데이터"""

    background_id: str
    subject_id: str
    scale: float = 1.0
    position_x: int = 0
    position_y: int = 0
    user_id: str
    created_at: Optional[datetime] = None


def _supabase_client():
    from supabase import create_client

    url = os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if url and key:
        return create_client(url, key)
    return None


class ContentDB:
    """Supabase contents 테이블 연동"""

    @staticmethod
    def create_content(metadata: ContentMetadata) -> str:
        """
        새 콘텐츠 생성 (contents 테이블)

        Returns:
            content_id (UUID)
        """
        supabase = _supabase_client()
        if not supabase:
            raise RuntimeError("Supabase가 설정되지 않았습니다.")

        content_id = str(uuid.uuid4())
        row = {
            "content_id": content_id,
            "user_id": metadata.user_id,
            "background_theme_id": metadata.background_id,
            "subject_id": metadata.subject_id,
            "scale": metadata.scale,
            "position_x": metadata.position_x,
            "position_y": metadata.position_y,
            "created_at": (metadata.created_at or datetime.utcnow()).isoformat(),
        }
        supabase.table("contents").upsert(row, on_conflict="content_id").execute()
        return content_id

    @staticmethod
    def get_content(content_id: str) -> Optional[ContentMetadata]:
        """
        Content_ID로 메타데이터 조회
        """
        supabase = _supabase_client()
        if not supabase:
            return None

        r = (
            supabase.table("contents")
            .select("content_id, background_theme_id, subject_id, scale, position_x, position_y, user_id, created_at")
            .eq("content_id", content_id)
            .limit(1)
            .execute()
        )
        if not r.data or len(r.data) == 0:
            return None
        row = r.data[0]
        return ContentMetadata(
            background_id=row.get("background_theme_id") or row.get("background_id", ""),
            subject_id=row.get("subject_id", ""),
            scale=float(row.get("scale", 1.0)),
            position_x=int(row.get("position_x", 0)),
            position_y=int(row.get("position_y", 0)),
            user_id=row.get("user_id", ""),
            created_at=row.get("created_at"),
        )
