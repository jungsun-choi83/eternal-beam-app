# PHASE 1 — Credit-Based Premium: Freeze & Inventory

Status: **inventory only. Nothing changed. No code deleted, no subscription implemented.**

Date: 2026-08-19 · Branch: `devos`

Purpose: classify every piece of the existing credit/wallet premium implementation so
Phase 2 (monthly subscription + per-motion selection) can be planned without breaking
the legacy IDLE/TOUCH/VOICE/NFC device-pack path.

---

## 0. Freeze statement

The following are **out of scope and untouched** by this phase and by any Phase 2 work
unless explicitly requested: playback, spontaneous scheduler, motion runtime, seam
transitions, priority/preemption, WAN/Luma provider dispatch, generation queue, Pi/Unity,
`ACTION_ORDER`, `/device/sync`.

---

## 1. Two premium systems exist today (this is the key fact)

They share generation infrastructure but have **separate money models**:

| | **Legacy 4-coin device pack** | **Web premium (credits)** |
|---|---|---|
| Unit | 1 place × 4 actions (IDLE/TOUCH/VOICE/NFC) | `IDLE_BUNDLE` (all idle events) / `ACTION:COME_CLOSER` |
| Cost | `CREDIT_COST_PER_PLACE_SET = 4` | 1 credit each |
| Place | theme-bound (`place_key`) | theme-independent (`"any"`) |
| Entry | `POST /api/v1/pet/generate-with-credit` | `POST /api/v1/pet/premium/purchase` |
| Refund | all-or-nothing (1–3/4 → full refund) | only if **zero** assets promoted |
| Consumer | `/api/v1/device/sync` → Pi/Unity (404 unless all 4 exist) | web player / scheduler |
| Auth | none (user_id string) | `require_user` Bearer token |
| Wallet call | `deduct_credits(strict=False)` | `deduct_credits(strict=True)` (atomic RPC) |

**They meet in exactly one place:** `credit_generation_service.handle_luma_webhook_for_credit()`.
That single webhook handler drives both, and inside it `_advance_premium_queue()` branches
on `premium_generation.is_queued_action()`. This is the highest-risk file in the codebase
for Phase 2 — see §5.

**A third system already exists and is mostly overlooked:** a real monthly-subscription
stack (`user_subscriptions`, webhook parser/service, renewal + expiry + cancel-grace RPCs,
`is_entitled()`, and a `/device/sync` 403 gate). Today it exists to gate device sync and to
grant **12 credits/month**. Phase 2's job is largely to keep that machinery and cut the
credit grant out of it — not to build subscriptions from scratch.

---

## 2. Classification A — Reusable under subscription

Keep as-is or with a rename. No business-model assumptions baked in.

### Backend — generation (zero credit logic)
| File / symbol | Why reusable |
|---|---|
| `backend/services/premium_generation.py` (whole file) | `submit_premium_action()` and `advance_generation_queue()` never touch the wallet. Explicitly documented "크레딧 차감 없음". This is exactly the "generate one selected motion" primitive Phase 2 needs. |
| `backend/services/credit_keyframe.py` (whole file) | Misnamed. It is black-plate keyframe prep, no credit logic. Shared by legacy, premium, and dev paths. |
| `is_remote_asset_url()` (in `credit_keyframe`) | Generic `data:` URL rejection guard. |

### Backend — discovery & ownership
| File / symbol | Why reusable |
|---|---|
| `premium_purchase.asset_state()` + `AssetState` | READY / GENERATING / MISSING per action. This *is* the per-motion selection model the new PM direction asks for. |
| `premium_purchase.target_actions()`, `action_kind()`, `resolve_kind()` | Registry-driven action resolution (`IDLE_EVENTS`, `PET_ACTIONS`), no hardcoded counts. Only `KIND_IDLE_BUNDLE` inside them is category B. |
| `premium_purchase.assert_pet_owned()` | Trust-on-first-use pet ownership. Independent of billing. Fail-closed. |
| `GET /api/v1/pet/premium/assets` (`premium_v1`) | Read-only discovery; documented as never generating and never charging. Only the `idle_bundle_credits` / `action_event_credits` response fields are B. |
| `GET /api/v1/pet/premium/identity` (`premium_v1`) | Reconciles legacy local `user_id` with the authoritative token identity. Needed more, not less, under subscription. |
| `backend/auth.py` `require_user` / `AuthedUser` | Real auth. Subscription entitlement will hang off this. |

