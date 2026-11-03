# Exhibition Staff local preview

This preview is opt-in and uses the existing in-memory Exhibition stores. It
does not use the Supabase Exhibition or Passport stores, even when the local
`.env.local` contains production Supabase credentials.

The complete local preview is started from PowerShell in the repository root:

```powershell
$env:SHAKER_OPS_USER_IDS = "<canonical-authorized-user-id>"
npm.cmd run exhibition:preview
```

Leave `ENABLE_EXHIBITION_PREP` unset. Do not start
`backend.workers.exhibition_prep_worker`.

In a second terminal, start the frontend with process-only preview flags:

```powershell
$env:VITE_ENABLE_EXHIBITION_PASSPORT = "1"
$env:VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW = "1"
$env:VITE_API_BASE_URL = "http://127.0.0.1:8000"
npm.cmd run dev
```

Open the existing frontend at:

```text
http://localhost:5174/exhibition/staff
```

The backend listens on `http://localhost:8000`. Existing Ops authentication is
still required; the preview does not enable an auth bypass. Queue and SHOW NEXT
state exist only in the backend process and are cleared when it stops.

The preview keeps Supabase JWT/JWKS verification active, but forces
`user_identity_links` resolution and linking into the existing in-memory
identity store. It does not read or write that table in the configured Supabase
project.

`SHAKER_OPS_USER_IDS` must contain the already-authorized account's canonical
Eternal Beam user ID, comma-separated. Use the value from the authorized
production identity/operations source; do not guess it from a display name or
use a test bypass. A verified email normally resolves to its lowercase email
unless an existing identity link maps the account to another ID; otherwise the
Supabase subject is used.

The local preview mounts the in-memory staff queue and Passport routes. Passport
registration, photo association, retrieval, and download data all remain in
the same process-local run, artifact, and Passport stores. A Passport-number
submission is handled by the local Passport store and never falls back to the
legacy allocator. If the preview flag is present in a production environment,
the preview is not mounted and all Exhibition/Passport store constructors fail
closed.

Open `/exhibition/passport` to register, then `/exhibition/staff` to enter the
same number and upload a photo. Local Passport IDs are marked `LOCAL PREVIEW
ID`. The preview worker is intentionally not started; Passport retrieval shows
the uploaded original photo immediately after staff association.

All preview registrations and photos are process-local and are cleared when the
backend restarts. Keep the backend running while testing the Passport refresh
and download flow.

To stop the preview, press `Ctrl+C`. To clear the PowerShell-only settings:

```powershell
Remove-Item Env:ENABLE_EXHIBITION_LOCAL_PREVIEW -ErrorAction SilentlyContinue
Remove-Item Env:APP_ENV -ErrorAction SilentlyContinue
```

Because the repository `.env.local` still points at the live Supabase project,
use this process only for the explicitly mounted Exhibition preview routes.
Do not enable production Passport registration or run unrelated write-capable
local features.
