# Archive asynchronous preparation contract

Archive photo intake still uses the same service-token header, application ID,
multipart fields, validation rules, private storage, and `201` response. The
change is that the intake request now acknowledges durable preparation instead
of waiting for YOLO/SAM2/ViTMatte.

After the upload is persisted, the response is normally:

```json
{
  "application_id": "application-123",
  "status": "PREPARING",
  "generation_run_id": null
}
```

Poll the existing status endpoint with the same service token. It now exposes
safe preparation fields:

```json
{
  "status": "PREPARING",
  "preparation_stage": "CUTOUT_PREPARATION",
  "preparation_progress": 10,
  "preparation_attempt": 1,
  "preparation_error_code": null
}
```

The existing `GENERATION_QUEUED`, `GENERATING`, and `COMPLETED` statuses remain
valid. Once preparation succeeds, `generation_run_id` becomes available and
the existing generation polling and authenticated raw-MP4 retrieval contract
continues unchanged. On retryable preparation failure, the status is
`GENERATION_FAILED` and `preparation_error_code` contains only a safe machine
code. A normal intake resend or an unfiltered worker pass does not retry that
failure; an authorized operator recovery must explicitly requeue it.

## Operator recovery and Archive reconciliation

Operator recovery is a separate control-plane action and requires the
`X-Archive-Operator-Token` header configured by `ARCHIVE_OPERATOR_TOKEN`. It
does not accept the normal Archive service token and does not create a
generation run. The operator action atomically requeues an eligible failed or
expired preparation attempt through the durable queue:

```text
POST /api/internal/archive-intake/{application_id}/preparation-recovery
X-Archive-Operator-Token: ...
```

The response contains `recovery_outcome` (`QUEUED`, `ALREADY_QUEUED`,
`ALREADY_IN_PROGRESS`, or `GENERATION_ALREADY_EXISTS`) plus the same
authoritative status and preparation fields exposed by the status endpoint.
Archive should reconcile `VERIFY_REQUIRED` by polling
`GET /api/internal/archive-intake/{application_id}/status` with its normal
service token and treating EB's `status`, `generation_run_id`, and safe
preparation fields as authoritative. It must not create a new application ID
or idempotency key during reconciliation.

The Archive client must therefore stop treating a `201` response with a
missing `generation_run_id` as a failed upload. It should persist the
`application_id`, begin polling status, and wait for the generation run ID
before using generation-run-specific endpoints.

The preparation worker is a separate durable process:

```text
python -m backend.workers.archive_intake_preparation_worker
```

It requires `ARCHIVE_PREPARATION_WORKER_ENABLED=1`. The database migration
`20261108000000_archive_intake_preparation_worker.sql` must be applied before
enabling it in a deployed environment.

For controlled PowerShell testing of one explicitly allowed application:

```powershell
$env:ARCHIVE_PREPARATION_WORKER_ENABLED = "1"
$env:ARCHIVE_PREPARATION_TEST_APPLICATION_ID = "5a9b60cc-352d-4ab9-b38d-d66143de51b5"
$env:ARCHIVE_PREPARATION_WORKER_ID = "archive-single-application-test"
python -m backend.workers.archive_intake_preparation_worker
```

The restriction is enforced by the Supabase claim RPC and the worker refuses
to process a returned row whose application ID does not match.