### Backend — subscription stack (already built)
| File / symbol | Why reusable |
|---|---|
| `backend/services/subscription_store_service.py` | `is_entitled()` (active, or canceled-but-within-period), renewal/status-change via RPC + mock, webhook fingerprint dedupe. |
| `backend/services/subscription_webhook_service.py`, `subscription_webhook_parser.py` | Apple ASSN / Google RTDN / mock normalization. |
| `backend/models/subscription.py`, `backend/routers/subscription_v1.py` | `/plans`, `/status/{user_id}`, `/webhook`. |
| `backend/data/subscription_plans.py` | Plan catalog + store-product-id mapping. **Except** `credits_per_month` → category B. |
| `docs/supabase_subscription.sql` | `user_subscriptions`, `subscription_webhook_events`, `process_subscription_renewal`, `process_subscription_status_change`. |

### Database
| Object | Why reusable |
|---|---|
| `premium_purchases` table + `premium_purchases_active_uniq` partial unique index | Server-authoritative idempotency on `(user_id, pet_id, kind)`. Under subscription this becomes an **entitlement/request ledger** — "has this motion already been requested for this pet" — with `credits_charged` dropping to 0/unused. The migration comment already states it is *not* a playback-permission table. |
| `generated_motions` canonical rows | Already the sole authority for playback access ("잔액이 0이 되어도 재생된다"). Directly satisfies "generated READY assets should be reusable". |

### Frontend
| File / symbol | Why reusable |
|---|---|
| `src/lib/premium-auth-token.ts` | Verbatim. Never fabricates tokens; prod-safe dev bypass. |
| `src/lib/premium-assets.ts` — `discoverPremiumAssets()`, `PremiumAssets`, `PremiumApiError`, `actionKind()` | GET-only discovery + typed error codes. `purchasePremium()` becomes "request generation" (see B). |
| `src/lib/premium-unlock.ts` — `sideFrom()` state machine, `readyIdleEventIds()`, `isComeCloserReady()` | The ready/generating/missing derivation and the scheduler-candidate/double-tap readiness predicates are billing-independent. |
| `src/components/memorial/use-premium-unlock.ts` — the *discipline* | "Discovery is automatic (GET), purchase is manual (POST)", 15s poll only while generating, double-click ref guard, partial-failure no-rollback. Keep the shape; swap the wallet read. |
| `src/lib/subscription-mock.ts` | Mock webhook driver for INITIAL_BUY / RENEWAL / EXPIRATION / CANCEL. |
| `src/lib/come-closer-asset.ts`, `src/components/memorial/use-idle-event-assets.ts` | Asset lookup only; already documented as not creating generations. |

---

## 3. Classification B — Old premium UX / pricing to replace

These encode "credits are the premium business model" or "bundle, not per-motion selection".
Both assumptions are now dead. **Do not delete yet** — several are load-bearing until Phase 2 lands.

### The bundle concept (direct contradiction of new direction)
| File / symbol | Conflict |
|---|---|
| `KIND_IDLE_BUNDLE` — `premium_purchase.py:49`, `premium-assets.ts:26` | "1 credit unlocks **all** registered idle events." New direction: user selects individual motions and only those are generated. Bundle is the wrong granularity. |
| `premium_purchase.target_actions(KIND_IDLE_BUNDLE)` → `tuple(IDLE_EVENTS)` | Fans out to all 4 idle motions — exactly the auto-generate-everything behavior PM ruled out. |
| `premium_purchase._submit_missing()` | Loops over *all* missing actions in the bundle. Needs to become "submit the selected set". |
| `deriveUnlockState()` two-sided model (`idle` + `comeCloser`) in `premium-unlock.ts` | Hardcodes the two-purchase split into UI state. |

### Credit pricing / charging
| File / symbol | Note |
|---|---|
| `premium_purchase.purchase()` (the ~110-line charge flow) | claim → `deduct_credits(strict=True)` → submit → refund-on-submit-failure. Replaced by: entitlement check → submit selected. |
| `premium_purchase.credits_for_kind()`, `IDLE_BUNDLE_CREDITS`, `ACTION_EVENT_CREDITS` envs | Price table. |
| `premium_purchase._claim_purchase/_release_purchase/_mark_purchase_refunded` | Ledger mechanics are reusable (§2) but the *refund* semantics are credit-specific. |
| `premium_purchase.reconcile_after_terminal()` | Terminal-detection logic is reusable; the `refund_credits()` call inside is not. |
| `POST /api/v1/pet/premium/purchase` + `PurchaseResponse.credits_charged` / `credits_remaining` | Endpoint contract is credit-shaped. Successor takes a selected-motion list. |
| `subscription_plans.credits_per_month = 12` and the webhook's "+12 크레딧" on renewal | The single line that ties subscription to credits. Phase 2 replaces the grant with direct entitlement. |

