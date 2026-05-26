# Pet 40-Scenario Luma Batch Pipeline

## Overview

One consumer photo → **10 places × 4 actions = 40** Luma Image-to-Video jobs, submitted in parallel. Completion is handled via **Luma webhook**; videos are stored in Supabase Storage as:

`{user_id}/{pet_id}/{THEME_KEY}_{ACTION}.mp4`  
Example: `user123/pet-uuid/SNOW_FOREST_IDLE.mp4`

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/pet/generate-all` | Start batch (multipart `file` or form `image_url`) |
| POST | `/api/v1/pet/luma-webhook` | Luma callback (do not call manually) |
| GET | `/api/v1/pet/batch/{batch_id}` | Progress |

## Environment

```env
LUMA_API_KEY=luma-...
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_STORAGE_BUCKET=user-assets
PUBLIC_API_BASE_URL=https://your-api.onrender.com
```

Optional:

- `LUMA_MOCK=1` — skip real Luma calls (dev)
- `MOCK_LUMA_VIDEO_URL` — fake completed video for mock IDs
- `PET_BATCH_USE_SUPABASE=0` — in-memory DB only

## SQL

Run `docs/supabase_pet_scenarios.sql` in Supabase for persistent batch state across workers.

## Example

```bash
curl -X POST "http://localhost:8000/api/v1/pet/generate-all" \
  -F "file=@pet.jpg" \
  -F "user_id=demo-user" \
  -F "pet_id=my-pet-1"
```

```bash
curl "http://localhost:8000/api/v1/pet/batch/{batch_id}"
```

## Code layout

- `backend/scenarios/pet_scenarios.py` — PLACES, ACTIONS
- `backend/services/prompt_factory.py` — prompt assembly
- `backend/services/luma_batch_service.py` — `asyncio.gather` submit
- `backend/services/pet_generation_store.py` — state + Storage upload
- `backend/routers/pet_v1.py` — HTTP routes
