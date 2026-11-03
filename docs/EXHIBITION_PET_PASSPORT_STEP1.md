# Pet Passport + Eternal Beam — Step 1

This step adds registration storage and queue-number allocation only. It does
not change the staff UI, photo capture, hologram worker, or SHOW NEXT flow.

## Data boundary

`exhibition_pet_passport_registrations` is separate from
`exhibition_prep_runs`. A registration owns the visitor's pet name, email,
consent record, queue number, and `WAITING_FOR_PHOTO` state. It does not enter
the display queue until a later photo-association step. Hologram processing is
not part of Passport readiness.

The table is RLS-enabled and has no `anon` or `authenticated` table/function
access. The backend uses the service-role client. Public responses contain no
email, raw token, registration id, or photo run id.

## Allocation and retries

The `exhibition_register_pet_passport` security-definer RPC takes the
exhibition lock, checks the token/fingerprint for an idempotent replay, calls
the shared exhibition allocator, and inserts the registration in the same
transaction. The allocator considers the existing counter, current
`exhibition_prep_runs`, and Passport rows. Cross-table triggers prevent a
number collision even if a legacy/manual write races the allocator.

The existing `exhibition_next_queue_number(TEXT)` RPC name remains intact for
staff callers, but now uses the same allocator and lock. The registration
endpoint requires a client-generated URL-safe token of at least 43 characters
(the equivalent of `secrets.token_urlsafe(32)`). Only its SHA-256 hash is
stored. The same token is the idempotency key, so retries return the original
number without consuming another one.

## Safe cutover

`exhibition_queue_source_config.mode` defaults to `STAFF` when no row exists:

1. Apply the migration to a non-production environment and verify the counter
   backfill against existing `exhibition_prep_runs`.
2. Keep the Passport API feature flag off while validating the migration.
3. Set an exhibition to `DUAL` and enable `ENABLE_EXHIBITION_PASSPORT=1` for a
   controlled pilot. Both flows use the same counter and cannot allocate the
   same number.
4. After the future staff photo flow can accept an existing Passport number,
   set the exhibition to `PASSPORT`. The legacy staff allocation RPC then
   fails closed with `STAFF_QUEUE_ALLOCATION_CLOSED`; the legacy staff path is
   retained for rollback by changing the mode back to `DUAL` or `STAFF`.

No production migration, deployment, secret, or mode change is performed in
this step.
