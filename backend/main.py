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
load_dotenv()
for _p in (
    os.path.join(_root, "env.local"),
    os.path.join(_root, ".env.local"),
):
    if os.path.isfile(_p):
        load_dotenv(_p)
# 폴더 .env.local 안의 env.local (사용자가 폴더로 만든 경우)
_env_in_folder = os.path.join(_root, ".env.local", "env.local")
if os.path.isfile(_env_in_folder):
    load_dotenv(_env_in_folder)

# backend/env.local — use when AnySign4PC locks ".env"; plain text, same KEY=value format
_backend_dir = os.path.dirname(os.path.abspath(__file__))
for _p in (
    os.path.join(_backend_dir, "env.local"),
    os.path.join(_backend_dir, ".env.local"),
    os.path.join(_backend_dir, ".env"),
):
    if os.path.isfile(_p):
        load_dotenv(_p, override=True)

from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware

from .routers import cutout, compose, assets, preview, content, generate

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: rembg 모델 로드 등
    yield
    # shutdown
    pass

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

app.include_router(cutout.router, prefix="/api", tags=["cutout"])
app.include_router(compose.router, prefix="/api", tags=["compose"])
app.include_router(assets.router, prefix="/api", tags=["assets"])
app.include_router(preview.router, prefix="/api", tags=["preview"])
app.include_router(content.router, prefix="/api", tags=["content"])
app.include_router(generate.router, prefix="/api", tags=["generate"])

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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/current_slot")
@app.get("/api/current_slot")
def current_slot():
    """Unity GameManager 등에서 호출. 현재 슬롯 번호 반환."""
    return {"slot": 0}