### Frontend UX
| File | Note |
|---|---|
| `src/components/memorial/unlock-features-card.tsx` | Single "Unlock everything for N credits" CTA, balance display, "not enough credits", "get credits" link. Whole component is the old UX. Replace with a per-motion picker. |
| `src/components/memorial/credits-section.tsx` | Settings wallet balance + "add test credits" packs. |
| `src/lib/credit-topup.ts`, `src/lib/credit-packs.ts` | Mock top-up via the IAP path. `TEST_CREDIT_PACKS` must stay in lockstep with `iap_products.py` (enforced by `credit-topup.test.ts`). |
| `credit_pack_test_2` / `credit_pack_test_5` in `backend/data/iap_products.py` | Mock-only test packs feeding the above. (`credit_pack_4` is category C.) |
| `src/lib/credit-session.ts` | Persists `credits_remaining` / `credits_charged` from the legacy generate-with-credit response. Legacy-only. |
| `src/lib/credit-pipeline.ts` | Frontend driver for the legacy 4-set (`runCreditMotionGeneration`, `isInsufficientCreditsError`). Legacy-only. |
| `src/components/memorial/subscription-test-panel.tsx` | Test panel built around the credit-granting webhook. Needs rework alongside the grant change. |
| `memorial-i18n.ts` — `unlock.*` and `credits.*` key groups | All credit-worded copy (cost, balance, notEnough, getCredits, topUpHint). |
| `device_v1.py:17` `_DEVICE_404_DETAIL` — "앱에서 코인을 사용해 충전하세요" | Credit-worded copy inside a category-C file. Copy change only; do not touch the gate. |

---

## 4. Classification C — Required by legacy IDLE/TOUCH/VOICE/NFC device pack

**Do not delete. Do not refactor.** These keep the shipped device product working.

