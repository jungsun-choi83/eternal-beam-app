import os
from typing import Optional

from dotenv import load_dotenv
from supabase import Client, create_client


load_dotenv()


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")


def get_supabase() -> Client:
  """
  Supabase 클라이언트 생성.
  환경 변수 설정이 안 돼 있으면 명확한 에러를 던진다.
  """
  if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
      "Supabase 설정이 없습니다. .env 에 SUPABASE_URL 과 "
      "SUPABASE_SERVICE_ROLE_KEY (또는 SUPABASE_ANON_KEY)를 넣어주세요."
    )
  return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_video_url_for_slot(slot_number: int) -> Optional[str]:
  """
  슬롯 번호 → background video_url 조회.
  매핑 또는 콘텐츠가 없으면 None 을 반환한다.
  """
  supabase = get_supabase()

  # 1) 슬롯 매핑 조회
  mapping_res = (
    supabase.table("slot_content_mapping")
    .select("slot_number, content_id")
    .eq("slot_number", slot_number)
    .limit(1)
    .execute()
  )

  mappings = mapping_res.data or []
  if not mappings:
    return None

  content_id = mappings[0]["content_id"]

  # 2) contents 에서 video_url 조회
  content_res = (
    supabase.table("contents")
    .select("content_id, video_url")
    .eq("content_id", content_id)
    .limit(1)
    .execute()
  )

  contents = content_res.data or []
  if not contents:
    return None

  video_url = contents[0].get("video_url")
  return video_url or None


if __name__ == "__main__":
  # 간단한 수동 테스트용
  slot = int(os.getenv("TEST_SLOT_NUMBER", "1"))
  print("테스트 슬롯:", slot)
  print("video_url:", get_video_url_for_slot(slot))

