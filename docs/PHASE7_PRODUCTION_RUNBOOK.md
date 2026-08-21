# PHASE 7 — Production Subscription Runbook

Status as of 2026-08-19. Covers deployment, config, and the go/no-go checklist for a
first real provider generation.

---

## 1. Migrations

Apply **in this order**. All are idempotent (`if not exists` / `or replace`).

| # | File | Why |
|---|---|---|
| 1 | `supabase/migrations/20260721000200_hybrid_business_wallet.sql` | `user_wallets`, `generated_motions`, `motion_generation_jobs`, `credit_generation_sessions` + wallet RPCs |
| 2 | `supabase/migrations/20260818000000_premium_purchases.sql` | Credit-era purchase ledger (still read for legacy refunds) |
| 3 | `supabase/migrations/20260818010000_user_identity_links.sql` | Supabase `sub` → Eternal Beam identity |
| 4 | **`supabase/migrations/20260819000000_behavior_preferences.sql`** | Behavior ON/OFF (Phase 5) |
| 5 | **`supabase/migrations/20260819000100_subscription_schema.sql`** | `user_subscriptions`, `subscription_webhook_events`, 2 RPCs |
| 6 | **`supabase/migrations/20260819000200_canonicalise_subscription_identity.sql`** | Backfill: lowercase email identities |

**#5 is new and was a latent production blocker.** The subscription tables lived only in
`docs/supabase_subscription.sql` and were never in the migration pipeline. Since Phase 2,
`premium_entitlement` reads them to authorize generation — without the tables, every
premium generation fails closed with 503 and `/assets` reports `entitled: false`.

**#6 is a data backfill.** It only rewrites rows with no lowercase conflict; conflicting
rows are left untouched. After running, verify nothing remains:

```sql
select user_id from public.user_subscriptions
 where user_id like '%@%' and user_id <> lower(user_id);
```

Non-empty means one user has both mixed- and lower-case records — decide manually which
subscription is authoritative.

---

## 2. Environment variables

### Required — service refuses to work without them (fail closed)

| Variable | Missing behavior |
|---|---|
| `SUPABASE_JWT_SECRET` | All premium API → **503** `AUTH_NOT_CONFIGURED` |
| `SUBSCRIPTION_WEBHOOK_SECRET` | Real store webhooks → **503** `WEBHOOK_NOT_CONFIGURED`; subscription state never updates |
| `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` | Entitlement/preference lookups → **503** |
| `PUBLIC_API_BASE_URL` | Provider callbacks unreachable; generations never complete |

### Must be OFF in production

| Variable | Risk if on |
|---|---|
| `SUBSCRIPTION_MOCK` | Any logged-in user can activate their own subscription |
| `PAYMENT_MOCK` | Credits granted without receipt verification |
| `ALLOW_INSECURE_TEST_AUTH` | `Bearer test:<user_id>` impersonates anyone |
| `ENABLE_DEV_PREMIUM_TRIGGER` | Unauthenticated, unmetered generation route |
| `VITE_IAP_MOCK` | **Defaults ON** (`!== "0"`) — must be explicitly `0` |
| `VITE_SUBSCRIPTION_MOCK` | **Defaults ON** — must be explicitly `0` |

Both `VITE_*` flags are **fail-open by default**. Set them explicitly to `0`.

### Cost control

| Variable | Meaning |
|---|---|
| `GENERATION_MOCK` | **New.** `1` = no provider call from any provider. `0` = real calls, real cost |
| `PREMIUM_REQUIRES_SUBSCRIPTION` | Default `1`. `0` reverts premium to credit charging |
| `VIDEO_PROVIDER` | `luma` (default) / `wan`. Independent of `GENERATION_MOCK` |

### Verify after deploy

```
GET /readiness   → {"production_ready": true, "blockers": [], ...}
```

Separate from `/health` (liveness) on purpose: a config gap should not trigger restart
loops. Contains no secret values, only whether each is set.

---

## 3. Subscription purchase flow — implementation status

| Capability | Status |
|---|---|
| Webhook ingestion (`INITIAL_BUY` / `RENEWAL` / `CANCEL` / `EXPIRATION`) | **Implemented** |
| Idempotency (event fingerprint), per-user locking | **Implemented** |
| Entitlement storage + cancel-grace | **Implemented** |
| Status read (`GET /v1/subscription/status`, authenticated) | **Implemented** |
| Mock lifecycle driver (`SubscriptionTestPanel`) | **Implemented** (dev only) |
| **Start Membership (real purchase)** | ❌ **NOT IMPLEMENTED** |
| **Cancel Membership (in-app)** | ❌ **NOT IMPLEMENTED** — store-managed only |
| **Restore purchases** | ❌ **NOT IMPLEMENTED** |
| **Subscription receipt verification** | ❌ **NOT IMPLEMENTED** |

### Why Start/Cancel/Restore are not implemented

