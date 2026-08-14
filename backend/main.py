"""
Eternal Beam - 영상 처리 API (서버 사이드 렌더링)
- 자동 누끼: rembg + Alpha Matting (머리카락 한 올까지)
- FFmpeg 합성: 테마 MP4 + 피사체 오버레이 + Soft Glow + Breathing
- 결제 Gate: payment_status 확인 후 합성
- NFC payload 생성
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# cwd와 무관하게 프로젝트 루트 .env를 먼저 읽음 (npm run video-api 시 cwd=루트)
load_dotenv(os.path.join(_root, ".env"))
for _p in (
    os.path.join(_root, "env.local"),
    os.path.join(_root, ".env.local"),
):
    if os.path.isfile(_p):
        load_dotenv(_p, override=True)
# 폴더 .env.local 안의 env.local (사용자가 폴더로 만든 경우)
_env_in_folder = os.path.join(_root, ".env.local", "env.local")
if os.path.isfile(_env_in_folder):
    load_dotenv(_env_in_folder, override=True)


def _merge_dotenv_nonempty(path: str) -> None:
    """빈 값은 기존 환경 변수를 덮어쓰지 않음 (빈 SUPABASE_URL= 로 인한 getaddrinfo 방지)."""
    if not os.path.isfile(path):
        return
    try:
        from dotenv import dotenv_values
    except ImportError:
        load_dotenv(path, override=True)
        return
    for key, val in dotenv_values(path).items():
        if val is None:
            continue
        s = str(val).strip().strip('"').strip("'")
        if s == "":
            continue
        os.environ[key] = s


# backend/env.local — AnySign 등으로 루트 .env를 못 쓸 때. 빈 줄은 루트 값 유지.
_backend_dir = os.path.dirname(os.path.abspath(__file__))
for _p in (
    os.path.join(_backend_dir, "env.local"),
    os.path.join(_backend_dir, ".env.local"),
    os.path.join(_backend_dir, ".env"),
):
    _merge_dotenv_nonempty(_p)

import logging

from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

from .routers import (
  cutout,
  matting,
  compose,
  assets,
  preview,
  content,
  pet_v1,
  device_v1,
  payment_v1,
  subscription_v1,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("PET_HYBRID_SEED", "1").strip().lower() in ("1", "true", "yes"):
        from .services.wallet_service import seed_dummy_wallets

        seed_dummy_wallets()

    # 누끼 모델 프리로드 — 첫 요청 지연 제거용(선택).
    # 기본 OFF: 메모리 작은 무료 인스턴스에서 부팅/헬스체크를 막지 않도록.
    # 켤 때도 백그라운드 스레드로 돌려 /health 응답을 막지 않음.
    if os.getenv("CUTOUT_PRELOAD_MODEL", "0").strip().lower() in ("1", "true", "yes"):
        import threading

        def _preload() -> None:
            try:
                from .services.cutout_service import preload_default_model

                preload_default_model(
                    os.getenv("CUTOUT_PRELOAD_MODEL_NAME", "isnet-general-use")
                )
            except Exception:
                pass

        threading.Thread(target=_preload, daemon=True).start()

    # ViTMatte 모델 프리로드 — 기본 OFF (torch/transformers 로드 비용이 큼).
    if os.getenv("VITMATTE_PRELOAD_MODEL", "0").strip().lower() in ("1", "true", "yes"):
        import threading

        def _preload_vitmatte() -> None:
            try:
                from .services.vitmatte_service import preload_vitmatte_model

                preload_vitmatte_model()
            except Exception:
                pass

        threading.Thread(target=_preload_vitmatte, daemon=True).start()

    yield

app = FastAPI(
    title="Eternal Beam Video API",
    description="AI 누끼 + FFmpeg 합성 (SSR)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    처리되지 않은 예외를 JSON 500으로 변환해 CORSMiddleware가 헤더를 붙이게 한다.
    Starlette ServerErrorMiddleware의 순수 텍스트 500은 CORS 헤더 없이 나가
    브라우저에서 불투명한 CORS 오류로 보이는 문제를 방지한다.
    """
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error": str(exc) or exc.__class__.__name__,
            "path": request.url.path,
        },
    )


app.include_router(cutout.router, prefix="/api", tags=["cutout"])
app.include_router(matting.router, prefix="/api", tags=["matting"])
app.include_router(compose.router, prefix="/api", tags=["compose"])
app.include_router(assets.router, prefix="/api", tags=["assets"])
app.include_router(preview.router, prefix="/api", tags=["preview"])
app.include_router(content.router, prefix="/api", tags=["content"])
app.include_router(pet_v1.router, prefix="/api", tags=["pet-v1"])
app.include_router(device_v1.router, prefix="/api", tags=["device-v1"])
app.include_router(payment_v1.router, prefix="/api", tags=["payment-v1"])
app.include_router(subscription_v1.router, prefix="/api", tags=["subscription-v1"])

# Optional heavy pipeline endpoints (Luma/generate). Disable by default on lightweight deployments.
_enable_generate = os.getenv("ENABLE_GENERATE_API", "0").strip().lower() in ("1", "true", "yes")
if _enable_generate:
    from .routers import generate
    app.include_router(generate.router, prefix="/api", tags=["generate"])

# 개발 전용 프리미엄 액션 트리거(COME_CLOSER). 기본 꺼짐 —
# 켜지 않으면 라우트 자체가 앱에 존재하지 않는다.
if os.getenv("ENABLE_DEV_PREMIUM_TRIGGER", "0").strip().lower() in ("1", "true", "yes"):
    from .routers import dev_premium
    app.include_router(dev_premium.router, prefix="/api", tags=["pet-dev"])

# 프리뷰 출력 디렉토리 (main.py와 같은 위치 기준)
_output_dir = os.path.join(os.path.dirname(__file__), "..", "outputs")
if not os.path.exists(_output_dir):
    os.makedirs(_output_dir, exist_ok=True)
os.environ.setdefault("PREVIEW_OUTPUT_DIR", _output_dir)

# 정적 파일 서빙 (프리뷰 출력)
from fastapi.staticfiles import StaticFiles
_output_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "outputs"))
if os.path.exists(_output_path):
    app.mount("/outputs", StaticFiles(directory=_output_path), name="outputs")


@app.get("/")
def root():
    """헬스/루트 프로브용(일부 호스팅은 GET / 로 검사)."""
    return {"service": "eternal-beam-api", "health": "/health", "docs": "/docs"}


@app.get("/health")
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/current_slot")
@app.get("/api/current_slot")
def current_slot():
    """Unity GameManager 등에서 호출. 현재 슬롯 번호 반환."""
    return {"slot": 0}
