# Archive raw MP4 faststart repair

## Cause confirmed

Archive's authenticated raw-video route resolves the selected candidate from the
generation-run lineage and validates the candidate's `raw_bucket` and
`raw_video_path`. It does not validate or substitute the packed-alpha derived
object.

For application `5a9b60cc-352d-4ab9-b38d-d66143de51b5` and run
`e5ef2ebf-6fa4-4900-b7d6-e9e80b61c04a`, the selected raw object is:

```text
user-assets/archive_5a9b60cc-352d-4ab9-b38d-d66143de51b5/archive_5a9b60cc-352d-4ab9-b38d-d66143de51b5/motions/breathing/v1/wan_3_standard_a1_raw.mp4
```

The read-only inspection found `ftyp → free → mdat → moov`, H.264/yuv420p,
854×480, and four seconds. The late `moov` atom is the precise reason for
`BROWSER_VIDEO_NOT_FASTSTART`.

The raw object is uploaded from the provider's `result.video_bytes` unchanged
in `motion_video_service`. Existing `+faststart` flags are in the packed-alpha
derived-video path, so they do not repair this raw object.

## Controlled repair procedure

1. Re-read the application, generation run, motion version, and selected
   candidate immediately before repair. Require all of the following:
   `COMPLETED`, `PUBLISHED`, `BREATHING`, `FREE_HOME`, matching selected
   candidate/version IDs, `DELIVERED_GENERATED`, and the same private bucket and
   object path above.
2. Download the private raw object to an operator-controlled temporary location
   and record its size/hash. Do not expose the bytes or signed URL.
3. Run `repair_archive_raw_mp4` for that exact bucket and path. It runs:

   ```text
   ffmpeg -y -v error -i source.mp4 -map 0 -c copy -movflags +faststart -f mp4 repaired.mp4
   ```

4. The repaired temporary file must pass ffprobe, MP4/browser compatibility,
   `moov`-before-`mdat`, duration, size, and first/last-frame decode checks.
   Only then may the explicit replacement callback upload it with `upsert=true`
   to the same private object path.
5. Re-read the object and call the existing Archive video endpoint. A repeated
   repair is a validated no-op and does not upload again.

The repair keeps the same storage path, application ID, generation-run ID,
motion version, selected candidate, publication ID, and all database metadata.
No database update is required. The original bytes remain untouched until the
replacement has passed all validation; operators should retain the recorded
local backup/hash until Archive confirms import and preview.

This procedure is operator-controlled and is not run by an Archive GET,
worker, or FastAPI background task. It must not be invoked until the production
repair approval is explicit.

## Forward behavior

`export_archive_browser_mp4` now uses the copy-only remux for a compatible
non-faststart source before publishing its deterministic private browser export.
Incompatible sources still use the existing H.264/yuv420p conversion path.
The raw preview route remains read-only and continues to reject a non-faststart
raw object rather than mutating storage during a GET.