There is **no client purchase surface**. The app is a Vite/React web app; no StoreKit,
Google Play Billing, RevenueCat, or IAP bridge exists anywhere in the repo. The
"Start Membership" CTA navigates to Settings and shows status — it initiates no purchase.

`iap_verification_service.verify_store_receipt()` handles **one-time products** (credit
packs) via Apple `/verifyReceipt` and a Google verify URL. It does **not** handle
subscriptions, which need `latest_receipt_info` (Apple) or
`purchases.subscriptions.get` (Google).

**To ship real subscriptions, one of:**
1. Wrap the web app natively (Capacitor + IAP plugin), or
2. Use a subscription platform (RevenueCat/Stripe Billing) and point webhooks here, or
3. Web billing (Stripe Checkout) — keys already present but unwired for subscriptions.

Until then the system can *honor* subscriptions but cannot *sell* them.

---

## 4. Webhook security — status and blocker

**Current:** shared-secret header `X-Subscription-Webhook-Secret`, compared with
`hmac.compare_digest`. Missing config → 503. Mock events require a user token and derive
identity from it. Omitting `store_type` cannot bypass the store path.

This is a genuine control (secret + non-public URL), but it is **not** cryptographic
verification of store authenticity.

**Native signature verification is a production blocker, not implemented:**

- **Apple ASSN v2** — `signedPayload` is JWS with an `x5c` chain. Correct verification
  requires validating the chain to Apple's Root CA G3 (bundled), checking the leaf, then
  verifying the signature. PyJWT + `cryptography` are available, but PyJWT does **not**
  perform x5c chain validation — it must be hand-written. A partial version (decoding
  without chain validation) is **worse than the shared secret**: it looks like
  verification while accepting forged payloads.
- **Google RTDN** — Pub/Sub push carries an OIDC token verified via `google-auth`, which
  is **not installed** and not in `requirements-eternal-beam.txt`.

Deliberately not implemented rather than half-implemented. Until then, treat the webhook
URL as a secret and rotate `SUBSCRIPTION_WEBHOOK_SECRET` on any suspected leak.

---

## 5. Generation cost safety — six layers verified

All confirmed by `backend/tests/test_generation_cost_safety.py` (21 tests).

| Layer | Mechanism |
|---|---|
| Provider-neutral kill switch | `GENERATION_MOCK` checked in `submit_generation` **before** provider dispatch |
| Canonical reuse | `asset_state()` — READY never resubmitted |
| Active-job reuse | In-flight actions excluded from `missing` |
| Per-pet concurrency | `MAX_CONCURRENT_GENERATIONS_PER_PET = 2`, enforced even for explicit picks |
| Subscription gate | Expired → 402, zero submissions |
| Submission receipt | Logged before DB write; recoverable by `external_id` |

**Defect found and fixed:** mocking was per-provider. `LUMA_MOCK` guarded only Luma;
`wan_service` had **no mock at all**. Setting `VIDEO_PROVIDER=wan` — or a typo — would
issue real fal.ai calls with no kill switch. `GENERATION_MOCK` is now provider-neutral.

---

## 6. One controlled real generation — checklist

Run top to bottom. Stop on any ✗.

```
PRE-FLIGHT
[ ] Migrations 1–6 applied; backfill verification query returns 0 rows
[ ] GET /readiness → production_ready: true
[ ] SUBSCRIPTION_MOCK=0, PAYMENT_MOCK=0, ALLOW_INSECURE_TEST_AUTH unset
[ ] ENABLE_DEV_PREMIUM_TRIGGER unset  (dev route absent)
[ ] PUBLIC_API_BASE_URL set and reachable from the provider
[ ] VIDEO_PROVIDER explicitly set (do not rely on default)
[ ] Provider key present for THAT provider only (LUMA_API_KEY xor FAL_KEY)
[ ] Provider account spend limit / budget alert configured

SUBJECT
[ ] One test pet, one account you control
[ ] Subscription ACTIVE for that account (via webhook w/ real secret)
[ ] Exactly ONE behavior in MISSING; other four READY or intentionally untouched
[ ] pet_image_url is a reachable https URL (not data:)

EXECUTE
[ ] Set GENERATION_MOCK=0   ← the only gate; flip last, revert first
[ ] Click Generate on exactly ONE behavior in the Behavior Library
[ ] Confirm exactly ONE "SUBMISSION RECEIPT" log line
[ ] Confirm provider dashboard shows exactly ONE job

VERIFY
[ ] Webhook lands; candidate saved → validated → promoted
[ ] Behavior shows READY; ON/OFF appears
[ ] Pressing Generate again submits nothing (canonical reuse)
[ ] Playback: behavior becomes scheduler-eligible

ROLLBACK
[ ] Set GENERATION_MOCK=1 immediately after
[ ] Record actual provider cost against expectation
```

**Do not** run this with more than one behavior MISSING: the queue admits up to 2
concurrent jobs per pet, so two could submit.
