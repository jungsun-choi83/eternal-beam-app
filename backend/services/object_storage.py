"""
객체 스토리지 업로드 (Supabase / AWS S3 / Firebase Storage)
환경변수로 백엔드 선택: STORAGE_BACKEND=supabase|s3|firebase|auto (기본 auto: 설정된 순서로 시도)
"""

from __future__ import annotations

import os
import uuid
from typing import Optional


def _guess_content_type(path: str) -> str:
    p = path.lower()
    if p.endswith(".png"):
        return "image/png"
    if p.endswith(".jpg") or p.endswith(".jpeg"):
        return "image/jpeg"
    if p.endswith(".mp4"):
        return "video/mp4"
    return "application/octet-stream"


async def upload_bytes(
    object_path: str,
    data: bytes,
    content_type: str,
    backend: Optional[str] = None,
) -> str:
    """
    바이트 업로드 후 공개·서명 URL 반환.
    """
    b = (backend or os.getenv("STORAGE_BACKEND", "auto")).lower().strip()

    if b == "supabase":
        from .supabase_assets import upload_asset_to_storage

        return await upload_asset_to_storage(object_path, data, content_type)

    if b == "s3":
        return await _upload_s3(object_path, data, content_type)

    if b == "firebase":
        return await _upload_firebase(object_path, data, content_type)

    # auto
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        from .supabase_assets import upload_asset_to_storage

        return await upload_asset_to_storage(object_path, data, content_type)

    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY") and os.getenv("S3_BUCKET"):
        return await _upload_s3(object_path, data, content_type)

    if os.getenv("FIREBASE_STORAGE_BUCKET"):
        return await _upload_firebase(object_path, data, content_type)

    raise RuntimeError(
        "스토리지가 설정되지 않았습니다. "
        "Supabase(SUPABASE_URL+SUPABASE_SERVICE_ROLE_KEY) 또는 "
        "S3(AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET) 또는 "
        "Firebase(FIREBASE_STORAGE_BUCKET + 자격증명)를 설정하세요."
    )


async def upload_input_image_for_luma(
    data: bytes,
    user_id: str,
    content_id: str,
    ext: str = ".png",
) -> str:
    """Luma에 넘길 공개 URL용 입력 이미지 업로드."""
    oid = f"{user_id}/{content_id}_input{ext}"
    ct = "image/png" if ext.lower().endswith("png") else "image/jpeg"
    return await upload_bytes(oid, data, ct)


async def _upload_s3(object_path: str, data: bytes, content_type: str) -> str:
    try:
        import boto3  # type: ignore
    except ImportError as e:
        raise RuntimeError("S3 업로드에 boto3가 필요합니다: pip install boto3") from e

    bucket = os.environ["S3_BUCKET"]
    region = os.getenv("AWS_REGION", "ap-northeast-2")
    def _put():
        client = boto3.client(
            "s3",
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            region_name=region,
        )
        put_kw = {
            "Bucket": bucket,
            "Key": object_path,
            "Body": data,
            "ContentType": content_type,
        }
        if os.getenv("S3_PUBLIC_ACL", "1") == "1":
            put_kw["ACL"] = "public-read"
        client.put_object(**put_kw)
        base = os.getenv("S3_PUBLIC_BASE_URL", "").rstrip("/")
        if base:
            return f"{base}/{object_path}"
        return f"https://{bucket}.s3.{region}.amazonaws.com/{object_path}"

    import asyncio

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _put)


async def _upload_firebase(object_path: str, data: bytes, content_type: str) -> str:
    try:
        import firebase_admin  # type: ignore
        from firebase_admin import credentials, storage as fb_storage
    except ImportError as e:
        raise RuntimeError("Firebase 업로드에 firebase-admin이 필요합니다: pip install firebase-admin") from e

    bucket_name = os.environ["FIREBASE_STORAGE_BUCKET"]
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("FIREBASE_CREDENTIALS_JSON")

    def _put():
        if not firebase_admin._apps:
            if cred_path and os.path.isfile(cred_path):
                cred = credentials.Certificate(cred_path)
            else:
                cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred, {"storageBucket": bucket_name})

        bucket = fb_storage.bucket(bucket_name)
        blob = bucket.blob(object_path)
        blob.upload_from_string(data, content_type=content_type)
        # 공개 URL (버킷 규칙이 공개 읽기일 때)
        try:
            blob.make_public()
        except Exception:
            pass
        return blob.public_url

    import asyncio

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _put)


def new_content_id() -> str:
    return f"content_{uuid.uuid4().hex[:12]}"
