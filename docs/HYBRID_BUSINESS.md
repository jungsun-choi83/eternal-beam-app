# Hybrid Business API (NFC Place + Subscription Credits)

## Rules

| Rule | Detail |
|------|--------|
| Place | Physical NFC card → 1 of 10 fixed backgrounds |
| Motions | Subscription credits; **4 credits** = 1 place × 4 actions (IDLE, TOUCH, VOICE, NFC) |
| Device | Unity calls sync when card is inserted |

## Endpoints

### `POST /api/v1/pet/generate-with-credit`

```json
{
  "user_id": "demo-user",
  "pet_image_url": "https://.../cutout.png",
  "selected_place_id": "snow_forest",
  "pet_id": "optional-pet-id"
}
```

- **403** if credits &lt; 4: `크레딧이 부족합니다. 구독 플랜을 업그레이드하세요.`
- Deducts 4 credits immediately
- Submits 4 Luma jobs (max 3 concurrent)
- Refunds 4 credits if all 4 submissions fail

### `GET /api/v1/device/sync?user_id=&place_id=&pet_id=`

Returns:

```json
{
  "user_id": "demo-user",
  "pet_id": "demo-user_pet",
  "place_id": "snow_forest",
  "motions": [
    { "action_id": "IDLE", "video_url": "https://..." },
    ...
  ]
}
```

- **404** if any of the 4 actions is missing → app shows “코인으로 영혼 충전” popup

### Webhook

`POST /api/v1/pet/luma-webhook` — shared with 40-scenario batch; credit jobs are handled first.

## Dev

```bash
# demo-user has 12 credits (seed)
curl -X POST http://localhost:8000/api/v1/pet/generate-with-credit \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo-user","pet_image_url":"https://example.com/pet.jpg","selected_place_id":"1"}'
```

```bash
curl "http://localhost:8000/api/v1/device/sync?user_id=demo-user&place_id=snow_forest"
```

## SQL

Run `docs/supabase_hybrid_business.sql`.

## Env

```env
CREDIT_COST_PER_PLACE=4
LUMA_CREDIT_CONCURRENCY=3
HYBRID_USE_SUPABASE=1
PET_HYBRID_SEED=1
PUBLIC_API_BASE_URL=https://eternal-beam-video-api.onrender.com
```
