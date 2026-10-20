# Exhibition Staff local preview

This preview is opt-in and uses the existing in-memory Exhibition stores. It
does not use the Supabase Exhibition or Passport stores, even when the local
`.env.local` contains production Supabase credentials.

Start it from PowerShell in the repository root:

```powershell
$env:ENABLE_EXHIBITION_LOCAL_PREVIEW = "1"
$env:APP_ENV = "local"
$env:SHAKER_OPS_USER_IDS = "<canonical-authorized-user-id>"
npm.cmd run video-api
```

Leave `ENABLE_EXHIBITION_PREP` unset. Do not start
`backend.workers.exhibition_prep_worker`.

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

The local preview mounts only the staff queue routes. Prep, device, Passport
registration, and Passport photo-association routes are not mounted. A
Passport-number submission is rejected rather than falling back to the legacy
allocator. If the preview flag is present in a production environment, the
preview is not mounted and both Exhibition store constructors fail closed.

To stop the preview, press `Ctrl+C`. To clear the PowerShell-only settings:

```powershell
Remove-Item Env:ENABLE_EXHIBITION_LOCAL_PREVIEW -ErrorAction SilentlyContinue
Remove-Item Env:APP_ENV -ErrorAction SilentlyContinue
```

Because the repository `.env.local` still points at the live Supabase project,
use this process only for the explicitly mounted Exhibition preview routes.
Do not enable registration or run unrelated write-capable local features.
