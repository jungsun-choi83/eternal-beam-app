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
  premium_v1,
  device_v1,
  payment_v1,
  subscription_v1,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 보안·비용 설정을 부팅 시 한 번 점검해 로그에 남긴다. **부팅을 막지는 않는다** —
    # 프로세스를 죽이면 헬스체크 롤백 루프에 빠지고, 설정 하나 때문에 이미 동작하던
    # 무료 기능까지 멈춘다. 실제 차단은 요청 시점의 fail-closed 가 이미 한다.
    try:
        from .services.production_readiness import log_audit

        log_audit()
    except Exception:  # noqa: BLE001 — 감사 실패가 부팅을 막아선 안 된다
        logging.getLogger(__name__).exception("프로덕션 설정 감사 실패")

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
# 프로덕션 프리미엄 구매/생성. **항상 마운트된다** — dev_premium 과 달리 env 게이트가
# 없고, 대신 모든 요청이 검증된 토큰과 소유권 검사를 통과해야 한다.
app.include_router(premium_v1.router, prefix="/api", tags=["pet-premium"])
app.include_router(device_v1.router, prefix="/api", tags=["device-v1"])
app.include_router(payment_v1.router, prefix="/api", tags=["payment-v1"])
app.include_router(subscription_v1.router, prefix="/api", tags=["subscription-v1"])

# 웹 정기결제 (Toss = 제공자 #1). 자격 코어는 건드리지 않고 정규화된 이벤트만 보낸다.
from .routers import billing_v1  # noqa: E402

app.include_router(billing_v1.router, prefix="/api", tags=["billing-v1"])

# QR Shaker (Phase 10). 공개 재생 1개 + 소유자 관리 3개.
# **항상 마운트된다** — 공개 경로는 인증 대신 공유 토큰으로 인가하고, 관리 경로는
# 예전 프리미엄 라우터와 같은 검증된 토큰을 요구한다. 생성 경로는 import 조차 없다.
from .routers import shaker_v1  # noqa: E402

app.include_router(shaker_v1.router, prefix="/api", tags=["shaker-v1"])

# 판매자/운영 Shaker 도구 (물리 제품용 QR 생성). 항상 마운트되지만 모든 경로가
# SHAKER_OPS_USER_IDS allowlist 를 요구한다 — 미설정이면 전원 403 (fail closed).
from .routers import shaker_ops_v1  # noqa: E402

app.include_router(shaker_ops_v1.router, prefix="/api", tags=["shaker-ops"])

# 유료 테마 스토어 (Phase 11). 테마 소유권은 구독 자격과 **완전히 별개**이며,
# 이 라우터는 구독도 크레딧도 생성도 건드리지 않는다.
from .routers import theme_store_v1  # noqa: E402

app.include_router(theme_store_v1.router, prefix="/api", tags=["theme-store"])

# 물리 제품 주문 (Phase 12). 구독·테마·크레딧과 별개인 네 번째 축이며,
# 편지도 펫도 Shaker 공유도 **새로 만들지 않는다** — 기존 것을 가리킨다.
from .routers import orders_v1  # noqa: E402

app.include_router(orders_v1.router, prefix="/api", tags=["physical-orders"])

# 인쇄 생산 파이프라인 (Phase 13). 판매자/운영 전용이며 Phase 10 과 같은
# allowlist 를 쓴다. 편지·펫·Shaker 공유를 새로 만들지 않고, 결제된 주문만 받는다.
from .routers import production_ops_v1  # noqa: E402

app.include_router(production_ops_v1.router, prefix="/api", tags=["production-ops"])

# canonical 펫 레지스트리 (Phase 13.2). 생성 로직을 건드리지 않고, 앱이 파이프라인
# 완료 후 결과를 등록한다 — BREATHING 만 있는 무료 펫도 운영이 발견할 수 있다.
from .routers import pet_registry_v1  # noqa: E402

app.include_router(pet_registry_v1.router, prefix="/api", tags=["pet-registry"])

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


@app.get("/readiness")
@app.get("/api/readiness")
def readiness():
    """
    배포 파이프라인·운영자용 설정 감사.

    /health 와 분리한 이유: /health 는 "프로세스가 살아 있는가"이고 오케스트레이터가
    재시작 판단에 쓴다. 설정이 빠졌다고 재시작해 봐야 같은 상태다. 이쪽은
    "실 사용자를 받아도 되는가"를 답한다 — 사람이 배포 후 확인하는 용도다.

    비밀값은 **절대 싣지 않는다**. 설정 여부와 사람이 읽을 진단만 돌려준다.
    """
    from .services.production_readiness import audit

    return audit().as_dict()


@app.get("/current_slot")
@app.get("/api/current_slot")
def current_slot():
    """Unity GameManager 등에서 호출. 현재 슬롯 번호 반환."""
    return {"slot": 0}
