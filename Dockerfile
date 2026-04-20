# Eternal Beam — FastAPI 누끼·합성 API (Render / Railway / Fly 등)
# 빌드: docker build -t eternal-beam-api .
# 실행: docker run -p 8000:8000 --env-file .env eternal-beam-api

FROM python:3.11-bookworm-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Render/클라우드: torch 없는 requirements-render.txt (빌드·실행 안정)
COPY backend/requirements-render.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY backend /app/backend

ENV PYTHONPATH=/app
# Render 등은 PORT를 주입함 — 없으면 8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
