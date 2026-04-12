"""

Modal 원격 호출 - GPU 누끼 처리

환경변수: MODAL_TOKEN_ID, MODAL_TOKEN_SECRET (또는 ~/.modal.toml)



주의: is_modal_available()은 ~/.modal.toml만 있어도 True인데,

예전 구현은 process_video_via_modal()이 MODAL_TOKEN_* 없으면 즉시 None을 반환해

CLI로 로그인한 경우에도 호출이 막혔음 → 인증 조건을 통일함.

"""



import os

from pathlib import Path

from typing import Optional





def _modal_auth_configured() -> bool:

    """Modal Python SDK가 쓸 수 있는 자격이 있는지 (env 또는 ~/.modal.toml)."""

    if os.getenv("MODAL_TOKEN_ID") or os.getenv("MODAL_TOKEN_SECRET"):

        return True

    toml = Path.home() / ".modal.toml"

    return toml.is_file()





async def process_video_via_modal(

    video_bytes: bytes,

    replace_bg: Optional[str] = "white",

    max_frames: int = 120,

    use_alpha_matting: bool = False,

) -> Optional[bytes]:

    """

    Modal GPU에서 rembg 누끼 처리 후 RGBA MP4 bytes 반환.

    자격 없으면 None (로컬 처리로 폴백).

    """

    if not _modal_auth_configured():

        return None



    try:

        import modal

    except ImportError:

        return None



    try:

        fn = modal.Function.from_name("eternal-beam-cutout", "process_video_to_rgba_modal")

        import asyncio



        result = await asyncio.to_thread(

            fn.remote,

            video_bytes=video_bytes,

            replace_bg=replace_bg,

            max_frames=max_frames,

            use_alpha_matting=use_alpha_matting,

        )

        return result

    except Exception as e:

        raise RuntimeError(f"Modal GPU 처리 실패: {e}") from e



async def process_dog_only_video_via_modal(

    video_bytes: bytes,

    replace_bg: Optional[str] = "white",

    max_frames: int = 120,

    use_alpha_matting: bool = False,

) -> Optional[dict]:

    """

    Modal GPU에서 YOLO+rembg 강아지 전용 누끼.

    성공 시 {"rgb_mp4": bytes, "alpha_mp4": bytes}, 자격 없으면 None.

    """

    if not _modal_auth_configured():

        return None



    try:

        import modal

    except ImportError:

        return None



    try:

        fn = modal.Function.from_name("eternal-beam-cutout", "process_dog_only_video_modal")

        import asyncio

        result = await asyncio.to_thread(

            fn.remote,

            video_bytes=video_bytes,

            replace_bg=replace_bg,

            max_frames=max_frames,

            use_alpha_matting=use_alpha_matting,

        )

        return result

    except Exception as e:

        raise RuntimeError(f"Modal 강아지 전용 처리 실패: {e}") from e





def is_modal_available() -> bool:

    return _modal_auth_configured()