### Backend
| File / symbol | Role |
|---|---|
| `backend/services/credit_generation_service.py` | `generate_with_credit()` (4-coin orchestration), `_maybe_retry_action()`, `_finalize_session_if_terminal()` (all-or-nothing refund matching `/device/sync`'s 4-of-4 contract), **and the shared webhook `handle_luma_webhook_for_credit()`**. |
| `backend/services/credit_luma_batch.py` | `submit_place_motion_set()` over `ACTION_ORDER`, `resubmit_action()`, `credit_cost()`, `Semaphore(3)` rate limiting, `DEV_ACTION_SUBSET`. |
| `backend/services/wallet_service.py` | `deduct_credits(strict=False)` is the legacy path and is explicitly documented as behavior-frozen. `add_credits()` / `refund_credits()` still serve IAP and the 4-coin refunds. |
| `backend/routers/pet_v1.py` — `POST /generate-with-credit`, `GET /wallet/{user_id}` | Legacy entry point + balance read (the latter is also used by the B-category UI). |
| `backend/routers/device_v1.py` | `/device/sync` — subscription 403 gate + 4-of-4 404. Untouchable. |
| `backend/scenarios/pet_scenarios.py` — `ACTION_ORDER`, `CREDIT_COST_PER_PLACE_SET = 4`, `ACTIONS`, `ACTIONS_EN`, `PLACES` | Frozen by explicit instruction and by tests. |
| `backend/services/iap_charge_service.py`, `payment_history_service.py`, `models/payment.py`, `routers/payment_v1.py`, `credit_pack_4` | IAP verify → idempotent charge → `add_credits`. Still the only real top-up path. |
| `backend/services/generation_reconciler.py:120` | Imports `handle_luma_webhook_for_credit` for stuck-job recovery. |

### Database
`user_wallets` · `credit_generation_sessions` · `motion_generation_jobs` · `deduct_wallet_credits()` /
`add_wallet_credits()` RPCs (`supabase/migrations/20260721000200_hybrid_business_wallet.sql`) ·
`docs/supabase_payment_iap.sql` · `docs/supabase_hybrid_business.sql`

### Frontend
`src/app/services/videoProcessingApi.ts` — `generateWithCredit()`, `getWalletBalance()`,
`verifyAndChargeIAP()`, `postSubscriptionWebhook()`, `getSubscriptionStatus()`.

### Dev-only (leave alone)
`backend/routers/dev_premium.py` (`ENABLE_DEV_PREMIUM_TRIGGER=1` only, unmounted in prod;
explicitly never touches the 4-coin pack), `src/lib/idle-event-dev-trigger.ts`,
`src/lib/come-closer-autogen.ts`.

---

## 5. Danger zones for Phase 2

1. **`handle_luma_webhook_for_credit()` is shared by both money models.** Legacy 4-coin
   refunds and premium queue advance both run through it. Any change here can silently
   break device-pack refunds or stall the premium queue. Touch only additively.
2. **`wallet_service.deduct_credits()` has two behaviors in one function** — `strict=True`
   (premium, atomic RPC, fail-closed) and `strict=False` (legacy, optimistic lock with
   in-memory fallback). Removing the premium caller must not disturb the legacy branch.
3. **`_advance_premium_queue()` calls `premium_purchase.reconcile_after_terminal()`.**
   If credits stop being charged but that call still fires, it will attempt refunds on
   zero-credit purchases. Needs an explicit decision, not a silent no-op.
4. **`credit_keyframe.py` is named for credits but is shared infrastructure** used by the
   legacy path, the premium path, and `dev_premium`. Do not delete on a name match.
5. **`premium_purchases` table already carries live rows** with real `credits_charged`
   values. Repurposing it as an entitlement ledger needs a migration decision (reuse with
   `credits_charged = 0`, or new table + backfill), not an in-place semantic flip.
6. **`credit-topup.test.ts` asserts frontend pack constants equal `iap_products.py`.**
   Changing one side alone breaks the suite.

---

## 6. Not classified: dead code found during inventory

`src/app/components/eternal-beam/**` (28 screens incl. `CheckoutScreen`, `UpgradePopup`,
`UsageGauge`, `SettingsScreen`) + `src/app/services/quotaService.ts` +
`src/app/contexts/*` + `supabase/migrations/20250220000000_user_quotas.sql` +
`supabase/functions/reset_monthly_quotas/index.ts`.

Verified unreachable: the live entry is `main.tsx → app/App.tsx → app/EternalBeamApp.tsx`,
which imports **only** from `src/components/memorial/`. Nothing outside that folder imports
the `eternal-beam` screen tree.

Worth noting: `quotaService.ts` is an **older monthly-plan model** (`basic`/`premium`/
`lifetime`, `PLAN_PRICES` monthly ₩14,900/₩29,900, per-month generation quotas, a monthly
quota-reset edge function). Conceptually it is closer to the new PM direction than the
credit system is — but it is dead, unwired, and its plan names/prices conflict with
`subscription_plans.py`'s single ₩9,900 standard plan. Flagging it so Phase 2 doesn't
accidentally revive two competing plan catalogs.

Adjacent but separate money axis (not credits, not in scope): `backend/services/theme_prices.py`
+ PayPal premium-theme purchases (`aurora`/`sunset`/`ocean_deep`/`custom_photo_bg` @ $2.99).

---

## 7. Tests that pin current credit behavior

Changing these means changing a contract. Listed so Phase 2 knows what will go red.

- `test_premium_purchase.py` — purchase flow, idempotency, refund-on-submit-failure
- `test_premium_auth_routes.py` — auth on `/assets` and `/purchase`
- `test_candidate_retry_refund.py` — all-or-nothing legacy refund
- `test_come_closer_action.py`, `test_come_closer_autogen_idempotency.py` — assert `CREDIT_COST_PER_PLACE_SET == 4`
- `test_credit_keyframe_failure.py` — no-fallback keyframe policy
- `test_idle_event_prompts.py`, `test_touch_action.py`, `test_voice_action.py`, `test_nfc_action.py`
- `test_generation_queue.py`, `test_queue_auto_advance.py`, `test_generation_reconciler.py`
- `src/lib/premium-unlock.test.ts`, `src/lib/credit-topup.test.ts`

---

## 8. Open decisions for Phase 2 (not decided here)

1. Does subscription grant **unlimited** motion selection, or a per-period selection cap?
   (`subscription_plans.credits_per_month = 12` currently answers this implicitly with credits.)
2. What happens to READY assets when a subscription **expires**? `generated_motions`
   canonical rows currently mean "playable forever, regardless of balance". Keeping that
   rule means expiry blocks *new* generation only.
3. Is `premium_purchases` reused as the entitlement/request ledger, or replaced?
4. Does the 4-coin device pack eventually fold into the subscription, or stay a separate
   product? Everything in §4 depends on the answer.
5. What is the selection granularity in the API — one motion per request, or a list?
   (Affects whether `KIND_IDLE_BUNDLE` can simply be deleted or needs a compatibility shim.)
