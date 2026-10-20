import assert from "node:assert/strict";
import test from "node:test";

import {
  preparePassportPetPhotoSubmission,
  submitPassportPetPhoto,
  staffPhotoSubmissionMode,
  validatePassportQueueNumber,
} from "./exhibition-queue.ts";

test("Passport queue number validation accepts only positive safe integers", () => {
  assert.deepEqual(validatePassportQueueNumber(""), { value: null, error: "required" });
  assert.deepEqual(validatePassportQueueNumber(" 42 "), { value: 42, error: null });
  for (const invalid of ["0", "-1", "1.5", "abc", String(Number.MAX_SAFE_INTEGER + 1)]) {
    assert.deepEqual(validatePassportQueueNumber(invalid), { value: null, error: "invalid" }, invalid);
  }
});

test("Passport payload includes the visitor number and cannot use legacy mode", () => {
  const form = preparePassportPetPhotoSubmission({
    file: new File(["photo"], "momo.jpg", { type: "image/jpeg" }),
    petName: " Momo ",
    exhibitionId: "expo-1",
    queueNumber: 42,
  });
  assert.equal(form.get("queue_number"), "42");
  assert.equal(form.get("pet_name"), "Momo");
  assert.equal(form.get("exhibition_id"), "expo-1");
  assert.equal((form.get("file") as File).name, "momo.jpg");
  assert.equal(staffPhotoSubmissionMode(""), "LEGACY_STAFF");
  assert.equal(staffPhotoSubmissionMode("42"), "PASSPORT");
});

test("Passport API client posts the explicit queue-number payload", async () => {
  let requestedUrl = "";
  const requestedForms: FormData[] = [];
  const result = await submitPassportPetPhoto(
    "ops-token",
    {
      file: new Blob(["photo"], { type: "image/jpeg" }),
      petName: "Coco",
      exhibitionId: "expo-1",
      queueNumber: 7,
    },
    async (input, init) => {
      requestedUrl = String(input);
      requestedForms.push(init?.body as FormData);
      return new Response(
        JSON.stringify({
          run_id: "run-7",
          exhibition_id: "expo-1",
          queue_number: 7,
          pet_name: "Coco",
          created_at: null,
          display_status: "WAITING",
          processing_status: "QUEUED",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }
  );

  assert.equal(requestedUrl, "/api/exhibition/staff/submissions");
  assert.equal(requestedForms.length, 1);
  const requestedForm = requestedForms[0];
  assert.ok(requestedForm);
  assert.equal(requestedForm.get("queue_number"), "7");
  assert.equal(result.queue_number, 7);
});
