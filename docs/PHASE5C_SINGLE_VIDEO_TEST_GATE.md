# Phase 5C test-only single video gate

The `PHASE6_SINGLE_PAID_VIDEO_TEST=1` switch is a test-process control. It is
not enabled by default and does not change normal Eternal Beam generation.

When enabled, the process must also provide exactly one matching value in the
explicit test-pet and both allowlist settings:

```text
PHASE6_SINGLE_PAID_VIDEO_TEST_PET_ID=pet_<synthetic-test-id>
BUSINESS_QA_USER_TEST_MODE=allowlist
BUSINESS_QA_USER_TEST_PET_IDS=pet_<synthetic-test-id>
PHASE6_LIVE_MODE=allowlist
PHASE6_LIVE_ALLOWLIST=pet_<synthetic-test-id>
```

Missing, malformed, or mismatched values fail closed. The generation worker
readiness check also rejects the configuration before claiming test work.

The durable provider-job layer reserves one run-level motion slot before the
first video provider submission. The reservation is persisted in
`pet_generation_provider_jobs` using a fixed test-gate provider key. The
existing table uniqueness constraint makes the reservation safe across
workers; the in-memory mock store uses a lock. A second candidate, provider
fallback, duplicate click, or retry cannot call `submit()` after the slot is
consumed. Existing external job IDs continue to be polled, and ambiguous
submission states remain recovery-required without automatic resubmission.

This gate covers only `MOTION_VIDEO` provider submissions. It does not cap
paid image work used earlier in the pipeline:

- canonical image generation;
- action/keyframe image generation;
- image-provider fallbacks and their candidate limits.

Those image calls require separate provider selection, candidate limits, and
account spending controls in the dedicated test environment. Do not enable
this switch against production Supabase or a production worker.
